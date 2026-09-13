"""
Acciones verificadas del agente. Estas son las UNICAS puertas de entrada para
tocar dinero real -- tanto advance-day como el chat con Gemini llaman aqui.
Ninguna, sea disparada por un checkpoint programado o por un mensaje de chat,
ejecuta nada sin volver a verificar contra el estado real en DynamoDB/Nessie.
El LLM del chat nunca decide montos ni ejecuta directo: solo puede invocar
estas funciones, y estas funciones son las que deciden si procede o no.
"""
import calendar
import math
import re
import time
import statistics
import unicodedata
import boto3
from datetime import date, timedelta
from decimal import Decimal
from boto3.dynamodb.conditions import Key

from signal_engine import compute_signals, compute_totals, score_liquidity, compute_elapsed_days, LIQUIDITY_WARNING_DAYS, resolve_reference_date, is_neutral, NON_RECURRING_DEPOSIT_CATEGORIES
from nessie_actions import sweep_to_savings, release_from_savings, receive_from_third_party

REGION = "us-east-1"
TABLE_NAME = "jarbis-financiero-data"
CHECKING_ID = "3303b959-15c4-4a5d-aeea-1e0503712d37"  # Ana -- fallback para user_id no reconocidos
SAVINGS_ID = "5616be1c-84c4-4e37-9736-9028d5d444a0"
ACCOUNTS_BY_USER = {
    "ana": {"checking": CHECKING_ID, "savings": SAVINGS_ID},
}


def get_account_ids(user_id):
    """Cada persona tiene su propia cuenta real en Nessie -- sin esto, una
    accion de dinero (mover a ahorro, apartados) disparada con un user_id
    sin cuenta propia registrada escribiria de verdad en la cuenta de otra
    persona, aunque el registro en DynamoDB dijera el user_id correcto."""
    return ACCOUNTS_BY_USER.get(user_id, ACCOUNTS_BY_USER["ana"])
# Cuenta real de un tercero (otra app/otro dueno en el sandbox de Nessie, NO
# nuestra) usada para la demo de "nomina de un tercero" -- ver
# simulate_third_party_payroll.
EMPLOYER_ACCOUNT_ID = "98698915-42ab-45a8-a89d-31330909e9e5"
THIRD_PARTY_INCOME_CATEGORY = "income_third_party_demo"
MAX_AUTONOMOUS_SAVINGS = 100  # tope por transaccion Y por dia -- el chat no puede mover mas que esto solo

BUDGET_PREFIX = "BUDGET#"
GOAL_PREFIX = "GOAL#"
MONTHLY_BUDGET_SK = "MONTHLY_BUDGET"
DISMISSED_LEAK_PREFIX = "DISMISSED_LEAK#"
LEAK_DISMISS_DAYS = 30  # cuanto tiempo se calla una alerta de fuga descartada antes de re-evaluarla

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


def get_dismissed_leak_titles(user_id):
    """Comercios cuya alerta de fuga la usuaria pidio explicitamente que se
    callara por un tiempo (ver dismiss_leak) -- solo los que no vencieron."""
    resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id) & Key("sk").begins_with(DISMISSED_LEAK_PREFIX))
    today = date.today().isoformat()
    return {item["bill_title"] for item in resp["Items"] if item.get("dismissed_until", "") >= today}


def dismiss_leak(user_id, bill_title, days=None):
    """Silencia la alerta de 'fuga' de un cargo por un tiempo, cuando la
    usuaria confirma que si lo sigue usando. A proposito NO toca el score
    ni evaluate_bills -- 'Recurrencia sana' se sigue calculando solo con
    actividad real (ver evaluate_bills en signal_engine.py), asi que decirle
    a Kivo 'si la uso' nunca puede mejorar el score por si solo. Esto solo
    calla el AVISO; si para cuando venza el plazo sigue sin haber actividad
    real relacionada, la alerta vuelve a aparecer sola."""
    bill_title = (bill_title or "").strip()
    if not bill_title:
        return {"ok": False, "reason": "Falta el nombre del cargo."}
    try:
        days = int(days) if days is not None else LEAK_DISMISS_DAYS
    except (TypeError, ValueError):
        return {"ok": False, "reason": "Los dias no son un numero valido."}
    if days <= 0:
        return {"ok": False, "reason": "Los dias tienen que ser mayores a cero."}
    dismissed_until = (date.today() + timedelta(days=days)).isoformat()
    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"{DISMISSED_LEAK_PREFIX}{bill_title}",
        "bill_title": bill_title, "dismissed_until": dismissed_until,
    }))
    return {
        "ok": True, "bill_title": bill_title, "dismissed_until": dismissed_until, "days": days,
        "message": f"Entendido, no te voy a avisar de '{bill_title}' por {days} dias. Esto no cambia tu score -- si para entonces sigue sin actividad real, te lo vuelvo a mencionar.",
    }


def get_full_signals(deposits, purchases, bills_plain, user_id=None, as_of_date=None, persist=True):
    # as_of_date=None solia significar "no evalues anomalia ni gastos
    # proximos" -- corregido primero forzando date.today() aqui, pero eso
    # cambiaba de referencia solo con el reloj de pared: en cuanto "hoy"
    # real pasara el ultimo checkpoint sembrado, la referencia se habria
    # adelantado mas alla de cualquier dato real. Ahora compute_signals
    # resuelve internamente la fecha de referencia contra los datos mismos
    # (signal_engine.resolve_reference_date) -- no hace falta forzar nada
    # aqui, y record_score() abajo sigue usando la fecha real de hoy para
    # el punto de historial (eso si es correcto: es cuando se registro,
    # no una fecha de referencia para anomalia/pronostico).
    # compute_totals (no reimplementado a mano) -- una version anterior sumaba
    # deposits/purchases aqui mismo sin el trato especial de savings_release
    # (dinero liberado del ahorro de vuelta a checking), asi que lo contaba
    # como gasto en vez de ingreso: current_balance quedaba subestimado casi
    # el doble de cualquier release_savings_buffer, afectando de rebote a
    # get_status, move_to_savings, release_savings_buffer y
    # compute_smart_allocation (todos pasan por aqui).
    total_income, total_expense = compute_totals(deposits, purchases)
    history = get_score_history(user_id) if user_id else []
    signals = compute_signals(
        deposits, purchases, bills_plain,
        total_income=total_income, total_expense=total_expense,
        as_of_date=as_of_date, score_history=history,
    )
    if persist and user_id:
        record_score(user_id, as_of_date, signals["score"]["value"])
    if user_id:
        # Solo se calla el AVISO -- signals["score"] ya se calculo arriba con
        # el bill_health real, sin importar que se descarte despues.
        dismissed = get_dismissed_leak_titles(user_id)
        if dismissed:
            signals["alerts"] = [a for a in signals["alerts"] if not (a["type"] == "leak" and a["title"] in dismissed)]
    return signals


