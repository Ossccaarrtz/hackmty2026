"""
Acciones verificadas del agente. Estas son las UNICAS puertas de entrada para
tocar dinero real -- tanto advance-day como el chat con Gemini llaman aqui.
Ninguna, sea disparada por un checkpoint programado o por un mensaje de chat,
ejecuta nada sin volver a verificar contra el estado real en DynamoDB/Nessie.
El LLM del chat nunca decide montos ni ejecuta directo: solo puede invocar
estas funciones, y estas funciones son las que deciden si procede o no.
"""
import time
import boto3
from datetime import date
from decimal import Decimal
from boto3.dynamodb.conditions import Key

from signal_engine import compute_signals, score_liquidity, compute_elapsed_days, LIQUIDITY_WARNING_DAYS
from nessie_actions import stop_recurring_bill, sweep_to_savings, release_from_savings

REGION = "us-east-1"
TABLE_NAME = "jarbis-financiero-data"
CHECKING_ID = "3cbe83c6-e844-48b3-b86a-627b8a6e3028"
SAVINGS_ID = "f9428a58-dbc4-49b3-9105-e69460a56a9a"
MAX_AUTONOMOUS_SAVINGS = 100  # tope por transaccion Y por dia -- el chat no puede mover mas que esto solo

ENVELOPE_PREFIX = "ENVELOPE#"
INCOME_PATTERN_SK = "INCOME_PATTERN"
PENDING_ALLOCATION_SK = "PENDING_ALLOCATION"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)


def to_decimal(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_decimal(v) for v in obj]
    return obj


def load_data(user_id):
    resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id))
    items = resp["Items"]
    deposits = [i for i in items if i.get("type") == "deposit"]
    purchases = [i for i in items if i.get("type") == "purchase"]
    bills = [i for i in items if i.get("type") == "bill"]
    bills_plain = [{"payee": b["payee"], "status": b["status"], "payment_amount": float(b["payment_amount"]), "bill_id": b["bill_id"]} for b in bills]
    return deposits, purchases, bills_plain


def get_score_history(user_id):
    """Historial real de scores persistidos -- usado para trend/proyeccion
    en vez de numeros inventados."""
    resp = table.query(
        KeyConditionExpression=Key("user_id").eq(user_id) & Key("sk").begins_with("SCORE#")
    )
    points = [{"date": i["sk"].split("#")[1], "value": int(i["value"])} for i in resp["Items"]]
    points.sort(key=lambda p: p["date"])
    return points


def record_score(user_id, as_of_date, value):
    """Guarda un punto de historial real. Se llama despues de cada calculo
    de signals -- asi trend/proyeccion se basan en datos reales, no en
    literales inventados como antes."""
    today = as_of_date or date.today().isoformat()
    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"SCORE#{today}", "type": "score_point",
        "date": today, "value": value,
    }))


def get_full_signals(deposits, purchases, bills_plain, user_id=None, as_of_date=None, persist=True):
    # as_of_date=None significaba "no evalues anomalia ni gastos proximos"
    # (compute_signals depende de una fecha de referencia real para eso) --
    # asi que TODA llamada fuera de un checkpoint de advance-day (chat,
    # verified_move_to_savings, verified_release_buffer, /signals normal)
    # tenia el guardrail de anomalia permanentemente apagado sin que nadie
    # lo notara. Default a hoy real cuando no se especifica un checkpoint.
    as_of_date = as_of_date or date.today().isoformat()
    total_income = sum(float(d["amount"]) for d in deposits)
    total_expense = sum(float(p["amount"]) for p in purchases)
    history = get_score_history(user_id) if user_id else []
    signals = compute_signals(
        deposits, purchases, bills_plain,
        total_income=total_income, total_expense=total_expense,
        as_of_date=as_of_date, score_history=history,
    )
    if persist and user_id:
        record_score(user_id, as_of_date, signals["score"]["value"])
    return signals


def get_current_signals(user_id="mia"):
    deposits, purchases, bills_plain = load_data(user_id)
    return get_full_signals(deposits, purchases, bills_plain, user_id=user_id)


def _savings_pool_available(purchases):
    """Cuanto se ha acumulado en el 'ahorro' de la simulacion menos lo ya
    liberado -- no tenemos un balance real de la cuenta savings en Nessie
    disponible en DynamoDB, asi que lo derivamos de los movimientos
    registrados (todo lo que salio a savings_transfer, menos lo que ya se
    libero via savings_release)."""
    moved_in = sum(float(p["amount"]) for p in purchases if p.get("category") == "savings_transfer")
    released = sum(float(p["amount"]) for p in purchases if p.get("category") == "savings_release")
    return moved_in - released


