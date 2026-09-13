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
import re
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
            "name": "get_score_history",
            "description": "Consulta el historial real de scores de Mia, un punto por cada fecha en la que se calculo su score (no es diario, son los checkpoints/consultas reales que han ocurrido). Usa esto cuando Mia pregunte por su score en un mes o fecha pasada especifica, por ejemplo '¿como estaba mi score en septiembre?' o '¿cual era mi score hace dos semanas?'. Razona tu mismo sobre la lista de puntos {date, value} que te regresa para responder -- nunca inventes un valor para una fecha que no este en la lista, y aclarale a Mia si no hay ningun punto registrado en el rango que pregunto.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "stop_subscription",
            "description": "Propone detener el cargo automatico de una suscripcion/cargo recurrente -- NUNCA ejecuta en esta llamada, solo evalua si es una fuga real y deja la propuesta pendiente. Solo procede si ese cargo esta marcado como fuga detectada ahora mismo -- si no lo esta, se rechaza automaticamente sin importar lo que el usuario diga. Para ejecutar de verdad, el usuario tiene que confirmar explicitamente despues y tienes que llamar confirm_stop_bill.",
            "parameters": {
                "type": "object",
                "properties": {"bill_title": {"type": "string", "description": "Nombre exacto del comercio, ej. 'Gym Co'"}},
                "required": ["bill_title"],
            },
        },
        {
            "name": "confirm_stop_bill",
            "description": "Ejecuta de verdad una cancelacion de cargo que quedo pendiente de stop_subscription, despues de que el usuario confirmo explicitamente en la conversacion que quiere proceder.",
            "parameters": {"type": "object", "properties": {}},
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
        {
            "name": "simulate_decision",
            "description": "Simulador educativo: responde '¿que pasaria con mi score si...?' sin ejecutar nada real -- ni Nessie ni la cuenta se tocan, es puramente una proyeccion. Usa esto cuando el usuario pregunte hipoteticamente por el impacto de una decision antes de tomarla, por ejemplo '¿que pasa si dejo el gimnasio?' o '¿como me ayudaria gastar 200 menos al mes en salidas?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "'stop_bill' para simular dejar de pagar un cargo recurrente, o 'reduce_discretionary' para simular reducir gasto discrecional mensual."},
                    "bill_payee": {"type": "string", "description": "Nombre exacto del cargo a simular deteniendo, solo si action es 'stop_bill', ej. 'Gym Co'."},
                    "monthly_amount": {"type": "number", "description": "Monto mensual a simular reduciendo, solo si action es 'reduce_discretionary'."},
                },
                "required": ["action"],
            },
        },
        {
            "name": "get_financial_lesson",
            "description": "Identifica el factor mas debil del score actual del usuario y explica por que le pesa, con sus propios numeros reales -- usa esto cuando el usuario pregunte algo general como '¿como puedo mejorar mi score?' o '¿en que estoy fallando?', antes de sugerir una simulacion especifica con simulate_decision.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]
}]

SYSTEM_INSTRUCTION = (
    "Eres el asistente de Centinel One, un agente financiero para Mia (freelancer, ingreso irregular, "
    "sin historial de credito). Tienes memoria real de esta conversacion -- los mensajes anteriores estan "
    "incluidos abajo, usalos para entender referencias como 'eso' o 'el mismo monto'. Reglas estrictas: "
    "1) Nunca inventes NI DERIVES numeros de su cuenta que no aparezcan literalmente en el resultado de una herramienta -- si necesitas datos reales, llama a get_status primero, o a get_score_history si pregunta por una fecha o mes pasado. Si una herramienta no te da un monto especifico (ej. solo te da el costo anual de una fuga, no el mensual), NO calcules ni asumas ese monto -- di explicitamente que no tienes ese dato exacto en vez de inventar una cifra que suene razonable. "
    "2) Nunca llames move_to_savings o release_savings_buffer sin que el usuario lo haya pedido o confirmado explicitamente en la conversacion. "
    "3) stop_subscription SIEMPRE es un proceso de dos pasos: la primera llamada solo propone (nunca detiene nada de verdad) y te va a devolver una advertencia de riesgo contractual para que se la muestres a Mia tal cual. Si Mia confirma explicitamente despues de leer esa advertencia, llama confirm_stop_bill -- no vuelvas a llamar stop_subscription. "
    "4) Si una herramienta rechaza la accion, explicale a Mia por que en lenguaje simple, no insistas ni la reintentes con otros valores. "
    "5) release_savings_buffer es para semanas de ingreso bajo -- no lo ofrezcas a menos que Mia mencione que le entro poco dinero o necesita liquidez extra. "
    "6) Los apartados (create_envelope) se reparten solos cuando llega un deposito que coincide con el patron de nomina declarado (set_income_pattern) -- si Mia no ha declarado su patron todavia y quiere crear un apartado, pidele primero el monto y frecuencia aproximada de su nomina. "
    "7) simulate_decision y get_financial_lesson NUNCA ejecutan nada real -- son simulaciones educativas, no acciones. Puedes llamarlas libremente sin pedir confirmacion, y explica siempre que el resultado es una proyeccion, no un cambio ya hecho. Si Mia pregunta '¿que pasaria si...?' sobre un cargo o su gasto, usa simulate_decision en vez de estimar tu mismo el impacto. "
    "8) Se breve y claro, en español."
)