def get_current_signals(user_id="ana"):
    deposits, purchases, bills_plain = load_data(user_id)
    return get_full_signals(deposits, purchases, bills_plain, user_id=user_id)


EXTERNAL_SOURCES = {"cash", "other_card"}
# Mismas categorias que ya usa el resto del motor de senales (rent/groceries/
# transport/utilities/discretionary) -- a proposito NO se inventa una
# taxonomia nueva para gasto externo, porque score_essential_ratio necesita
# que la categoria caiga en ESSENTIAL_CATEGORIES o no para calcular el ratio
# correctamente, sin importar si el gasto vino del banco o fue declarado.
EXTERNAL_EXPENSE_CATEGORIES = {"rent", "groceries", "transport", "utilities", "discretionary"}
EXTERNAL_CATEGORY_LABELS = {
    "rent": "Renta", "groceries": "Comida/Despensa", "transport": "Transporte",
    "utilities": "Servicios", "discretionary": "Discrecional",
}
EXTERNAL_DEDUP_WINDOW_MS = 120_000  # 2 minutos -- mismo criterio que el chat de Jarbis (agent/tools/expenses.py)


def _find_recent_duplicate_external_expense(user_id, amount, category, source, card_name):
    """Si el LLM llama la tool dos veces para el mismo mensaje (reintento,
    doble confirmacion), no se debe duplicar el gasto -- mismo problema que
    ya resolvio el proyecto hermano Jarbis con su propia deteccion de
    duplicados en save_expense."""
    _, purchases, _ = load_data(user_id)
    now_ms = int(time.time() * 1000)
    candidates = [p for p in purchases if p.get("source") in EXTERNAL_SOURCES]
    for p in sorted(candidates, key=lambda p: p.get("logged_at_ms", 0), reverse=True):
        logged_at = p.get("logged_at_ms")
        if not logged_at or now_ms - int(logged_at) > EXTERNAL_DEDUP_WINDOW_MS:
            continue
        if (float(p["amount"]) == float(amount) and p["category"] == category
                and p.get("source") == source and (p.get("card_name") or None) == (card_name or None)):
            return p
    return None