def _amount_moved_today(items, category, today):
    return sum(
        float(i["amount"]) for i in items
        if i.get("category") == category and i.get("date") == today
    )


def verified_stop_bill(user_id, bill_title):
    """Solo detiene un cargo si de verdad esta marcado como fuga ahora mismo.
    No confia en lo que diga el LLM sobre el bill -- lo vuelve a checar."""
    if not bill_title:
        return {"ok": False, "reason": "No especificaste que suscripcion detener."}

    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    bill = next((b for b in bills_plain if b["payee"].lower() == bill_title.lower()), None)

    if not bill:
        return {"ok": False, "reason": f"No encontre ningun cargo llamado '{bill_title}'."}
    if bill["status"] != "recurring":
        return {"ok": False, "reason": f"'{bill_title}' ya no esta activo (estado actual: {bill['status']})."}
    is_leak = any(a["title"].lower() == bill_title.lower() for a in signals["alerts"])
    if not is_leak:
        return {"ok": False, "reason": f"'{bill_title}' no esta marcado como fuga en este momento -- no lo voy a detener sin una razon real detectada."}

    try:
        stop_recurring_bill(bill["bill_id"], bill["payee"], bill["payment_amount"])
    except Exception as e:
        return {"ok": False, "reason": f"No se pudo detener el cargo en Nessie ahorita: {e}. No se hizo ningun cambio."}

    table.update_item(
        Key={"user_id": user_id, "sk": f"BILL#{bill['payee']}"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "cancelled"},
    )
    return {"ok": True, "amount": bill["payment_amount"], "message": f"Detuve el cargo automatico de {bill_title} (${bill['payment_amount']}/mes). Esto no cancela el contrato con el comercio, solo el cargo."}


def verified_move_to_savings(user_id, amount, reason):
    """Mueve dinero a ahorro solo si: el monto es razonable, no hay una
    anomalia activa, no se excede el tope acumulado del dia, y no deja a
    Mia con menos del colchon minimo de seguridad."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto no es un numero valido."}
    if amount <= 0:
        return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}
    if amount > MAX_AUTONOMOUS_SAVINGS:
        return {"ok": False, "reason": f"${amount} es mas de lo que puedo mover de forma autonoma (limite ${MAX_AUTONOMOUS_SAVINGS} por transaccion). Esto necesitaria una confirmacion adicional fuera del chat."}

    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    if signals["anomaly"]["detected"]:
        return {"ok": False, "reason": f"Pause esta accion por seguridad: {signals['anomaly']['reason']}"}

    today = date.today().isoformat()
    already_today = _amount_moved_today(purchases, "savings_transfer", today)
    if already_today + amount > MAX_AUTONOMOUS_SAVINGS:
        return {"ok": False, "reason": f"Ya moviste ${already_today} a ahorro hoy -- mover ${amount} mas pasaria el tope diario autonomo de ${MAX_AUTONOMOUS_SAVINGS}."}

    current_balance = signals["_debug"]["current_balance"]
    elapsed_days = signals["_debug"]["elapsed_days"]
    projected_liquidity = score_liquidity(current_balance - amount, purchases, elapsed_days)
    if projected_liquidity["days_covered"] < LIQUIDITY_WARNING_DAYS:
        return {"ok": False, "reason": f"Mover ${amount} dejaria tu colchon en solo {projected_liquidity['days_covered']} dias de gasto esencial -- menos de una semana. No lo voy a hacer sin que lo confirmes fuera del chat."}

    try:
        sweep_to_savings(CHECKING_ID, SAVINGS_ID, amount, reason or "Ahorro solicitado por chat")
    except Exception as e:
        return {"ok": False, "reason": f"No se pudo mover el dinero en Nessie ahorita: {e}. No se hizo ningun cambio."}

    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"TXN#{today}#chat{int(time.time() * 1000)}",
        "type": "purchase", "date": today, "amount": amount,
        "category": "savings_transfer", "category_label": "Ahorro automático",
        "merchant_name": None, "description": reason or "Ahorro solicitado por chat",
    }))
    return {"ok": True, "amount": amount, "message": f"Mande ${amount} a tu ahorro. {reason or ''}".strip()}


def verified_release_buffer(user_id, amount, reason):
    """Suavizado de ingreso irregular: libera parte de lo acumulado en
    ahorro de vuelta a checking, para una semana con ingreso bajo. Misma
    categoria de riesgo que mover a ahorro (reversible, sin terceros,
    dinero de la propia Mia) -- autonomo bajo el mismo tope diario, pero
    verifica que de verdad haya ese dinero disponible en el 'pool' de
    ahorro antes de soltarlo."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto no es un numero valido."}
    if amount <= 0:
        return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}
    if amount > MAX_AUTONOMOUS_SAVINGS:
        return {"ok": False, "reason": f"${amount} es mas de lo que puedo liberar de forma autonoma (limite ${MAX_AUTONOMOUS_SAVINGS} por transaccion)."}

    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    if signals["anomaly"]["detected"]:
        return {"ok": False, "reason": f"Pause esta accion por seguridad: {signals['anomaly']['reason']}"}

    available = _savings_pool_available(purchases)
    if amount > available:
        return {"ok": False, "reason": f"Solo tienes ${available} acumulados en tu ahorro dentro de esta simulacion -- no puedo liberar ${amount}."}

    today = date.today().isoformat()
    already_today = _amount_moved_today(purchases, "savings_release", today)
    if already_today + amount > MAX_AUTONOMOUS_SAVINGS:
        return {"ok": False, "reason": f"Ya liberaste ${already_today} hoy -- liberar ${amount} mas pasaria el tope diario autonomo de ${MAX_AUTONOMOUS_SAVINGS}."}

    try:
        release_from_savings(CHECKING_ID, SAVINGS_ID, amount, reason or "Suavizado de ingreso solicitado por chat")
    except Exception as e:
        return {"ok": False, "reason": f"No se pudo liberar el dinero en Nessie ahorita: {e}. No se hizo ningun cambio."}

    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"TXN#{today}#release{int(time.time() * 1000)}",
        "type": "purchase", "date": today, "amount": amount,
        "category": "savings_release", "category_label": "Retorno de ahorro",
        "merchant_name": None, "description": reason or "Suavizado de ingreso solicitado por chat",
    }))
    return {"ok": True, "amount": amount, "message": f"Libere ${amount} de tu ahorro a tu cuenta corriente. {reason or ''}".strip()}


