"""
Lambda: POST /simulation/advance-day
El "avanzar dia" de la demo: mueve un checkpoint a la vez por la historia de
Mia, aplicando la politica de riesgo (bills = siempre pregunta; ahorro =
autonomo) y la verificacion antes de ejecutar cualquier escritura real en
Nessie. Guarda el estado y el log de acciones en DynamoDB.

La ejecucion de acciones reutiliza las mismas funciones verificadas de
agent_actions.py que usa el chat -- un solo lugar decide si una accion
procede o no, sin importar si la disparo un checkpoint programado o un
mensaje de texto libre.
"""
import json
import time
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal
from boto3.dynamodb.conditions import Key

from signal_engine import compute_totals
import agent_actions as actions

REGION = "us-east-1"
TABLE_NAME = "jarbis-financiero-data"

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


def claim_checkpoint(user_id, expected_old_idx, new_idx):
    """Escritura condicional atomica: si dos invocaciones concurrentes (doble
    click nervioso, reintento por red lenta) intentan avanzar el mismo
    checkpoint, solo UNA gana la condicion -- la otra recibe False y no
    ejecuta ninguna accion. Sin esto, un click doble en el checkpoint de
    ahorro podria mover el dinero dos veces de verdad en Nessie."""
    try:
        table.update_item(
            Key={"user_id": user_id, "sk": "STATE#simulation"},
            UpdateExpression="SET checkpoint_idx = :new",
            ConditionExpression="attribute_not_exists(checkpoint_idx) OR checkpoint_idx = :old",
            ExpressionAttributeValues={":new": new_idx, ":old": expected_old_idx},
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


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
        return _handle_reset(user_id)

    state = get_state(user_id)
    idx = int(state["checkpoint_idx"]) + 1

    if idx >= len(CHECKPOINTS):
        return _response(200, {"done": True, "message": "No hay mas dias que avanzar en esta demo."})

    if not claim_checkpoint(user_id, idx - 1, idx):
        return _response(200, {"retry": True, "message": "Ya se esta procesando este paso (otra solicitud llego al mismo tiempo) -- intenta de nuevo en un momento."})

    checkpoint = CHECKPOINTS[idx]
    as_of = checkpoint["date"]

    deposits, purchases, bills = load_data(user_id)
    bills_plain = [{"payee": b["payee"], "status": b["status"], "payment_amount": float(b["payment_amount"]), "bill_id": b["bill_id"]} for b in bills]

    signals = actions.get_full_signals(deposits, purchases, bills_plain, user_id=user_id, as_of_date=as_of)
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
        # Dia 63: el humano ya confirmo (asumido en la demo) -> reusa la MISMA
        # verificacion que el chat -- re-checa que de verdad sea una fuga antes de tocar Nessie.
        result = actions.verified_stop_bill(user_id, "Gym Co")
        if result["ok"]:
            new_actions.append(log_action(user_id, {
                "date": as_of, "type": "bill_stopped", "requires_confirmation": False,
                "amount": result["amount"],
                "text": "Confirmaste que ya no usas el gimnasio -- detuve el cargo automatico de Gym Co ($40/mes).",
            }))
        else:
            new_actions.append(log_action(user_id, {
                "date": as_of, "type": "verification_blocked", "requires_confirmation": False,
                "text": f"No se detuvo el cargo: {result['reason']}",
            }))

    elif idx == 3:
        # Dia 90: accion autonoma -- mover a ahorro el dinero liberado de la fuga.
        # verified_move_to_savings ya revisa: monto razonable, tope diario,
        # anomalia activa, Y que el colchon de liquidez no quede por debajo
        # del minimo de seguridad despues del movimiento.
        gym_was_stopped = gym_bill is None or gym_bill["status"] != "recurring"
        if gym_was_stopped:
            amount = 40  # lo que se libera mensualmente al dejar de pagar el gimnasio
            result = actions.verified_move_to_savings(user_id, amount, "Ahorro automatico - fuga de Gym Co resuelta")
            if result["ok"]:
                new_actions.append(log_action(user_id, {
                    "date": as_of, "type": "savings_moved", "requires_confirmation": False,
                    "amount": result["amount"],
                    "text": f"Como ya no pagas el gimnasio, mande ${result['amount']} a tu ahorro -- ese dinero ya no lo necesitas para gastos fijos.",
                }))
            elif "anomalia" in result["reason"].lower() or "Pause" in result["reason"]:
                new_actions.append(log_action(user_id, {
                    "date": as_of, "type": "anomaly_pause", "requires_confirmation": True,
                    "text": f"Iba a mover ${amount} a tu ahorro, pero {result['reason']} ¿confirmas que todo esta bien antes de que lo mueva?",
                }))
            else:
                new_actions.append(log_action(user_id, {
                    "date": as_of, "type": "info", "requires_confirmation": False,
                    "text": result["reason"],
                }))
        else:
            new_actions.append(log_action(user_id, {
                "date": as_of, "type": "info", "requires_confirmation": False,
                "text": "No hay excedente para mover a ahorro todavia (la fuga del gimnasio sigue sin resolverse).",
            }))

    if idx in (2, 3):
        # Dia 63 y Dia 90 ejecutan una accion real (stop_bill / move_to_savings)
        # que cambia el estado -- si devolvemos el "signals" calculado al
        # principio de este mismo request, el score/alertas que ve el
        # frontend quedan desfasados un paso respecto al "new_actions" que
        # ya narra la accion como hecha (ej. alerta de fuga todavia activa
        # junto con el texto "ya detuve el cargo"). Se recalcula sobre el
        # estado ya escrito antes de responder.
        deposits, purchases, bills = load_data(user_id)
        bills_plain = [{"payee": b["payee"], "status": b["status"], "payment_amount": float(b["payment_amount"]), "bill_id": b["bill_id"]} for b in bills]
        signals = actions.get_full_signals(deposits, purchases, bills_plain, user_id=user_id, as_of_date=as_of)

    state["checkpoint_idx"] = idx
    save_state(user_id, state)

    return _response(200, {
        "date": as_of, "label": checkpoint["label"], "done": False,
        "score": signals["score"], "alerts": signals["alerts"],
        "new_actions": new_actions,
    })


def _handle_reset(user_id):
    table.delete_item(Key={"user_id": user_id, "sk": "STATE#simulation"})
    table.delete_item(Key={"user_id": user_id, "sk": "CHAT_HISTORY"})
    table.delete_item(Key={"user_id": user_id, "sk": "PENDING_STOP_BILL"})
    table.delete_item(Key={"user_id": user_id, "sk": "PENDING_ALLOCATION"})
    try:
        resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id))
        for item in resp["Items"]:
            category = item.get("category") or ""
            # savings_transfer/savings_release: sweeps de suavizado de ingreso.
            # envelope:*: repartos a apartados ya ejecutados.
            # income_third_party_demo: depositos de la demo de nomina de un
            # tercero (backend/signals-lambda/agent_actions.py::simulate_third_party_payroll)
            # -- sin esto, cada ensayo en vivo deja un deposito extra permanente
            # en el historial de Mia.
            if (category in ("savings_transfer", "savings_release", "income_third_party_demo")
                    or category.startswith("envelope:")
                    or item["sk"].startswith("ACTION#") or item["sk"].startswith("NOTIFICATION#")):
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


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, default=decimal_default),
    }