def log_external_expense(user_id, amount, category, description, source, card_name=None):
    """Registra un gasto que NO paso por la tarjeta del banco aliado --
    efectivo o una tarjeta distinta. Sin esto, el score/ratio esencial-
    discrecional y el indice de activacion solo verian la fraccion de la
    vida financiera real del estudiante que pasa por Nessie, lo cual
    subestimaria su comportamiento financiero real y sobreestimaria cuanto
    "ahorra" dentro de lo que el banco si ve.

    Se guarda como una transaccion mas en DynamoDB (misma tabla, mismo
    patron TXN#), marcada con source= para que:
    - la senal de activacion (compute_activation_signal) la EXCLUYA --
      mide especificamente uso de la tarjeta del banco, no gasto en general.
    - el balance/colchon de liquidez (compute_totals/score_liquidity) la
      EXCLUYA -- ese dinero nunca salio de la cuenta que representan.
    - el ratio esencial/discrecional y wallet_share SI la incluyan -- son
      las dos senales que necesitan la foto completa del comportamiento."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto no es un numero valido."}
    if amount <= 0:
        return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}
    if source not in EXTERNAL_SOURCES:
        return {"ok": False, "reason": f"No reconozco la fuente '{source}'. Usa 'cash' (efectivo) u 'other_card' (otra tarjeta)."}
    category = (category or "").strip().lower()
    if category not in EXTERNAL_EXPENSE_CATEGORIES:
        return {"ok": False, "reason": f"'{category}' no es una categoria valida. Usa una de: {', '.join(sorted(EXTERNAL_EXPENSE_CATEGORIES))}."}
    card_name = (card_name or "").strip() or None
    if source == "other_card" and not card_name:
        return {"ok": False, "reason": "¿Con que tarjeta se pago? Necesito el nombre (ej. 'Banorte') antes de registrarlo."}

    duplicate = _find_recent_duplicate_external_expense(user_id, amount, category, source, card_name)
    if duplicate:
        return {"ok": False, "reason": "Ya registre un gasto identico hace unos segundos -- no lo dupliqué. Si de verdad son dos gastos distintos, dilo explicitamente."}

    today = date.today().isoformat()
    now_ms = int(time.time() * 1000)
    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"TXN#{today}#external{now_ms}",
        "type": "purchase", "date": today, "amount": amount,
        "category": category, "category_label": EXTERNAL_CATEGORY_LABELS[category],
        "merchant_name": None, "description": description or "Gasto registrado manualmente",
        "source": source, "card_name": card_name, "logged_at_ms": now_ms,
    }))
    label = "efectivo" if source == "cash" else f"tu tarjeta {card_name}"
    return {
        "ok": True, "amount": amount, "category": category, "source": source,
        "message": f"Registre ${amount} en {label} ({EXTERNAL_CATEGORY_LABELS[category]}). Esto se suma a tu presupuesto, pero no cuenta como uso de tu tarjeta del banco.",
    }


SIMULATABLE_ACTIONS = {"stop_bill", "reduce_discretionary"}


def simulate_decision(user_id, action, params=None):
    """Simulador financiero educativo: responde '¿que pasaria con mi score
    si...?' reutilizando el MISMO signal_engine.compute_signals que ya
    calcula el score real, sobre una copia hipotetica de los datos -- nunca
    toca Nessie ni DynamoDB. La comparacion antes/despues es honesta
    (mismos calculos, no una aproximacion aparte), y el "lesson" que
    regresa esta atado a los numeros reales de esta simulacion, no es un
    consejo generico. Pensado para dejar que alguien pruebe una decision
    antes de tomarla -- eso es educacion financiera aplicada, no solo un
    dato mas en el dashboard."""
    params = params or {}
    if action not in SIMULATABLE_ACTIONS:
        return {"ok": False, "reason": f"No se como simular '{action}'. Puedo simular: {', '.join(sorted(SIMULATABLE_ACTIONS))}."}

    deposits, purchases, bills_plain = load_data(user_id)
    # compute_totals, no reimplementado a mano -- mismo bug real que ya se
    # corrigio en get_full_signals: sumar purchases bank-only sin el trato
    # especial de savings_release lo contaba como gasto en vez de ingreso.
    total_income, total_expense = compute_totals(deposits, purchases)
    history = get_score_history(user_id) if user_id else []
    current = compute_signals(deposits, purchases, bills_plain, total_income=total_income, total_expense=total_expense, score_history=history)

    if action == "stop_bill":
        payee = (params.get("bill_payee") or "").strip()
        bill = next((b for b in bills_plain if b["payee"].lower() == payee.lower()), None)
        if not bill:
            return {"ok": False, "reason": f"No encontre ningun cargo llamado '{payee}' para simular."}
        if bill["status"] != "recurring":
            return {"ok": False, "reason": f"'{bill['payee']}' ya no esta activo (estado actual: {bill['status']}) -- no hay nada que simular."}
        monthly_amount = float(bill["payment_amount"])
        changed_amount = monthly_amount
        hypothetical_bills = [dict(b, status="cancelled") if b["bill_id"] == bill["bill_id"] else b for b in bills_plain]
        hypothetical = compute_signals(
            deposits, purchases, hypothetical_bills,
            total_income=total_income, total_expense=total_expense - monthly_amount,
            score_history=history,
        )
        lesson = (
            f"Dejar de pagar '{bill['payee']}' (${monthly_amount}/mes) mueve dos de los 4 factores del score a la vez: "
            f"tu recurrencia de bills sube porque ese cargo deja de contar como una fuga sin monitorear (pesa 20%), y "
            f"si ese dinero se queda en tu cuenta en vez de gastarse, tu colchon de liquidez tambien crece (pesa otro 20%). "
            f"Por eso las suscripciones olvidadas pesan tanto: no es solo el gasto, es la señal de que nadie esta viendo a donde va el dinero."
        )
    else:  # reduce_discretionary
        try:
            target_amount = float(params.get("monthly_amount"))
        except (TypeError, ValueError):
            return {"ok": False, "reason": "Dime un monto valido para simular la reduccion de gasto discrecional."}
        if target_amount <= 0:
            return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}
        current_discretionary = sum(float(p["amount"]) for p in purchases if p["category"] == "discretionary")
        if current_discretionary <= 0:
            return {"ok": False, "reason": "No tienes gasto discrecional registrado todavia -- no hay nada que reducir en la simulacion."}
        actual_reduction = min(target_amount, current_discretionary)
        changed_amount = actual_reduction
        scale = (current_discretionary - actual_reduction) / current_discretionary
        hypothetical_purchases = [
            {**p, "amount": float(p["amount"]) * scale} if p["category"] == "discretionary" else p
            for p in purchases
        ]
        _, hypothetical_total_expense = compute_totals(deposits, hypothetical_purchases)
        hypothetical = compute_signals(
            deposits, hypothetical_purchases, bills_plain,
            total_income=total_income, total_expense=hypothetical_total_expense,
            score_history=history,
        )
        lesson = (
            f"Reducir ${round(actual_reduction)} de gasto discrecional sube directamente tu ratio esencial/discrecional "
            f"(pesa 25% del score, el segundo factor mas pesado despues de la regularidad de ingreso) y tambien tu "
            f"colchon de liquidez, porque ese dinero se queda disponible en vez de gastarse."
        )
        if actual_reduction < target_amount:
            lesson += f" Nota: solo tienes ${round(current_discretionary)} de gasto discrecional registrado en este periodo, asi que la simulacion se limito a eso."

    return {
        "ok": True,
        "monthly_amount": round(changed_amount, 2),
        "score_now": current["score"]["value"],
        "score_projected": hypothetical["score"]["value"],
        "score_delta": hypothetical["score"]["value"] - current["score"]["value"],
        "liquidity_days_now": current["liquidity"]["days_covered"],
        "liquidity_days_projected": hypothetical["liquidity"]["days_covered"],
        "lesson": lesson,
    }


FACTOR_LESSONS = {
    "income_regularity": {
        "why": "Pesa 35% del score, el factor mas pesado de los 4 -- mide que tan predecible es la fecha y el monto de tus depositos.",
        "how": "No se puede simular con una decision de gasto (depende de tus fuentes de ingreso reales), pero declarar tu patron de nomina ayuda a que el sistema reconozca tus depositos reales de forma consistente.",
    },
    "essential_ratio": {
        "why": "Pesa 25% del score, el segundo factor mas pesado -- mide que tan grande es tu gasto discrecional comparado con tu ingreso total.",
        "how": "Prueba simular una reduccion de gasto discrecional (simulate_decision con action='reduce_discretionary') para ver el impacto exacto en tu score antes de decidir algo real.",
    },
    "bill_health": {
        "why": "Pesa 20% del score -- mide cuantos de tus cargos recurrentes tienen actividad real relacionada, contra cuantos son fugas sin monitorear.",
        "how": "Si tienes una fuga detectada, prueba simular que la detienes (simulate_decision con action='stop_bill') para ver el impacto exacto antes de decidir algo real.",
    },
    "liquidity_cushion": {
        "why": "Pesa 20% del score -- mide cuantos dias de gasto esencial cubre tu balance actual.",
        "how": "Resolver una fuga o reducir gasto discrecional tambien mejora este factor, porque deja mas dinero disponible en tu cuenta -- son las mismas dos simulaciones de arriba.",
    },
}


def get_weakest_factor_lesson(user_id):
    """Identifica el factor mas debil del score real de la persona y explica
    por que le pesa, usando SUS propios numeros (el campo 'detail' ya viene
    calculado por signal_engine a partir de datos reales) -- no una lista
    de tips genericos, que es justo lo que este proyecto evita a proposito
    en el resto del producto."""
    signals = get_current_signals(user_id)
    breakdown = signals["score"]["breakdown"]
    if not breakdown:
        return {"ok": False, "reason": "No hay suficientes datos todavia para identificar tu factor mas debil."}
    weakest = min(breakdown, key=lambda item: item["value"])
    lesson = FACTOR_LESSONS.get(weakest["key"], {})
    return {
        "ok": True,
        "factor": weakest["key"],
        "factor_label": weakest["label"],
        "value": weakest["value"],
        "weight": weakest["weight"],
        "detail": weakest["detail"],
        "why_it_matters": lesson.get("why", ""),
        "how_to_improve": lesson.get("how", ""),
    }


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


PENDING_STOP_BILL_SK = "PENDING_STOP_BILL"


def _find_leak_bill(user_id, bill_title):
    """Revisa desde cero si bill_title es una fuga real ahora mismo. No
    ejecuta nada -- lo comparten verified_stop_bill/propose_stop_bill para
    no duplicar la logica de verificacion."""
    if not bill_title:
        return None, {"ok": False, "reason": "No especificaste que suscripcion detener."}
    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    bill = next((b for b in bills_plain if b["payee"].lower() == bill_title.lower()), None)
    if not bill:
        return None, {"ok": False, "reason": f"No encontre ningun cargo llamado '{bill_title}'."}
    if bill["status"] != "recurring":
        return None, {"ok": False, "reason": f"'{bill_title}' ya no esta activo (estado actual: {bill['status']})."}
    is_leak = any(a["title"].lower() == bill_title.lower() for a in signals["alerts"])
    if not is_leak:
        return None, {"ok": False, "reason": f"'{bill_title}' no esta marcado como fuga en este momento -- no lo voy a detener sin una razon real detectada."}
    return bill, None


def _execute_stop_bill(user_id, bill):
    """Kivo ya no ejecuta cambios reales contra el banco/Nessie -- solo
    actualiza su propia vista (score, alertas, recordatorios). Cancelar el
    cargo de verdad con el comercio sigue siendo responsabilidad del
    usuario; esto nunca "mueve dinero", solo deja de contarlo."""
    table.update_item(
        Key={"user_id": user_id, "sk": f"BILL#{bill['payee']}"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "cancelled"},
    )
    return {
        "ok": True, "amount": bill["payment_amount"],
        "message": f"Listo -- para Kivo, {bill['payee']} (${bill['payment_amount']}/mes) ya no se va a pagar: no lo vuelvo "
                   "a contar en tu score ni en tus recordatorios. Si el cargo real sigue activo con el comercio, "
                   "tienes que cancelarlo tu directamente con ellos.",
    }


def verified_stop_bill(user_id, bill_title):
    """Usado por advance-day: el propio checkpoint (Dia 62 avisa y pausa,
    Dia 63 ejecuta) YA es el paso de confirmacion humana de dos tiempos, asi
    que aqui se revalida desde cero y se ejecuta directo."""
    bill, error = _find_leak_bill(user_id, bill_title)
    if error:
        return error
    return _execute_stop_bill(user_id, bill)


def propose_stop_bill(user_id, bill_title):
    """Usado por el chat, que NO tiene un checkpoint externo que sirva de
    confirmacion -- NUNCA ejecuta en esta llamada, solo deja la propuesta
    pendiente. Encontrado en una auditoria propia: antes el chat ejecutaba
    esto en una sola llamada, y la unica barrera contra una cancelacion
    prematura era una instruccion de prompt ('no llames esto sin
    confirmacion explicita') -- exactamente el tipo de seguridad que
    depende de que el LLM se porte bien, lo que el resto del proyecto evita
    a proposito."""
    bill, error = _find_leak_bill(user_id, bill_title)
    if error:
        return error
    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": PENDING_STOP_BILL_SK,
        "bill_id": bill["bill_id"], "payee": bill["payee"], "payment_amount": bill["payment_amount"],
    }))
    # monthly_amount explicito -- mismo bug que set_category_budget: el
    # monto solo vivia dentro del string "reason", asi que cuando Gemini le
    # repetia el monto a la usuaria para pedirle confirmacion,
    # find_unverified_amounts lo marcaba como no verificado y lo redactaba
    # a "[monto no confirmado]" en el flujo central de la demo (la fuga de
    # FitZone Campus).
    return {
        "ok": False, "pending": True, "payee": bill["payee"], "monthly_amount": bill["payment_amount"],
        "reason": f"Detecte que '{bill['payee']}' (${bill['payment_amount']}/mes) es una fuga real -- no tiene actividad relacionada. "
                  f"Si tiene contrato anual, cancelar antes de tiempo podria generarte una penalizacion o mandarte a cobranza. "
                  f"Confirma explicitamente que ya no lo usas y quieres que lo detenga.",
    }


def confirm_stop_bill(user_id):
    """Segunda mitad del paso de dos tiempos del chat -- revalida desde
    cero (no confia en que la propuesta siga siendo valida) antes de tocar
    Nessie de verdad."""
    resp = table.get_item(Key={"user_id": user_id, "sk": PENDING_STOP_BILL_SK})
    pending = resp.get("Item")
    if not pending:
        return {"ok": False, "reason": "No hay ninguna cancelacion pendiente de confirmar."}
    table.delete_item(Key={"user_id": user_id, "sk": PENDING_STOP_BILL_SK})
    bill, error = _find_leak_bill(user_id, pending["payee"])
    if error:
        return {"ok": False, "reason": f"Ya no puedo confirmar esto: {error['reason']}"}
    return _execute_stop_bill(user_id, bill)


def verified_move_to_savings(user_id, amount, reason):
    """Mueve dinero a ahorro solo si: el monto es razonable, no hay una
    anomalia activa, no se excede el tope acumulado del dia, y no deja a
    Ana con menos del colchon minimo de seguridad."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto no es un numero valido."}
    if amount <= 0:
        return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}
    # Nota general de esta funcion: cada return de rechazo trae los montos
    # mencionados en "reason" tambien como campos explicitos (amount,
    # already_moved_today, max_autonomous, days_covered) -- sin esto,
    # find_unverified_amounts en lambda_chat.py no los reconoce como
    # verificados (solo ve numeros que existan como campo real del
    # resultado, no cifras que solo vivan dentro del string), y la
    # explicacion que Gemini le da a la usuaria de por que se rechazo la
    # accion se redacta a "[monto no confirmado]".
    if amount > MAX_AUTONOMOUS_SAVINGS:
        return {"ok": False, "amount": amount, "max_autonomous": MAX_AUTONOMOUS_SAVINGS, "reason": f"${amount} es mas de lo que puedo mover de forma autonoma (limite ${MAX_AUTONOMOUS_SAVINGS} por transaccion). Esto necesitaria una confirmacion adicional fuera del chat."}

    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    if signals["anomaly"]["detected"]:
        return {"ok": False, "reason": f"Pause esta accion por seguridad: {signals['anomaly']['reason']}"}

    today = date.today().isoformat()
    already_today = _amount_moved_today(purchases, "savings_transfer", today)
    if already_today + amount > MAX_AUTONOMOUS_SAVINGS:
        return {"ok": False, "amount": amount, "already_moved_today": already_today, "max_autonomous": MAX_AUTONOMOUS_SAVINGS, "reason": f"Ya moviste ${already_today} a ahorro hoy -- mover ${amount} mas pasaria el tope diario autonomo de ${MAX_AUTONOMOUS_SAVINGS}."}

    current_balance = signals["_debug"]["current_balance"]
    elapsed_days = signals["_debug"]["elapsed_days"]
    projected_liquidity = score_liquidity(current_balance - amount, purchases, elapsed_days)
    if projected_liquidity["days_covered"] < LIQUIDITY_WARNING_DAYS:
        return {"ok": False, "amount": amount, "days_covered": projected_liquidity["days_covered"], "reason": f"Mover ${amount} dejaria tu colchon en solo {projected_liquidity['days_covered']} dias de gasto esencial -- menos de una semana. No lo voy a hacer sin que lo confirmes fuera del chat."}

    try:
        accounts = get_account_ids(user_id)
        sweep_to_savings(accounts["checking"], accounts["savings"], amount, reason or "Ahorro solicitado por chat")
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
    dinero de la propia Ana) -- autonomo bajo el mismo tope diario, pero
    verifica que de verdad haya ese dinero disponible en el 'pool' de
    ahorro antes de soltarlo."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto no es un numero valido."}
    if amount <= 0:
        return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}
    if amount > MAX_AUTONOMOUS_SAVINGS:
        return {"ok": False, "amount": amount, "max_autonomous": MAX_AUTONOMOUS_SAVINGS, "reason": f"${amount} es mas de lo que puedo liberar de forma autonoma (limite ${MAX_AUTONOMOUS_SAVINGS} por transaccion)."}

    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    if signals["anomaly"]["detected"]:
        return {"ok": False, "reason": f"Pause esta accion por seguridad: {signals['anomaly']['reason']}"}

    available = _savings_pool_available(purchases)
    if amount > available:
        return {"ok": False, "amount": amount, "available": available, "reason": f"Solo tienes ${available} acumulados en tu ahorro dentro de esta simulacion -- no puedo liberar ${amount}."}

    today = date.today().isoformat()
    already_today = _amount_moved_today(purchases, "savings_release", today)
    if already_today + amount > MAX_AUTONOMOUS_SAVINGS:
        return {"ok": False, "amount": amount, "already_released_today": already_today, "max_autonomous": MAX_AUTONOMOUS_SAVINGS, "reason": f"Ya liberaste ${already_today} hoy -- liberar ${amount} mas pasaria el tope diario autonomo de ${MAX_AUTONOMOUS_SAVINGS}."}

    try:
        accounts = get_account_ids(user_id)
        release_from_savings(accounts["checking"], accounts["savings"], amount, reason or "Suavizado de ingreso solicitado por chat")
    except Exception as e:
        return {"ok": False, "reason": f"No se pudo liberar el dinero en Nessie ahorita: {e}. No se hizo ningun cambio."}

    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"TXN#{today}#release{int(time.time() * 1000)}",
        "type": "purchase", "date": today, "amount": amount,
        "category": "savings_release", "category_label": "Retorno de ahorro",
        "merchant_name": None, "description": reason or "Suavizado de ingreso solicitado por chat",
    }))
    return {"ok": True, "amount": amount, "message": f"Libere ${amount} de tu ahorro a tu cuenta corriente. {reason or ''}".strip()}