def _slug(category):
    return category.strip().lower().replace(" ", "_")


def get_income_pattern(user_id):
    resp = table.get_item(Key={"user_id": user_id, "sk": INCOME_PATTERN_SK})
    return resp.get("Item")


def set_income_pattern(user_id, expected_amount, frequency_days, tolerance_pct=0.25):
    try:
        expected_amount = float(expected_amount)
        frequency_days = int(frequency_days)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto o la frecuencia no son numeros validos."}
    if expected_amount <= 0 or frequency_days <= 0:
        return {"ok": False, "reason": "El monto y la frecuencia tienen que ser mayores a cero."}
    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": INCOME_PATTERN_SK,
        "expected_amount": expected_amount, "frequency_days": frequency_days,
        "tolerance_pct": float(tolerance_pct),
    }))
    return {"ok": True, "message": f"Guarde tu patron de ingreso: ~${expected_amount} cada {frequency_days} dias. Los depositos que no coincidan con esto no van a repartirse a tus apartados."}


def deposit_matches_income_pattern(pattern, deposit_amount):
    """No adivinamos si un deposito es nomina -- lo comparamos contra un
    patron que el propio usuario declaro una vez. Asi un amigo mandandote
    $100 nunca dispara un reparto a apartados por accidente."""
    if not pattern:
        return False
    expected = float(pattern["expected_amount"])
    tolerance = float(pattern.get("tolerance_pct", 0.25))
    return abs(float(deposit_amount) - expected) <= expected * tolerance


def get_envelopes(user_id):
    resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id) & Key("sk").begins_with(ENVELOPE_PREFIX))
    return resp["Items"]


def create_envelope(user_id, category, monthly_target):
    if not category or not category.strip():
        return {"ok": False, "reason": "Falta el nombre de la categoria."}
    try:
        monthly_target = float(monthly_target)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto mensual no es un numero valido."}
    if monthly_target <= 0:
        return {"ok": False, "reason": "El monto mensual tiene que ser mayor a cero."}
    slug = _slug(category)
    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"{ENVELOPE_PREFIX}{slug}",
        "category": category.strip(), "slug": slug, "monthly_target": monthly_target,
        "created_at": date.today().isoformat(),
    }))
    return {"ok": True, "message": f"Apartado '{category.strip()}' creado con meta de ${monthly_target}/mes."}


