"""
Lambda: POST /chat/message
Chat real con Gemini (tool-calling) con memoria de conversacion real. El LLM
entiende el mensaje en lenguaje natural y decide que herramienta llamar --
pero las herramientas son las MISMAS funciones verificadas de
agent_actions.py que ya usa advance-day. El LLM nunca ejecuta nada directo
ni decide montos por su cuenta: cada tool vuelve a verificar contra el
estado real antes de actuar. Si el LLM alucina una confirmacion que no
existio, o pide un monto fuera de rango, la funcion lo rechaza de todas
formas -- la seguridad no depende de que el LLM se porte bien, depende del
codigo determinista debajo.

Memoria: se persiste en DynamoDB solo el intercambio visible (lo que Mia
escribio + la respuesta final de Centinel) -- no los pasos internos de que
herramienta se llamo. Cada mensaje nuevo reconstruye la conversacion con
ese historial antes de mandarla a Gemini.
"""
import json
import os
import urllib.request
import urllib.error
from decimal import Decimal

import agent_actions as actions

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
# gemini-2.5-flash ya no esta disponible para keys nuevas, y gemini-3.6-flash
# (el que Google recomienda) tenia cuota gratuita de solo 20/dia antes de
# activar billing -- este default tiene que ser un modelo que de verdad
# funcione hoy con la key del equipo, no el "sugerido a ciegas", para que un
# redeploy sin la env var configurada no tumbe el chat completo el dia de
# la demo.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

MAX_HISTORY_TURNS = 10  # pares (usuario, asistente) -- topa el tamaño y el costo por request

TOOLS = [{
    "functionDeclarations": [
        {
            "name": "get_status",
            "description": "Consulta el score de resiliencia financiera actual, las fugas detectadas y la liquidez de Mia. Usa esto siempre que necesites datos reales de su cuenta -- nunca inventes numeros.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "stop_subscription",
            "description": "Detiene el cargo automatico de una suscripcion/cargo recurrente. Solo tiene efecto si ese cargo esta marcado como fuga detectada ahora mismo -- si no lo esta, se rechaza automaticamente sin importar lo que el usuario diga.",
            "parameters": {
                "type": "object",
                "properties": {"bill_title": {"type": "string", "description": "Nombre exacto del comercio, ej. 'Gym Co'"}},
                "required": ["bill_title"],
            },
        },
        {
            "name": "move_to_savings",
            "description": "Mueve un monto de la cuenta corriente al ahorro. Se rechaza si el monto es mayor al limite autonomo permitido, si ya se movio el tope diario, si hay una anomalia de gasto sin resolver, o si dejaria el colchon de liquidez por debajo de una semana.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["amount", "reason"],
            },
        },
        {
            "name": "release_savings_buffer",
            "description": "Suavizado de ingreso irregular: libera parte de lo acumulado en ahorro de vuelta a la cuenta corriente, para una semana con ingreso mas bajo de lo normal. Se rechaza si no hay suficiente acumulado en el ahorro, si se paso el tope diario, o si hay una anomalia activa.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["amount", "reason"],
            },
        },
        {
            "name": "get_envelopes_status",
            "description": "Consulta los apartados de gastos fijos de Mia (ej. gasolina, comida) con su meta mensual y saldo acumulado actual.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "create_envelope",
            "description": "Crea un apartado nuevo para un gasto fijo mensual (ej. 'gasolina' con meta de $2000/mes). El monto se reparte proporcional cada vez que llega la nomina.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Nombre del gasto, ej. 'gasolina'"},
                    "monthly_target": {"type": "number", "description": "Meta mensual en pesos"},
                },
                "required": ["category", "monthly_target"],
            },
        },
        {
            "name": "set_income_pattern",
            "description": "Declara el patron de nomina de Mia (monto aproximado y frecuencia en dias) para que el sistema sepa distinguir su nomina de un deposito random (ej. un amigo mandandole dinero). Los apartados solo se reparten cuando un deposito coincide con este patron.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expected_amount": {"type": "number"},
                    "frequency_days": {"type": "integer", "description": "Cada cuantos dias le llega su nomina, ej. 15"},
                },
                "required": ["expected_amount", "frequency_days"],
            },
        },
        {
            "name": "confirm_pending_allocation",
            "description": "Ejecuta un reparto de nomina a apartados que quedo pendiente de confirmar porque hubiera dejado el colchon de liquidez muy bajo.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "get_upcoming_expenses",
            "description": "Consulta gastos recurrentes que se esperan pronto (ej. gasolina cada ~14 dias) segun la cadencia real observada en su historial -- no son montos inventados, se calculan de transacciones reales pasadas.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]
}]

SYSTEM_INSTRUCTION = (
    "Eres el asistente de Centinel One, un agente financiero para Mia (freelancer, ingreso irregular, "
    "sin historial de credito). Tienes memoria real de esta conversacion -- los mensajes anteriores estan "
    "incluidos abajo, usalos para entender referencias como 'eso' o 'el mismo monto'. Reglas estrictas: "
    "1) Nunca inventes numeros de su cuenta -- si necesitas datos reales, llama a get_status primero. "
    "2) Nunca llames stop_subscription, move_to_savings o release_savings_buffer sin que el usuario lo haya pedido o confirmado explicitamente en la conversacion. "
    "3) Si detienes un cargo recurrente, siempre aclara que eso no cancela el contrato con el comercio, solo el cargo automatico. "
    "4) Si una herramienta rechaza la accion, explicale a Mia por que en lenguaje simple, no insistas ni la reintentes con otros valores. "
    "5) release_savings_buffer es para semanas de ingreso bajo -- no lo ofrezcas a menos que Mia mencione que le entro poco dinero o necesita liquidez extra. "
    "6) Los apartados (create_envelope) se reparten solos cuando llega un deposito que coincide con el patron de nomina declarado (set_income_pattern) -- si Mia no ha declarado su patron todavia y quiere crear un apartado, pidele primero el monto y frecuencia aproximada de su nomina. "
    "7) Se breve y claro, en español."
)