def get_monthly_budget(user_id):
    resp = table.get_item(Key={"user_id": user_id, "sk": MONTHLY_BUDGET_SK})
    return resp.get("Item")


def set_monthly_budget(user_id, amount):
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto no es un numero valido."}
    if amount <= 0:
        return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}
    table.put_item(Item=to_decimal({"user_id": user_id, "sk": MONTHLY_BUDGET_SK, "amount": amount}))
    return {"ok": True, "amount": amount, "message": f"Guarde tu meta de gasto mensual: ${amount}."}


def simulate_third_party_payroll(user_id, amount, employer_label="Papa y mama", on_date=None):
    """Mueve dinero DE VERDAD desde la cuenta de un tercero (otra app/otro
    dueno en el sandbox de Nessie, no la nuestra) a la cuenta de Ana, y lo
    registra como cualquier deposito real -- util para la demo y para
    alimentar el saldo que despues usa compute_smart_allocation. Esta SI es
    una escritura real en Nessie: no es Kivo moviendo el dinero de Ana, es
    Kivo observando que un tercero se lo mando. `employer_label` es
    generico a proposito -- sirve tanto para nomina de los papas como para
    cualquier otro ingreso real de un tercero (beca, regalo, trabajo
    freelance) que Ana reporte por chat, ver log_income_deposit."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto no es un numero valido."}
    if amount <= 0:
        return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}

    _, purchases, _ = load_data(user_id)
    on_date = on_date or resolve_reference_date(None, purchases) or date.today().isoformat()

    try:
        checking_id = get_account_ids(user_id)["checking"]
        movement = receive_from_third_party(EMPLOYER_ACCOUNT_ID, checking_id, amount, employer_label, on_date=on_date)
    except Exception as e:
        return {"ok": False, "reason": f"No se pudo mover el dinero en Nessie: {e}"}

    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"TXN#{on_date}#thirdparty{int(time.time() * 1000)}",
        "type": "deposit", "date": on_date, "amount": amount,
        "category": THIRD_PARTY_INCOME_CATEGORY, "category_label": employer_label,
        "merchant_name": None, "description": f"{employer_label} (cuenta de un tercero, verificable en Nessie)",
    }))
    return {
        "ok": True, "amount": amount, "date": on_date, "nessie": movement,
        "message": f"Se recibieron ${amount} ({employer_label}) en tu cuenta el {on_date}.",
    }


def _slugify_label(label):
    normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", normalized.strip().lower()).strip("_")
    return slug or "meta"


def _months_between(start_iso, end_iso):
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    return max((end.year - start.year) * 12 + (end.month - start.month), 1)


def estimate_monthly_disposable(user_id, exclude_goal_slug=None):
    """Cuanto dinero real le queda libre a Ana por mes, al ritmo actual --
    ingreso real menos gasto real (bank-only) menos lo que ya comprometio
    en otras metas de ahorro. NO resta las metas de categoria: esas son
    techos sobre gasto que YA esta contado en total_expense, restarlas
    tambien contaria ese gasto dos veces.

    OJO: esto NO reutiliza signals["_debug"]["total_expense"] tal cual.
    Ese total (compute_totals) cuenta un sweep a ahorro (savings_transfer)
    como salida real -- correcto para current_balance/liquidez, porque ese
    dinero de verdad sale de checking. Pero para "cuanto te queda libre
    por mes", un sweep a tu propio ahorro no es gasto real (is_neutral()),
    es solo reubicar dinero tuyo -- si se contara como gasto aqui, cada
    prueba en vivo de move_to_savings (fuera del ciclo de reset de
    advance-day) iba inflando este total_expense para siempre sin un
    income correspondiente, hasta casi empatar con total_income y dejar
    el disponible en centavos. Se recalcula aparte, con la misma
    exclusion is_neutral() que ya usan score_essential_ratio/detect_anomaly.

    Por la misma razon, total_income TAMPOCO reutiliza
    signals["_debug"]["total_income"] tal cual -- ese total suma TODO
    deposito sin filtrar, incluyendo ajustes de una sola vez (ej.
    "opening_balance", el saldo inicial antes del historial sembrado) o
    depositos de demo de un tercero (income_third_party_demo). Proyectar
    un ingreso de una sola vez como si fuera mensual recurrente (total/
    elapsed_days*30) infla el disponible de forma irreal -- mismo criterio
    de NON_RECURRING_DEPOSIT_CATEGORIES que ya usa score_income_regularity."""
    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    elapsed_days = max(signals["_debug"]["elapsed_days"], 1)
    total_income = sum(
        float(d["amount"]) for d in deposits
        if d.get("category") not in NON_RECURRING_DEPOSIT_CATEGORIES
    )
    real_expense = sum(
        float(p["amount"]) for p in purchases
        if p.get("source", "bank") == "bank"
        and p.get("category") != "savings_release"
        and not is_neutral(p.get("category"))
    )
    monthly_income = total_income / elapsed_days * 30
    monthly_expense = real_expense / elapsed_days * 30
    committed_goals = sum(
        float(g["monthly_contribution"]) for g in get_goals(user_id) if g["slug"] != exclude_goal_slug
    )
    return round(monthly_income - monthly_expense - committed_goals, 2)


MAX_REALISTIC_GOAL_MONTHS = 60  # 5 anios -- mas alla de esto deja de ser un plan util y se vuelve solo un numero


def create_goal(user_id, label, target_amount, target_date=None):
    """Meta de ahorro de largo plazo (ej. 'viaje a Japon') -- a diferencia
    de una meta de categoria, esta SI acumula un total a lo largo de
    varios meses. Kivo no mueve ni aparta nada: solo calcula cuanto tiempo
    es realista segun tu disponible real, y guarda el plan. El avance de
    verdad se registra con log_goal_contribution cuando tu apartas el
    dinero por tu cuenta."""
    label = (label or "").strip()
    if not label:
        return {"ok": False, "reason": "Falta el nombre de la meta."}
    try:
        target_amount = float(target_amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto objetivo no es un numero valido."}
    if target_amount <= 0:
        return {"ok": False, "reason": "El monto objetivo tiene que ser mayor a cero."}

    slug = _slugify_label(label)
    disposable = estimate_monthly_disposable(user_id, exclude_goal_slug=slug)

    if target_date:
        months = _months_between(date.today().isoformat(), target_date)
        monthly_contribution = round(target_amount / months, 2)
        # Antes "disposable <= 0" contaba como realista por accidente --
        # sin dinero libre, CUALQUIER aporte requerido es irrealista, no al reves.
        realistic = disposable > 0 and monthly_contribution <= disposable
    elif disposable <= 0:
        return {"ok": False, "reason": f"Con tu ingreso y gasto real actual no veo dinero libre por mes para ahorrar (~${disposable}) -- todavia no puedo armarte un plan realista. Baja una meta de categoria o ajusta tu gasto primero."}
    else:
        months = max(math.ceil(target_amount / disposable), 1)
        monthly_contribution = round(target_amount / months, 2)
        # El disponible SIEMPRE alcanza aqui (months se calcula justo para
        # que quepa) -- lo que puede no ser realista es el PLAZO: a $69/mes
        # una meta de $80,000 toma 1154 meses (~96 anios), matematicamente
        # "cabe" pero no es un plan util para nadie.
        realistic = months <= MAX_REALISTIC_GOAL_MONTHS

    existing = table.get_item(Key={"user_id": user_id, "sk": f"{GOAL_PREFIX}{slug}"}).get("Item")
    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"{GOAL_PREFIX}{slug}", "type": "goal",
        "slug": slug, "label": label, "target_amount": target_amount,
        "monthly_contribution": monthly_contribution, "estimated_months": months,
        "realistic": realistic,
        "contributed": existing["contributed"] if existing else 0,
        "created_at": existing["created_at"] if existing else date.today().isoformat(),
    }))
    verb = "actualizada" if existing else "creada"
    years = round(months / 12, 1)
    if realistic:
        message = f"Meta '{label}' {verb}: ${target_amount} en ~{months} meses (~${monthly_contribution}/mes segun tu disponible real). No aparto nada -- usa log_goal_contribution cuando de verdad apartes dinero para esto."
    elif target_date:
        message = f"Meta '{label}' {verb}: para llegar a ${target_amount} el {target_date} necesitas ~${monthly_contribution}/mes, mas de lo que veo disponible hoy (~${disposable}/mes). La guarde de todos modos, pero puede que tengas que mover la fecha o bajar otra meta."
    else:
        message = f"Meta '{label}' {verb}: a tu ritmo actual (~${disposable}/mes libres) esto tomaria ~{months} meses (~{years} anios) -- no es un plazo realista para la mayoria de las metas. La guarde de todos modos, pero considera bajar el monto o buscar mas margen en tu presupuesto antes de comprometerte."
    return {
        "ok": True, "slug": slug, "target_amount": target_amount, "months": months,
        "monthly_contribution": monthly_contribution, "disposable": disposable,
        "realistic": realistic, "message": message,
    }


def get_goals(user_id):
    resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id) & Key("sk").begins_with(GOAL_PREFIX))
    return resp["Items"]


def get_goals_with_progress(user_id):
    today = date.today()
    goals = []
    for g in get_goals(user_id):
        target_amount = float(g["target_amount"])
        contributed = float(g.get("contributed", 0))
        created = date.fromisoformat(g["created_at"])
        months_elapsed = max((today.year - created.year) * 12 + (today.month - created.month), 0)
        expected_by_now = round(min(float(g["monthly_contribution"]) * months_elapsed, target_amount), 2)
        on_track = contributed >= expected_by_now
        # "nueva" existe para no decir 'vas a buen ritmo' de una meta recien
        # creada con $0 aportados -- tecnicamente cumple (nada se le debia
        # todavia), pero suena a logro cuando en realidad no ha pasado nada.
        if target_amount and contributed >= target_amount:
            pace = "cumplida"
        elif months_elapsed == 0:
            pace = "nueva"
        elif on_track:
            pace = "bien"
        else:
            pace = "atrasada"
        goals.append({
            "slug": g["slug"], "label": g["label"], "target_amount": target_amount,
            "monthly_contribution": float(g["monthly_contribution"]), "estimated_months": int(g["estimated_months"]),
            "contributed": contributed, "remaining": round(max(target_amount - contributed, 0), 2),
            "percent": round(contributed / target_amount * 100) if target_amount else 0,
            "on_track": on_track, "expected_by_now": expected_by_now, "pace": pace,
            "realistic": bool(g.get("realistic", True)),
        })
    return goals


def log_goal_contribution(user_id, goal_slug, amount, note=None):
    """Registra que Ana aparto dinero para una meta POR SU CUENTA (fuera de
    Nessie, igual que log_external_expense) -- Kivo no mueve nada, solo
    lleva la cuenta de cuanto lleva acumulado hacia esa meta."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto no es un numero valido."}
    if amount <= 0:
        return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}
    key = {"user_id": user_id, "sk": f"{GOAL_PREFIX}{goal_slug}"}
    goal = table.get_item(Key=key).get("Item")
    if not goal:
        return {"ok": False, "reason": "No encontre esa meta."}
    new_total = round(float(goal.get("contributed", 0)) + amount, 2)
    updated = {**goal, "contributed": new_total}
    table.put_item(Item=to_decimal(updated))
    remaining = round(float(goal["target_amount"]) - new_total, 2)
    done = remaining <= 0
    return {
        "ok": True, "contributed": new_total, "remaining": max(remaining, 0), "done": done,
        "target_amount": float(goal["target_amount"]),
        "message": f"Anotado: llevas ${new_total} de ${float(goal['target_amount'])} para '{goal['label']}'." + (" Ya la cumpliste!" if done else ""),
    }