def get_envelope_balances(user_id, purchases=None):
    """El saldo de cada apartado no es un campo mutable -- se deriva de las
    transferencias reales ya escritas en Nessie, igual que hacemos con
    compute_totals. category='envelope:<slug>' es DISTINTO de
    'savings_transfer' a proposito: si compartieran categoria,
    _savings_pool_available inflaria el pool general de suavizado de
    ingreso con dinero que ya esta apartado para gastos fijos."""
    envelopes = get_envelopes(user_id)
    if purchases is None:
        _, purchases, _ = load_data(user_id)
    balances = []
    for env in envelopes:
        slug = env["slug"]
        moved = sum(float(p["amount"]) for p in purchases if p.get("category") == f"envelope:{slug}")
        balances.append({
            "category": env["category"], "slug": slug,
            "monthly_target": float(env["monthly_target"]), "balance": round(moved, 2),
        })
    return balances


def _execute_allocation(user_id, proposals, on_date):
    executed = []
    for p in proposals:
        if p["amount"] <= 0:
            continue
        try:
            sweep_to_savings(CHECKING_ID, SAVINGS_ID, p["amount"], f"Apartado automatico: {p['category']}", on_date=on_date)
        except Exception:
            continue
        table.put_item(Item=to_decimal({
            "user_id": user_id, "sk": f"TXN#{on_date}#env{p['slug']}{int(time.time() * 1000)}",
            "type": "purchase", "date": on_date, "amount": p["amount"],
            "category": f"envelope:{p['slug']}", "category_label": f"Apartado: {p['category']}",
            "merchant_name": None, "description": "Reparto automatico de nomina",
        }))
        executed.append(p)
    table.delete_item(Key={"user_id": user_id, "sk": PENDING_ALLOCATION_SK})
    return executed


def verified_allocate_envelopes(user_id, deposit_amount, deposit_date):
    """Se dispara solo cuando llega un deposito que matchea el patron de
    nomina declarado (ver deposit_matches_income_pattern). Reparte
    proporcional a cada apartado segun los dias reales transcurridos desde
    el deposito de nomina anterior -- no asume una cadencia fija, el
    ingreso de Mia es irregular. Reusa el MISMO umbral de liquidez de 7
    dias que verified_move_to_savings: si el reparto completo dejaria el
    colchon por debajo de eso, no ejecuta nada solo, deja la propuesta
    pendiente de confirmar (partial_allocation_pause)."""
    pattern = get_income_pattern(user_id)
    if not deposit_matches_income_pattern(pattern, deposit_amount):
        return {"ok": False, "reason": "Este deposito no coincide con el patron de nomina declarado -- no se reparte a apartados."}

    envelopes = get_envelopes(user_id)
    if not envelopes:
        return {"ok": False, "reason": "No hay apartados configurados todavia."}

    deposits, purchases, bills_plain = load_data(user_id)
    prior_income_dates = sorted(d["date"] for d in deposits if d["date"] < deposit_date)
    if prior_income_dates:
        elapsed = (date.fromisoformat(deposit_date) - date.fromisoformat(prior_income_dates[-1])).days
    else:
        elapsed = int(pattern.get("frequency_days", 15))
    elapsed = max(elapsed, 1)

    proposals = [
        {"category": env["category"], "slug": env["slug"], "amount": round(float(env["monthly_target"]) * elapsed / 30, 2)}
        for env in envelopes
    ]
    total_allocation = sum(p["amount"] for p in proposals)

    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    current_balance = signals["_debug"]["current_balance"]
    elapsed_days = signals["_debug"]["elapsed_days"]
    projected_liquidity = score_liquidity(current_balance - total_allocation, purchases, elapsed_days)

    if projected_liquidity["days_covered"] < LIQUIDITY_WARNING_DAYS:
        table.put_item(Item=to_decimal({
            "user_id": user_id, "sk": PENDING_ALLOCATION_SK,
            "proposals": proposals, "deposit_date": deposit_date, "total": total_allocation,
        }))
        return {
            "ok": False, "pending": True, "proposals": proposals,
            "reason": f"Repartir ${total_allocation} entre tus apartados dejaria tu colchon en {projected_liquidity['days_covered']} dias -- menos de una semana. Dejo la propuesta pendiente de confirmar.",
        }

    executed = _execute_allocation(user_id, proposals, deposit_date)
    if not executed:
        return {"ok": False, "reason": "No se pudo ejecutar el reparto en Nessie ahorita. No se hizo ningun cambio."}
    total_executed = sum(p["amount"] for p in executed)
    return {"ok": True, "amount": total_executed, "proposals": executed, "message": f"Reparti ${total_executed} entre tus apartados."}


