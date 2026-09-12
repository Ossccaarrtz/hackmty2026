"""
Lambda: POST /simulation/advance-day
El "avanzar dia" de la demo: mueve un checkpoint a la vez por la historia de
Mia, aplicando la politica de riesgo (bills = siempre pregunta; ahorro =
autonomo) y la verificacion antes de ejecutar cualquier escritura real en
Nessie. Guarda el estado y el log de acciones en DynamoDB.
"""
import json
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

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

# Checkpoints de la demo: cada uno es un "dia" que se puede mostrar en vivo.
# Las fechas caen dentro de los 90 dias ya sembrados (2026-06-14 a 2026-09-12).
CHECKPOINTS = [
    {"date": "2026-07-29", "label": "Dia 45"},
    {"date": "2026-08-15", "label": "Dia 62"},
    {"date": "2026-08-16", "label": "Dia 63"},
    {"date": "2026-09-12", "label": "Dia 90"},
]


def to_decimal(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_decimal(v) for v in obj]
    return obj


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def get_state(user_id):
    resp = table.get_item(Key={"user_id": user_id, "sk": "STATE#simulation"})
    return resp.get("Item", {"checkpoint_idx": -1, "gym_bill_stopped": False})


def save_state(user_id, state):
    table.put_item(Item=to_decimal({"user_id": user_id, "sk": "STATE#simulation", **state}))


def log_action(user_id, action):
    action_id = f"ACTION#{action['date']}#{int(time.time() * 1000)}"
    table.put_item(Item=to_decimal({"user_id": user_id, "sk": action_id, **action}))
    return action


