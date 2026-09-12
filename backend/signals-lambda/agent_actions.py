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
from decimal import Decimal
from boto3.dynamodb.conditions import Key

from signal_engine import compute_signals
from nessie_actions import stop_recurring_bill, sweep_to_savings

REGION = "us-east-1"
TABLE_NAME = "jarbis-financiero-data"
CHECKING_ID = "3cbe83c6-e844-48b3-b86a-627b8a6e3028"
SAVINGS_ID = "f9428a58-dbc4-49b3-9105-e69460a56a9a"
MAX_AUTONOMOUS_SAVINGS = 100  # tope de seguridad: el chat no puede mover mas que esto solo

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


def get_full_signals(deposits, purchases, bills_plain):
    total_income = sum(float(d["amount"]) for d in deposits)
    total_expense = sum(float(p["amount"]) for p in purchases)
    return compute_signals(deposits, purchases, bills_plain, total_income=total_income, total_expense=total_expense)


def get_current_signals(user_id="mia"):
    deposits, purchases, bills_plain = load_data(user_id)
    return get_full_signals(deposits, purchases, bills_plain)


def verified_stop_bill(user_id, bill_title):
    """Solo detiene un cargo si de verdad esta marcado como fuga ahora mismo.
    No confia en lo que diga el LLM sobre el bill -- lo vuelve a checar."""
    if not bill_title:
        return {"ok": False, "reason": "No especificaste que suscripcion detener."}

    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain)
    bill = next((b for b in bills_plain if b["payee"].lower() == bill_title.lower()), None)

    if not bill:
        return {"ok": False, "reason": f"No encontre ningun cargo llamado '{bill_title}'."}
    if bill["status"] != "recurring":
        return {"ok": False, "reason": f"'{bill_title}' ya no esta activo (estado actual: {bill['status']})."}
    is_leak = any(a["title"].lower() == bill_title.lower() for a in signals["alerts"])
    if not is_leak:
        return {"ok": False, "reason": f"'{bill_title}' no esta marcado como fuga en este momento -- no lo voy a detener sin una razon real detectada."}

    stop_recurring_bill(bill["bill_id"], bill["payee"], bill["payment_amount"])
    table.update_item(
        Key={"user_id": user_id, "sk": f"BILL#{bill['payee']}"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "cancelled"},
    )
    return {"ok": True, "amount": bill["payment_amount"], "message": f"Detuve el cargo automatico de {bill_title} (${bill['payment_amount']}/mes). Esto no cancela el contrato con el comercio, solo el cargo."}


def verified_move_to_savings(user_id, amount, reason):
    """Mueve dinero a ahorro solo si: el monto es razonable, y no hay una
    anomalia de gasto activa sin resolver."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "El monto no es un numero valido."}
    if amount <= 0:
        return {"ok": False, "reason": "El monto tiene que ser mayor a cero."}
    if amount > MAX_AUTONOMOUS_SAVINGS:
        return {"ok": False, "reason": f"${amount} es mas de lo que puedo mover de forma autonoma (limite ${MAX_AUTONOMOUS_SAVINGS}). Esto necesitaria una confirmacion adicional fuera del chat."}

    deposits, purchases, bills_plain = load_data(user_id)
    signals = get_full_signals(deposits, purchases, bills_plain)
    if signals["anomaly"]["detected"]:
        return {"ok": False, "reason": f"Pause esta accion por seguridad: {signals['anomaly']['reason']}"}

    sweep_to_savings(CHECKING_ID, SAVINGS_ID, amount, reason or "Ahorro solicitado por chat")
    table.put_item(Item=to_decimal({
        "user_id": user_id, "sk": f"TXN#2026-09-12#chat{int(time.time() * 1000)}",
        "type": "purchase", "date": "2026-09-12", "amount": amount,
        "category": "savings_transfer", "category_label": "Ahorro automático",
        "merchant_name": None, "description": reason or "Ahorro solicitado por chat",
    }))
    return {"ok": True, "amount": amount, "message": f"Mande ${amount} a tu ahorro. {reason or ''}".strip()}