def sanitize(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


def get_chat_history(user_id):
    """Solo el intercambio visible (usuario + respuesta final) -- nunca los
    pasos internos de tool-calling, no hace falta guardarlos para que la
    conversacion tenga memoria real."""
    resp = actions.table.get_item(Key={"user_id": user_id, "sk": "CHAT_HISTORY"})
    item = resp.get("Item")
    if not item or "contents" not in item:
        return []
    return sanitize(item["contents"])


def save_chat_history(user_id, contents):
    trimmed = contents[-(MAX_HISTORY_TURNS * 2):]
    actions.table.put_item(Item={"user_id": user_id, "sk": "CHAT_HISTORY", "contents": trimmed})


def call_gemini(contents):
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": contents,
        "tools": TOOLS,
    }
    req = urllib.request.Request(
        GEMINI_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        # Mas corto que el timeout del propio Lambda (ver Timeout en la
        # config de jarbis-financiero-chat) -- si son iguales, Lambda mata
        # la ejecucion antes de que este try/except alcance a capturar nada,
        # y el usuario recibe un timeout crudo de API Gateway en vez de un
        # mensaje amigable.
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini HTTP {e.code}: {e.read().decode()}")


def execute_tool(name, args, user_id):
    try:
        if name == "get_status":
            return sanitize(actions.get_current_signals(user_id))
        if name == "stop_subscription":
            return sanitize(actions.verified_stop_bill(user_id, args.get("bill_title", "")))
        if name == "move_to_savings":
            return sanitize(actions.verified_move_to_savings(user_id, args.get("amount"), args.get("reason", "")))
        if name == "release_savings_buffer":
            return sanitize(actions.verified_release_buffer(user_id, args.get("amount"), args.get("reason", "")))
        if name == "get_envelopes_status":
            return sanitize({"envelopes": actions.get_envelope_balances(user_id)})
        if name == "create_envelope":
            return sanitize(actions.create_envelope(user_id, args.get("category", ""), args.get("monthly_target")))
        if name == "set_income_pattern":
            return sanitize(actions.set_income_pattern(user_id, args.get("expected_amount"), args.get("frequency_days")))
        if name == "confirm_pending_allocation":
            return sanitize(actions.confirm_pending_allocation(user_id))
        if name == "get_upcoming_expenses":
            return sanitize({"upcoming_expenses": actions.get_current_signals(user_id)["upcoming_expenses"]})
        return {"ok": False, "reason": f"Herramienta desconocida: {name}"}
    except Exception as e:
        return {"ok": False, "reason": f"Algo fallo revisando tu cuenta ({e}) -- no se ejecuto ninguna accion."}


def lambda_handler(event, context):
    body = json.loads(event.get("body") or "{}")
    user_message = body.get("message", "")
    user_id = body.get("user_id", "mia")

    if not user_message.strip():
        return _response(400, {"error": "Falta el campo 'message'."})

    history = get_chat_history(user_id)
    contents = history + [{"role": "user", "parts": [{"text": user_message}]}]
    actions_taken = []
    visible_reply = None

    for _ in range(4):
        try:
            result = call_gemini(contents)
        except Exception as e:
            status = 429 if "429" in str(e) else 200
            reply = "Centinel esta saturado ahorita mismo (limite de solicitudes), intenta de nuevo en un minuto." if status == 429 else f"No pude conectar con el modelo: {e}"
            # No se persiste: un mensaje que nunca se proceso no debe contaminar la memoria de la conversacion.
            return _response(status, {"reply": reply, "actions_taken": actions_taken})

        if "candidates" not in result or not result["candidates"]:
            return _response(200, {"reply": "El modelo no genero respuesta.", "actions_taken": actions_taken})

        candidate = result["candidates"][0]
        parts = candidate.get("content", {}).get("parts", [])
        function_call = next((p["functionCall"] for p in parts if "functionCall" in p), None)

        if not function_call:
            visible_reply = "".join(p.get("text", "") for p in parts) or "No tengo una respuesta clara para eso."
            break

        contents.append({"role": "model", "parts": parts})
        tool_result = execute_tool(function_call["name"], function_call.get("args", {}), user_id)
        actions_taken.append({"tool": function_call["name"], "args": function_call.get("args", {}), "result": tool_result})
        contents.append({
            "role": "user",
            "parts": [{"functionResponse": {"name": function_call["name"], "response": tool_result}}],
        })
    else:
        visible_reply = "Esto necesito mas pasos de los permitidos, intenta reformular tu mensaje."

    save_chat_history(user_id, history + [
        {"role": "user", "parts": [{"text": user_message}]},
        {"role": "model", "parts": [{"text": visible_reply}]},
    ])
    return _response(200, {"reply": visible_reply, "actions_taken": actions_taken})


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, ensure_ascii=False),
    }
