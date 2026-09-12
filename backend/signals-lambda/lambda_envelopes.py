"""
Lambda: CRUD de apartados (envelope budgeting).

GET  /envelopes?user_id=mia                  -> lista de apartados con saldo derivado + patron de nomina declarado
POST /envelopes                              -> crea un apartado {category, monthly_target}
POST /envelopes/income-pattern               -> declara el patron de nomina {expected_amount, frequency_days}
POST /envelopes/confirm-allocation           -> ejecuta un reparto pendiente de confirmar
POST /envelopes/simulate-payroll             -> nomina real de un tercero {employer_label?, amount?}

Todas las escrituras reales pasan por agent_actions.py -- el mismo modulo
que usan advance-day y el chat. Este Lambda es solo la capa HTTP encima.
"""
import json

import agent_actions as actions


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/envelopes")
    params = event.get("queryStringParameters") or {}
    user_id = params.get("user_id", "mia")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Body invalido, se esperaba JSON."})

    if method == "GET" and path.rstrip("/") == "/envelopes":
        return _response(200, {
            "envelopes": actions.get_envelope_balances(user_id),
            "income_pattern": actions.get_income_pattern(user_id),
        })

    if method == "POST" and path.rstrip("/") == "/envelopes":
        result = actions.create_envelope(user_id, body.get("category", ""), body.get("monthly_target"))
        return _response(200 if result.get("ok") else 400, result)

    if method == "POST" and path.rstrip("/") == "/envelopes/income-pattern":
        result = actions.set_income_pattern(user_id, body.get("expected_amount"), body.get("frequency_days"))
        return _response(200 if result.get("ok") else 400, result)

    if method == "POST" and path.rstrip("/") == "/envelopes/confirm-allocation":
        result = actions.confirm_pending_allocation(user_id)
        return _response(200 if result.get("ok") else 400, result)

    if method == "POST" and path.rstrip("/") == "/envelopes/simulate-payroll":
        result = actions.simulate_third_party_payroll(user_id, body.get("employer_label", "Estudio Creativo"), body.get("amount"))
        return _response(200 if result.get("ok") else 400, result)

    return _response(404, {"error": f"Ruta no encontrada: {method} {path}"})


def _decimal_default(obj):
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, ensure_ascii=False, default=_decimal_default),
    }
