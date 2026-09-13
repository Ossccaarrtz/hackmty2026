"""
Lambda: presupuesto por categoria (antes "apartados"/envelope budgeting).

GET  /envelopes?user_id=mia                  -> estado del presupuesto (metas vs gasto real) + plan de nomina + patron de nomina + meta de gasto mensual
POST /envelopes                              -> fija/actualiza una meta {category, monthly_target, label?} (monthly_target=0 la quita), o la meta global {monthly_budget}
POST /envelopes/income-pattern               -> declara el patron de nomina {expected_amount, frequency_days}
POST /envelopes/simulate-payroll             -> nomina real de un tercero {employer_label?, amount?}

Se conserva la ruta /envelopes (no /budget) y el nombre de este archivo a
proposito: el workflow de deploy no actualiza el Handler configurado en
Lambda, y las rutas viven en API Gateway, fuera de este repo -- renombrar
cualquiera de los dos rompe produccion sin que el deploy lo detecte.

Todas las escrituras reales pasan por agent_actions.py -- el mismo modulo
que usan advance-day y el chat. Este Lambda es solo la capa HTTP encima.
"""
import json

import agent_actions as actions


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/envelopes")
    params = event.get("queryStringParameters") or {}
    user_id = params.get("user_id", "ana")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Body invalido, se esperaba JSON."})

    if method == "GET" and path.rstrip("/") == "/envelopes":
        return _response(200, {
            "budget": actions.get_budget_status(user_id),
            "payday_plan": actions.get_last_payday_plan(user_id),
            "income_pattern": actions.get_income_pattern(user_id),
            "monthly_budget": actions.get_monthly_budget(user_id),
        })

    if method == "POST" and path.rstrip("/") == "/envelopes":
        if "monthly_budget" in body:
            result = actions.set_monthly_budget(user_id, body.get("monthly_budget"))
        else:
            result = actions.set_category_budget(user_id, body.get("category", ""), body.get("monthly_target"), body.get("label"))
        return _response(200 if result.get("ok") else 400, result)

    if method == "POST" and path.rstrip("/") == "/envelopes/income-pattern":
        result = actions.set_income_pattern(user_id, body.get("expected_amount"), body.get("frequency_days"))
        return _response(200 if result.get("ok") else 400, result)

    if method == "POST" and path.rstrip("/") == "/envelopes/simulate-payroll":
        result = actions.simulate_third_party_payroll(user_id, body.get("employer_label", "Papa y mama"), body.get("amount"))
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