def confirm_pending_allocation(user_id):
    resp = table.get_item(Key={"user_id": user_id, "sk": PENDING_ALLOCATION_SK})
    pending = resp.get("Item")
    if not pending:
        return {"ok": False, "reason": "No hay ningun reparto pendiente de confirmar."}
    proposals = [{"category": p["category"], "slug": p["slug"], "amount": float(p["amount"])} for p in pending["proposals"]]
    executed = _execute_allocation(user_id, proposals, pending["deposit_date"])
    total = sum(p["amount"] for p in executed)
    return {"ok": True, "amount": total, "message": f"Confirmado -- reparti ${total} entre tus apartados."}


def _sk_timestamp(sk):
    try:
        return int(sk.rsplit("#", 1)[-1])
    except (ValueError, IndexError):
        return None


def get_verified_action_history(user_id):
    """Historial completo de acciones verificadas, sin importar si vinieron
    de un checkpoint de advance-day, del chat, o del reparto automatico de
    apartados. ACTION# solo lo escribe advance-day (tiene la narrativa mas
    rica, incluye los momentos de 'pedi confirmar antes de actuar'), pero
    NOTIFICATION# lo dispara el webhook de DynamoDB Streams sobre CUALQUIER
    escritura real sin importar el origen -- es la unica fuente que no se
    pierde las acciones que el chat o el reparto de apartados ejecutaron
    solos. Se deduplican por cercania de timestamp (mismo evento real
    escribe ambos registros casi al mismo milisegundo)."""
    action_items = table.query(KeyConditionExpression=Key("user_id").eq(user_id) & Key("sk").begins_with("ACTION#"))["Items"]
    notif_items = table.query(KeyConditionExpression=Key("user_id").eq(user_id) & Key("sk").begins_with("NOTIFICATION#"))["Items"]

    events = [{
        "date": a.get("date"), "text": a.get("text"), "type": a.get("type"),
        "requires_confirmation": bool(a.get("requires_confirmation", False)),
        "_ts": _sk_timestamp(a["sk"]),
    } for a in action_items]
    action_timestamps = [e["_ts"] for e in events if e["_ts"] is not None]

    for n in notif_items:
        ts = _sk_timestamp(n["sk"])
        if ts is not None and any(abs(ts - at) < 5000 for at in action_timestamps):
            continue  # ya cubierto por un ACTION# del mismo evento real
        events.append({
            "date": n.get("date"), "text": n.get("text"), "type": "notification",
            "requires_confirmation": False, "_ts": ts,
        })

    events.sort(key=lambda e: (e["date"] or "", e["_ts"] or 0))
    for e in events:
        e.pop("_ts", None)
    return events


def get_trust_report(user_id):
    """Convierte el score interno en un artefacto de confiabilidad con
    evidencia verificada -- pensado para que Capital One lo use como señal
    de graduacion de sus propios clientes de secured card, no como un
    reporte que se le vende a bancos externos (ver PITCH.md)."""
    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    history = get_score_history(user_id)
    actions = get_verified_action_history(user_id)

    MONEY_MOVING_TYPES = {"bill_stopped", "savings_moved", "notification"}
    resolved_leaks = sum(1 for e in actions if e["type"] == "bill_stopped")
    confirmations_requested = sum(1 for e in actions if e["requires_confirmation"])
    money_events = [e for e in actions if e["type"] in MONEY_MOVING_TYPES]
    first_score = history[0]["value"] if history else signals["score"]["value"]

    summary = (
        f"En {signals['_debug']['elapsed_days']} dias de historial verificado, el agente detecto y resolvio "
        f"{resolved_leaks} fuga(s) de gasto, ejecuto {len(money_events)} accion(es) real(es) sobre el dinero de {user_id}, "
        f"y pidio confirmacion humana en {confirmations_requested} ocasion(es) antes de actuar cuando el riesgo lo ameritaba. "
        f"Su score de resiliencia paso de {first_score} a {signals['score']['value']}."
    )

    return {
        "user_id": user_id,
        "score": signals["score"],
        "score_history": history,
        "liquidity": signals["liquidity"],
        "projection": signals["projection"],
        "verified_actions": actions,
        "summary": summary,
    }
