"""
Lambda: POST /chat/message
Chat real con Gemini (tool-calling). El LLM entiende el mensaje en lenguaje
natural y decide que herramienta llamar -- pero las herramientas son las
MISMAS funciones verificadas de agent_actions.py que ya usa advance-day.
El LLM nunca ejecuta nada directo ni decide montos por su cuenta: cada tool
vuelve a verificar contra el estado real antes de actuar. Si el LLM alucina
una confirmacion que no existio, o pide un monto fuera de rango, la funcion
lo rechaza de todas formas -- la seguridad no depende de que el LLM se porte
bien, depende del codigo determinista debajo.
"""
import json
import os
import urllib.request
import urllib.error
from decimal import Decimal

import agent_actions as actions

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
# gemini-2.5-flash ya no esta disponible para keys nuevas, y gemini-3.6-flash
# (el que Google recomienda) tiene cuota gratuita de solo 20/dia -- este
# default tiene que ser un modelo que de verdad funcione hoy con la key del
# equipo, no el "sugerido", para que un redeploy sin la env var configurada
# no tumbe el chat completo el dia de la demo.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

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
    ]
}]

SYSTEM_INSTRUCTION = (
    "Eres el asistente de Centinel One, un agente financiero para Mia (freelancer, ingreso irregular, "
    "sin historial de credito). Reglas estrictas: "
    "1) Nunca inventes numeros de su cuenta -- si necesitas datos reales, llama a get_status primero. "
    "2) Nunca llames stop_subscription, move_to_savings o release_savings_buffer sin que el usuario lo haya pedido o confirmado explicitamente en la conversacion. "
    "3) Si detienes un cargo recurrente, siempre aclara que eso no cancela el contrato con el comercio, solo el cargo automatico. "
    "4) Si una herramienta rechaza la accion, explicale a Mia por que en lenguaje simple, no insistas ni la reintentes con otros valores. "
    "5) release_savings_buffer es para semanas de ingreso bajo -- no lo ofrezcas a menos que Mia mencione que le entro poco dinero o necesita liquidez extra. "
    "6) Se breve y claro, en español."
)


def sanitize(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


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
        with urllib.request.urlopen(req, timeout=25) as resp:
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
        return {"ok": False, "reason": f"Herramienta desconocida: {name}"}
    except Exception as e:
        return {"ok": False, "reason": f"Algo fallo revisando tu cuenta ({e}) -- no se ejecuto ninguna accion."}


def lambda_handler(event, context):
    body = json.loads(event.get("body") or "{}")
    user_message = body.get("message", "")
    user_id = body.get("user_id", "mia")

    if not user_message.strip():
        return _response(400, {"error": "Falta el campo 'message'."})

    contents = [{"role": "user", "parts": [{"text": user_message}]}]
    actions_taken = []

    for _ in range(4):
        try:
            result = call_gemini(contents)
        except Exception as e:
            status = 429 if "429" in str(e) else 200
            reply = "Centinel esta saturado ahorita mismo (limite de solicitudes), intenta de nuevo en un minuto." if status == 429 else f"No pude conectar con el modelo: {e}"
            return _response(status, {"reply": reply, "actions_taken": actions_taken})

        if "candidates" not in result or not result["candidates"]:
            return _response(200, {"reply": "El modelo no genero respuesta.", "actions_taken": actions_taken})

        candidate = result["candidates"][0]
        parts = candidate.get("content", {}).get("parts", [])
        function_call = next((p["functionCall"] for p in parts if "functionCall" in p), None)

        if not function_call:
            text = "".join(p.get("text", "") for p in parts) or "No tengo una respuesta clara para eso."
            return _response(200, {"reply": text, "actions_taken": actions_taken})

        contents.append({"role": "model", "parts": parts})
        tool_result = execute_tool(function_call["name"], function_call.get("args", {}), user_id)
        actions_taken.append({"tool": function_call["name"], "args": function_call.get("args", {}), "result": tool_result})
        contents.append({
            "role": "user",
            "parts": [{"functionResponse": {"name": function_call["name"], "response": tool_result}}],
        })

    return _response(200, {"reply": "Esto necesito mas pasos de los permitidos, intenta reformular tu mensaje.", "actions_taken": actions_taken})


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, ensure_ascii=False),
    }
