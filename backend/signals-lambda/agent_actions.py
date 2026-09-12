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