def sanitize(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


DOLLAR_AMOUNT_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")


def extract_dollar_amounts(text):
    return {round(float(m.replace(",", "")), 2) for m in DOLLAR_AMOUNT_RE.findall(text or "")}


def collect_verified_numbers(actions_taken):
    """Aplana todos los numeros que de verdad vinieron de una tool en este
    turno -- la lista blanca contra la que se valida cualquier cifra en
    dolares que el modelo mencione en el texto de su respuesta final."""
    numbers = set()

    def walk(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            numbers.add(round(float(value), 2))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for action in actions_taken:
        walk(action.get("result"))
    return numbers


def find_unverified_amounts(reply, actions_taken):
    """Verificacion anti-alucinacion sobre el TEXTO de la respuesta, no solo
    sobre las acciones que mueven dinero. Encontrado en produccion: el
    modelo llamaba get_status correctamente (la tool solo trae
    'annual_cost' para una fuga, nunca un monto mensual) y luego inventaba
    un monto mensual plausible ('$299/mes') en la narrativa en vez de decir
    que no tenia ese dato exacto -- ninguna accion real se vio afectada,
    pero la respuesta le mintio a la usuaria sobre un numero de su cuenta."""
    if not actions_taken:
        return set()
    verified = collect_verified_numbers(actions_taken)
    mentioned = extract_dollar_amounts(reply)
    return {n for n in mentioned if not any(abs(n - v) < 0.5 for v in verified)}


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
        if name == "get_score_history":
            return sanitize({"history": actions.get_score_history(user_id)})
        if name == "stop_subscription":
            return sanitize(actions.propose_stop_bill(user_id, args.get("bill_title", "")))
        if name == "confirm_stop_bill":
            return sanitize(actions.confirm_stop_bill(user_id))
        if name == "move_to_savings":
            return sanitize(actions.verified_move_to_savings(user_id, args.get("amount"), args.get("reason", "")))
        if name == "release_savings_buffer":
            return sanitize(actions.verified_release_buffer(user_id, args.get("amount"), args.get("reason", "")))
        if name == "get_envelopes_status":
            return sanitize({"ok": True, "envelopes": actions.get_envelope_balances(user_id)})
        if name == "create_envelope":
            return sanitize(actions.create_envelope(user_id, args.get("category", ""), args.get("monthly_target")))
        if name == "set_income_pattern":
            return sanitize(actions.set_income_pattern(user_id, args.get("expected_amount"), args.get("frequency_days")))
        if name == "confirm_pending_allocation":
            return sanitize(actions.confirm_pending_allocation(user_id))
        if name == "get_upcoming_expenses":
            return sanitize({"ok": True, "upcoming_expenses": actions.get_current_signals(user_id)["upcoming_expenses"]})
        if name == "simulate_decision":
            params = {"bill_payee": args.get("bill_payee"), "monthly_amount": args.get("monthly_amount")}
            return sanitize(actions.simulate_decision(user_id, args.get("action", ""), params))
        if name == "get_financial_lesson":
            return sanitize(actions.get_weakest_factor_lesson(user_id))
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

    bad_amounts = find_unverified_amounts(visible_reply, actions_taken)
    if bad_amounts:
        # Se le da UNA oportunidad de corregirse antes de sanitizar a la
        # fuerza -- casi siempre basta con señalarle la cifra exacta que
        # invento para que responda de nuevo sin ella.
        contents.append({"role": "model", "parts": [{"text": visible_reply}]})
        contents.append({"role": "user", "parts": [{"text": (
            f"Tu respuesta menciona ${sorted(bad_amounts)[0]:.0f}, pero ese monto no aparece en ninguna "
            "herramienta que llamaste en esta conversacion. No inventes ni derives cifras en dolares -- usa "
            "unicamente los numeros que regresaron las herramientas, o di explicitamente que no tienes ese "
            "dato exacto. Responde de nuevo corrigiendo esto."
        )}]})
        try:
            retry = call_gemini(contents)
            retry_parts = retry.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            retry_text = "".join(p.get("text", "") for p in retry_parts).strip()
        except Exception:
            retry_text = ""
        if retry_text and not find_unverified_amounts(retry_text, actions_taken):
            visible_reply = retry_text
        else:
            visible_reply = DOLLAR_AMOUNT_RE.sub("[monto no confirmado]", visible_reply)

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