def _days_in_month(iso_date):
    year, month = int(iso_date[:4]), int(iso_date[5:7])
    return calendar.monthrange(year, month)[1]


def set_category_budget(user_id, category, monthly_target, label=None):
    """Meta mensual sobre una categoria REAL de gasto -- reusa la misma
    taxonomia cerrada de log_external_expense (no texto libre como el
    apartado viejo), para que la meta se pueda comparar contra gasto real
    sin necesitar transacciones sinteticas. Kivo no aparta ni mueve nada:
    solo guarda la meta para comparar despues. monthly_target=0 borra la
    meta -- asi no hace falta una ruta DELETE nueva en API Gateway."""
    category = (category or "").strip().lower()
    if category not in EXTERNAL_EXPENSE_CATEGORIES:
        return {"ok": False, "reason": f"Categoria no valida. Usa una de: {', '.join(sorted(EXTERNAL_EXPENSE_CATEGORIES))}."}
    try:
        monthly_target = float(monthly_target)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto mensual no es un numero valido."}
    if monthly_target < 0:
        return {"ok": False, "reason": "El monto mensual no puede ser negativo."}
    default_label = EXTERNAL_CATEGORY_LABELS[category]
    if monthly_target == 0:
        table.delete_item(Key={"user_id": user_id, "sk": f"{BUDGET_PREFIX}{category}"})
        return {"ok": True, "message": f"Quite la meta de {default_label}."}
    existing = table.get_item(Key={"user_id": user_id, "sk": f"{BUDGET_PREFIX}{category}"}).get("Item")
    final_label = (label or "").strip() or (existing["label"] if existing else default_label)
    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"{BUDGET_PREFIX}{category}", "type": "category_budget",
        "category": category, "label": final_label, "monthly_target": monthly_target,
        "created_at": existing["created_at"] if existing else date.today().isoformat(),
    }))
    verb = "actualizada" if existing else "creada"
    # monthly_target explicito en la respuesta (no solo dentro de "message")
    # -- find_unverified_amounts en lambda_chat.py solo reconoce numeros que
    # vienen como campo real del resultado de la tool, no cifras que solo
    # existan dentro de un string. Sin esto, cualquier respuesta del chat
    # que mencionara el monto se redactaba a "[monto no confirmado]" aunque
    # la meta se hubiera guardado bien -- mismo patron de bug que ya se
    # habia corregido una vez para monthly_amount en las alertas de
    # get_status y en simulate_decision.
    return {
        "ok": True, "category": category, "label": final_label, "monthly_target": monthly_target,
        "message": f"Meta {verb}: ${monthly_target}/mes en {final_label}. No aparto ni muevo nada -- comparo tus compras reales contra esto.",
    }