def load_data(user_id):
    resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id))
    items = resp["Items"]
    deposits = [i for i in items if i.get("type") == "deposit"]
    purchases = [i for i in items if i.get("type") == "purchase"]
    bills = [i for i in items if i.get("type") == "bill"]
    return deposits, purchases, bills


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    user_id = params.get("user_id", "mia")

    if params.get("reset") == "true":
        table.delete_item(Key={"user_id": user_id, "sk": "STATE#simulation"})
        try:
            resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id))
            for item in resp["Items"]:
                if item.get("category") == "savings_transfer" or item["sk"].startswith("ACTION#") or item["sk"].startswith("NOTIFICATION#"):
                    table.delete_item(Key={"user_id": user_id, "sk": item["sk"]})
        except Exception:
            pass
        try:
            _, _, bills = load_data(user_id)
            gym = next((b for b in bills if b["payee"] == "Gym Co"), None)
            if gym:
                from nessie_actions import _request
                _request("PUT", f"/bills/{gym['bill_id']}", {
                    "status": "recurring", "payee": "Gym Co", "nickname": "Gym membership",
                    "payment_date": "2026-09-07", "recurring_date": 5, "payment_amount": float(gym["payment_amount"]),
                })
                table.update_item(
                    Key={"user_id": user_id, "sk": "BILL#Gym Co"},
                    UpdateExpression="SET #s = :s",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "recurring"},
                )
        except Exception:
            pass
        return _response(200, {"reset": True, "message": "Simulacion reiniciada a Dia 0. Bill de Gym Co reactivado en Nessie para poder re-ensayar."})

    state = get_state(user_id)
    idx = int(state["checkpoint_idx"]) + 1

    if idx >= len(CHECKPOINTS):
        return _response(200, {"done": True, "message": "No hay mas dias que avanzar en esta demo."})

    checkpoint = CHECKPOINTS[idx]
    as_of = checkpoint["date"]

    deposits, purchases, bills = load_data(user_id)
    bills_plain = [{"payee": b["payee"], "status": b["status"], "payment_amount": float(b["payment_amount"]), "bill_id": b["bill_id"]} for b in bills]

    signals = compute_signals(deposits, purchases, bills_plain, as_of_date=as_of)
    new_actions = []
    gym_bill = next((b for b in bills_plain if b["payee"] == "Gym Co"), None)

    if idx == 0:
        # Dia 45: solo observacion, no hay ejecucion.
        new_actions.append(log_action(user_id, {
            "date": as_of, "type": "score_check", "requires_confirmation": False,
            "text": f"Revise tu cuenta: tu score de hoy es {signals['score']['value']}/100.",
        }))

    elif idx == 1:
        # Dia 62: se detecta la fuga. Politica de riesgo: NUNCA se ejecuta solo, siempre se pregunta.
        leak_detected = any(a["title"] == "Gym Co" for a in signals["alerts"])
        if leak_detected:
            new_actions.append(log_action(user_id, {
                "date": as_of, "type": "leak_detected", "requires_confirmation": True,
                "bill_id": gym_bill["bill_id"],
                "text": "Detecte que Gym Co ($40/mes) no tiene actividad relacionada hace 60 dias. "
                        "¿La sigues usando? Si tiene contrato anual, cancelar antes de tiempo podria "
                        "generarte una penalizacion o mandarte a cobranza -- confirmalo antes de que lo detengamos.",
            }))

    elif idx == 2:
        # Dia 63: el humano ya confirmo (asumido en la demo) -> verificacion -> ejecuta de verdad.
        # Verificacion: re-confirma que el bill sigue existiendo y sigue siendo el que se marco como fuga.
        if gym_bill and gym_bill["status"] == "recurring":
            try:
                stop_recurring_bill(gym_bill["bill_id"], "Gym Co", gym_bill["payment_amount"])
                table.update_item(
                    Key={"user_id": user_id, "sk": "BILL#Gym Co"},
                    UpdateExpression="SET #s = :s",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "cancelled"},
                )
                state["gym_bill_stopped"] = True
                new_actions.append(log_action(user_id, {
                    "date": as_of, "type": "bill_stopped", "requires_confirmation": False,
                    "amount": gym_bill["payment_amount"],
                    "text": "Confirmaste que ya no usas el gimnasio -- detuve el cargo automatico de Gym Co ($40/mes).",
                }))
            except Exception as e:
                new_actions.append(log_action(user_id, {
                    "date": as_of, "type": "error", "requires_confirmation": False,
                    "text": f"No se pudo detener el cargo en Nessie: {e}",
                }))
        else:
            new_actions.append(log_action(user_id, {
                "date": as_of, "type": "verification_blocked", "requires_confirmation": False,
                "text": "Verificacion fallida: el bill ya no coincide con lo detectado, no se ejecuta nada por seguridad.",
            }))

    elif idx == 3:
        # Dia 90: accion autonoma -- mover a ahorro el dinero liberado de la fuga.
        # Verificacion 1: solo se ejecuta SI el bill realmente se detuvo antes (dependencia causal real).
        # Verificacion 2 (guardrail de anomalia): si el gasto reciente se ve muy fuera de lo normal
        # para esta persona, NO se actua solo -- se escala a confirmacion humana en su lugar.
        if state.get("gym_bill_stopped"):
            amount = gym_bill["payment_amount"] if gym_bill else 40
            if signals["anomaly"]["detected"]:
                new_actions.append(log_action(user_id, {
                    "date": as_of, "type": "anomaly_pause", "requires_confirmation": True,
                    "text": f"Iba a mover ${amount} a tu ahorro, pero pause la accion: {signals['anomaly']['reason']} "
                            "¿confirmas que todo esta bien antes de que lo mueva?",
                }))
            else:
                try:
                    sweep_to_savings(CHECKING_ID, SAVINGS_ID, amount, "Ahorro automatico - fuga de Gym Co resuelta")
                    table.put_item(Item=to_decimal({
                        "user_id": user_id, "sk": f"TXN#{as_of}#sweep{int(time.time() * 1000)}",
                        "type": "purchase", "date": as_of, "amount": amount,
                        "category": "savings_transfer", "category_label": "Ahorro automático",
                        "merchant_name": None, "description": "Ahorro automatico - fuga de Gym Co resuelta",
                    }))
                    new_actions.append(log_action(user_id, {
                        "date": as_of, "type": "savings_moved", "requires_confirmation": False,
                        "amount": amount,
                        "text": f"Como ya no pagas el gimnasio, mande ${amount} a tu ahorro -- ese dinero ya no lo necesitas para gastos fijos.",
                    }))
                except Exception as e:
                    new_actions.append(log_action(user_id, {
                        "date": as_of, "type": "error", "requires_confirmation": False,
                        "text": f"No se pudo mover el dinero en Nessie: {e}",
                    }))
        else:
            new_actions.append(log_action(user_id, {
                "date": as_of, "type": "info", "requires_confirmation": False,
                "text": "No hay excedente para mover a ahorro todavia (la fuga del gimnasio sigue sin resolverse).",
            }))

    state["checkpoint_idx"] = idx
    save_state(user_id, state)

    return _response(200, {
        "date": as_of, "label": checkpoint["label"], "done": False,
        "score": signals["score"], "alerts": signals["alerts"],
        "new_actions": new_actions,
    })


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, default=decimal_default),
    }
