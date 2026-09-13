"""
Lambda: presupuesto por categoria + metas de ahorro (antes "apartados"/
envelope budgeting; la nomina declarada y el plan reactivo de nomina se
quitaron -- ver compute_smart_allocation para el reemplazo bajo demanda).

GET  /envelopes?user_id=mia                     -> estado del presupuesto (metas de categoria + metas de ahorro vs gasto/aporte real) + meta de gasto mensual
GET  /envelopes?user_id=mia&smart_allocation=1  -> ademas del estado normal, incluye el reparto inteligente sugerido para el saldo actual
POST /envelopes                                 -> fija/actualiza una meta de categoria {category, monthly_target, label?} (monthly_target=0 la quita),
                                                    la meta global {monthly_budget}, una meta de ahorro {goal_label, goal_target_amount, goal_target_date?},
                                                    o un aporte a una meta de ahorro {goal_contribution_slug, goal_contribution_amount}
POST /envelopes/simulate-payroll                -> nomina real de un tercero {amount, employer_label?}

Se conserva la ruta /envelopes (no /budget) y el nombre de este archivo a
proposito: el workflow de deploy no actualiza el Handler configurado en
Lambda, y las rutas viven en API Gateway, fuera de este repo -- renombrar
cualquiera de los dos rompe produccion sin que el deploy lo detecte. Por
la misma razon, las acciones nuevas (metas de ahorro, reparto inteligente)
se agregaron como variantes del BODY o QUERY STRING de las rutas que ya
existen, en vez de rutas nuevas que API Gateway todavia no conoce.

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
        payload = {
            "budget": actions.get_budget_status(user_id),
            "monthly_budget": actions.get_monthly_budget(user_id),
        }
        if params.get("smart_allocation"):
            payload["smart_allocation"] = actions.compute_smart_allocation(user_id)
        return _response(200, payload)

    if method == "POST" and path.rstrip("/") == "/envelopes":
        if "monthly_budget" in body:
            result = actions.set_monthly_budget(user_id, body.get("monthly_budget"))
        elif "goal_label" in body:
            result = actions.create_goal(user_id, body.get("goal_label", ""), body.get("goal_target_amount"), body.get("goal_target_date"))
        elif "goal_contribution_slug" in body:
            result = actions.log_goal_contribution(user_id, body.get("goal_contribution_slug", ""), body.get("goal_contribution_amount"))
        else:
            result = actions.set_category_budget(user_id, body.get("category", ""), body.get("monthly_target"), body.get("label"))
        return _response(200 if result.get("ok") else 400, result)

    if method == "POST" and path.rstrip("/") == "/envelopes/simulate-payroll":
        result = actions.simulate_third_party_payroll(user_id, body.get("amount"), body.get("employer_label", "Papa y mama"))
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
