"""
Lambda: POST /simulation/advance-day
El "avanzar dia" de la demo: mueve un checkpoint a la vez por la historia de
Ana, aplicando la politica de riesgo (bills = siempre pregunta; ahorro =
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
from datetime import date
from decimal import Decimal
from boto3.dynamodb.conditions import Key

from signal_engine import compute_totals
import agent_actions as actions
import email_notifications

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
    return resp.get("Item", {"checkpoint_idx": -1})


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


def _send_upcoming_expense_reminders(signals):
    """Un correo por cada gasto recurrente esperado (renta, gasolina, etc.
    -- inferido de tu propia cadencia de compras, NO suscripciones) que
    caiga a 5, 3 o 1 dia de distancia. Es solo un aviso para que tengas
    fondos listos -- estos gastos no son cancelables desde el chat, a
    diferencia de una suscripcion marcada como fuga (ver
    _send_leak_reminder)."""
    for item in signals.get("upcoming_expenses", []):
        days = item.get("days_until")
        if days not in (5, 3, 1):
            continue
        plural = "día" if days == 1 else "días"
        if days == 1:
            headline, vibe = "⏳ Mañana toca este gasto", "Checa que tengas fondos listos para que no te tome en curva."
        elif days == 3:
            headline, vibe = "📅 Se acerca la fecha", "Vas a tiempo, solo es para que lo tengas en el radar."
        else:
            headline, vibe = "👀 Ojo con esto", "Te avisamos con tiempo -- todavia falta, pero mejor prevenir."
        email_notifications.send_email(
            subject=f"{headline}: {item['category_label']} en {days} {plural}",
            html=email_notifications.wrap(f"""
                <p style="font-size:19px;font-weight:700;margin:0 0 14px;">{headline}</p>
                <p>Segun tu propio historial, en <strong>{days} {plural}</strong> esperamos que gastes
                   <strong style="color:#d22630;">~${item['expected_amount']:.2f}</strong> en
                   <strong>{item['category_label']}</strong>. 💸</p>
                <p>{vibe}</p>
                <p>Esto es solo un heads-up -- Kivo no lo va a pagar ni a detener por ti, nomas te
                   ayuda a que no te agarre desprevenido. 🙌</p>
                <p style="margin-bottom:0;">— El equipo de Kivo 💙</p>
            """),
        )


def _send_leak_reminder(leak_alert):
    """Se manda cuando una suscripcion se marca como fuga (sin actividad
    relacionada hace 60+ dias) -- a diferencia de upcoming_expenses, esto
    SI es una suscripcion real (tiene payee/bill_id) y SI se puede pedir
    que Kivo deje de contarla via chat ("cancela X"), asi que el CTA
    aplica de verdad."""
    email_notifications.send_email(
        subject=f"🕵️ ¿Sigues pagando {leak_alert['title']}?",
        html=email_notifications.wrap(f"""
            <p style="font-size:19px;font-weight:700;margin:0 0 14px;">🕵️ Esto huele a fuga</p>
            <p>Llevamos <strong>60+ dias</strong> sin ver actividad relacionada con
               <strong>{leak_alert['title']}</strong> (<strong style="color:#d22630;">${leak_alert['monthly_amount']:.2f}/mes</strong>). 💸</p>
            <p>¿Todavia lo usas o ya nomas esta ahi cobrando polvo? 🤔</p>
            <p style="background:#eef4fb;border-radius:12px;padding:14px 18px;margin:18px 0;">
              Si ya no, dile a Kivo en el chat:<br>
              <strong>"cancela {leak_alert['title']}"</strong><br>
              <span style="font-size:13px;color:#5c6d83;">Ojo: esto hace que Kivo deje de contarlo en tu score y
              tus recordatorios -- si el cargo real sigue activo con el comercio, cancelalo tu directamente con ellos.</span>
            </p>
            <p style="margin-bottom:0;">— El equipo de Kivo 💙</p>
        """),
    )


def _send_external_expense_reminder(purchases):
    """Si hoy (fecha real, no la del checkpoint simulado -- asi es como
    log_external_expense ya guarda estos gastos) no se registro en el chat
    ningun gasto en efectivo/otra tarjeta, manda un recordatorio: sin esto,
    el score y el presupuesto solo ven la fraccion de la vida financiera
    real que paso por la tarjeta del banco."""
    today = date.today().isoformat()
    logged_today = any(
        p.get("date") == today and p.get("sk", "").split("#")[2].startswith("external")
        for p in purchases if p.get("sk", "").count("#") >= 2
    )
    if not logged_today:
        email_notifications.send_email(
            subject="🕵️ ¿Gastaste algo hoy que Kivo no vio?",
            html=email_notifications.wrap("""
                <p style="font-size:19px;font-weight:700;margin:0 0 14px;">🕵️ Modo detective activado</p>
                <p>Hoy no nos platicaste de ningún gasto en efectivo o con otra tarjeta. 👀</p>
                <p>Si te echaste un taco, un Uber, o pagaste algo que no fue con tu tarjeta del
                   banco, cuéntaselo a Kivo para que tu score no se quede con información a medias. 📊</p>
                <p style="background:#eef4fb;border-radius:12px;padding:14px 18px;margin:18px 0;">
                  Solo dile algo como:<br>
                  <strong>"gasté 80 en efectivo en comida"</strong> y Kivo lo apunta por ti. ✍️
                </p>
                <p>Entre más completo esté tu historial, mejor te conocemos (y mejor te ayudamos
                   a no gastar de más). 💪</p>
                <p style="margin-bottom:0;">— Kivo</p>
            """),
        )


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    user_id = params.get("user_id", "ana")

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
    # La fuga se busca dinamicamente en las alertas reales, NUNCA por un
    # nombre de comercio hardcodeado -- un literal como "Gym Co" solo
    # funciona para la persona que existia cuando se escribio esto.
    # Encontrado como parte del mismo problema que ya causo un bug real en
    # evaluate_bills (ver signal_engine.py): cualquier hardcodeo atado a
    # una sola persona sembrada rompe en cuanto cambia la persona activa.
    leak_alert = next((a for a in signals["alerts"] if a["type"] == "leak"), None)

    if idx == 0:
        # Dia 45: solo observacion, no hay ejecucion.
        new_actions.append(log_action(user_id, {
            "date": as_of, "type": "score_check", "requires_confirmation": False,
            "text": f"Revise tu cuenta: tu score de hoy es {signals['score']['value']}/100.",
        }))

    elif idx == 1:
        # Dia 62: se detecta la fuga. Politica de riesgo: NUNCA se ejecuta solo, siempre se pregunta.
        if leak_alert:
            new_actions.append(log_action(user_id, {
                "date": as_of, "type": "leak_detected", "requires_confirmation": True,
                "bill_id": leak_alert["id"],
                "text": f"Detecte que {leak_alert['title']} (${leak_alert['monthly_amount']}/mes) no tiene actividad relacionada hace 60 dias. "
                        "¿La sigues usando? Si tiene contrato anual, cancelar antes de tiempo podria "
                        "generarte una penalizacion o mandarte a cobranza -- confirmalo antes de que lo detengamos.",
            }))
            _send_leak_reminder(leak_alert)

    elif idx == 2:
        # Dia 63: el humano ya confirmo (asumido en la demo) -> reusa la MISMA
        # verificacion que el chat -- re-checa que de verdad sea una fuga antes de tocar Nessie.
        if not leak_alert:
            new_actions.append(log_action(user_id, {
                "date": as_of, "type": "info", "requires_confirmation": False,
                "text": "No hay ninguna fuga activa que confirmar en este momento.",
            }))
        else:
            payee = leak_alert["title"]
            result = actions.verified_stop_bill(user_id, payee)
            if result["ok"]:
                new_actions.append(log_action(user_id, {
                    "date": as_of, "type": "bill_stopped", "requires_confirmation": False,
                    "amount": result["amount"],
                    "text": f"Confirmaste que ya no usas {payee} -- detuve el cargo automatico (${result['amount']}/mes).",
                }))
            else:
                new_actions.append(log_action(user_id, {
                    "date": as_of, "type": "verification_blocked", "requires_confirmation": False,
                    "text": f"No se detuvo el cargo: {result['reason']}",
                }))

    elif idx == 3:
        # Dia 90: accion autonoma -- mover a ahorro el dinero liberado de la
        # fuga resuelta en el checkpoint anterior. Se busca cualquier bill
        # que ya no este "recurring" (lo detuvo este mismo flujo) y se lee
        # su payment_amount real -- nunca un monto fijo atado a una persona.
        # verified_move_to_savings ya revisa: monto razonable, tope diario,
        # anomalia activa, Y que el colchon de liquidez no quede por debajo
        # del minimo de seguridad despues del movimiento.
        stopped_bill = next((b for b in bills_plain if b["status"] != "recurring"), None)
        if stopped_bill:
            amount = float(stopped_bill["payment_amount"])
            result = actions.verified_move_to_savings(user_id, amount, f"Ahorro automatico - fuga de {stopped_bill['payee']} resuelta")
            if result["ok"]:
                new_actions.append(log_action(user_id, {
                    "date": as_of, "type": "savings_moved", "requires_confirmation": False,
                    "amount": result["amount"],
                    "text": f"Como ya no pagas {stopped_bill['payee']}, mande ${result['amount']} a tu ahorro -- ese dinero ya no lo necesitas para gastos fijos.",
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
                "text": "No hay excedente para mover a ahorro todavia (la fuga detectada sigue sin resolverse).",
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

    _send_upcoming_expense_reminders(signals)
    _send_external_expense_reminder(purchases)

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
            # en el historial de la persona.
            if (category in ("savings_transfer", "savings_release", "income_third_party_demo")
                    or category.startswith("envelope:")
                    or item["sk"].startswith("ACTION#") or item["sk"].startswith("NOTIFICATION#")):
                table.delete_item(Key={"user_id": user_id, "sk": item["sk"]})
    except Exception:
        pass
    try:
        _, _, bills = load_data(user_id)
        # Reactiva cualquier bill que este flujo haya detenido -- nunca un
        # nombre hardcodeado como "Gym Co", que solo aplicaba a una persona
        # especifica y rompe el reset para cualquier otra.
        cancelled_bill = next((b for b in bills if b.get("status") == "cancelled"), None)
        if cancelled_bill:
            payee = cancelled_bill["payee"]
            from nessie_actions import _request
            _request("PUT", f"/bills/{cancelled_bill['bill_id']}", {
                "status": "recurring", "payee": payee, "nickname": payee,
                "payment_date": "2026-09-07", "recurring_date": 5, "payment_amount": float(cancelled_bill["payment_amount"]),
            })
            table.update_item(
                Key={"user_id": user_id, "sk": f"BILL#{payee}"},
                UpdateExpression="SET #s = :s",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "recurring"},
            )
    except Exception:
        pass
    return _response(200, {"reset": True, "message": "Simulacion reiniciada a Dia 0. Bill de fuga reactivado en Nessie para poder re-ensayar."})


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, default=decimal_default),
    }