def get_category_budgets(user_id):
    resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id) & Key("sk").begins_with(BUDGET_PREFIX))
    return resp["Items"]


def get_budget_status(user_id, as_of_date=None, purchases=None):
    """Nucleo del modelo de presupuesto: NO hay saldo acumulado ni
    transferencias, 'gastado' son transacciones que YA existian. Incluye a
    proposito el gasto declarado en efectivo/otra tarjeta (source!=bank):
    un presupuesto describe comportamiento completo, el criterio OPUESTO al
    de compute_totals (que solo mide el saldo del banco) -- son dos
    preguntas distintas sobre los mismos datos."""
    if purchases is None:
        _, purchases, _ = load_data(user_id)
    reference = resolve_reference_date(as_of_date, purchases)
    if not reference:
        return {
            "month": None, "reference_date": None, "day_of_month": None, "days_in_month": None,
            "categories": [], "unbudgeted": [], "unbudgeted_spend": 0,
            "total_targets": 0, "total_spent": 0, "monthly_budget": None,
            "goals": get_goals_with_progress(user_id),
        }
    month = reference[:7]
    spent = {}
    for p in purchases:
        category = p.get("category")
        if not category or is_neutral(category) or category == "savings_release" or p["date"][:7] != month:
            continue
        spent[category] = spent.get(category, 0.0) + float(p["amount"])

    day = int(reference[8:10])
    days_in_month = _days_in_month(reference)
    month_progress = day / days_in_month

    categories = []
    for b in get_category_budgets(user_id):
        category = b["category"]
        target = float(b["monthly_target"])
        used = round(spent.pop(category, 0.0), 2)
        if used > target:
            pace = "excedido"
        elif target and (used / target) > month_progress + 0.15:
            pace = "apretado"
        else:
            pace = "bien"
        categories.append({
            "category": category, "label": b["label"], "monthly_target": target,
            "spent": used, "remaining": round(target - used, 2),
            "percent": round(used / target * 100) if target else 0,
            "projected_month_end": round(used / max(day, 1) * days_in_month, 2),
            "pace": pace,
        })
    categories.sort(key=lambda c: -c["spent"])

    unbudgeted = sorted((
        {"category": key, "label": EXTERNAL_CATEGORY_LABELS.get(key, key), "spent": round(amount, 2)}
        for key, amount in spent.items()
    ), key=lambda c: -c["spent"])

    mb = get_monthly_budget(user_id)
    return {
        "month": month, "reference_date": reference, "day_of_month": day, "days_in_month": days_in_month,
        "categories": categories, "unbudgeted": unbudgeted,
        "unbudgeted_spend": round(sum(u["spent"] for u in unbudgeted), 2),
        "total_targets": round(sum(c["monthly_target"] for c in categories), 2),
        "total_spent": round(sum(c["spent"] for c in categories) + sum(u["spent"] for u in unbudgeted), 2),
        "monthly_budget": float(mb["amount"]) if mb else None,
        "goals": get_goals_with_progress(user_id),
    }


def compute_smart_allocation(user_id):
    """Reparto inteligente bajo demanda -- sustituye al viejo plan de
    nomina reactivo (que solo se armaba cuando un deposito matcheaba un
    patron declarado). Este corre cuando el usuario lo pide, usando su
    saldo actual, y sigue sin mover nada: aplica los mismos criterios que
    ya protegen cualquier movimiento real en esta app (no toca el colchon
    minimo de LIQUIDITY_WARNING_DAYS, se detiene si hay una anomalia
    activa), mas el mismo prorrateo por pendiente que el plan viejo. Si lo
    que piden tus metas no cabe en lo disponible, escala todo
    proporcionalmente en vez de solo reportar que no alcanza."""
    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain, user_id=user_id, persist=False)
    if signals["anomaly"]["detected"]:
        return {"ok": False, "reason": f"No arme un reparto ahorita por seguridad: {signals['anomaly']['reason']}"}

    status = get_budget_status(user_id, purchases=purchases)
    goals = get_goals_with_progress(user_id)
    if not status["categories"] and not goals:
        return {"ok": False, "reason": "No tienes metas de gasto ni de ahorro configuradas todavia -- crea al menos una para poder armarte un reparto."}

    current_balance = signals["_debug"]["current_balance"]
    days_covered = signals["liquidity"]["days_covered"]
    avg_daily_essential = current_balance / days_covered if 0 < days_covered < 999 else 0
    cushion_floor = round(avg_daily_essential * LIQUIDITY_WARNING_DAYS, 2)
    available = max(round(current_balance - cushion_floor, 2), 0)

    lines = [
        {"kind": "category", "key": c["category"], "label": c["label"], "needed": max(round(c["monthly_target"] - c["spent"], 2), 0)}
        for c in status["categories"]
    ] + [
        {"kind": "goal", "key": g["slug"], "label": g["label"], "needed": max(round(min(g["monthly_contribution"], g["remaining"]), 2), 0)}
        for g in goals
    ]
    lines = [l for l in lines if l["needed"] > 0]

    total_needed = round(sum(l["needed"] for l in lines), 2)
    scale = 1.0 if total_needed <= available or total_needed == 0 else round(available / total_needed, 4)
    for l in lines:
        l["suggested"] = round(l["needed"] * scale, 2)

    reserved = round(sum(l["suggested"] for l in lines), 2)
    return {
        "ok": True, "current_balance": current_balance, "cushion_floor": cushion_floor,
        "available": available, "lines": lines, "reserved": reserved,
        "free": round(available - reserved, 2), "scaled": scale < 1.0,
    }


