"""
Lambda: GET /trust-report

Convierte el score interno en un artefacto de confiabilidad exportable:
score actual + historial real + acciones verificadas (sin importar si las
disparo el chat, un checkpoint de advance-day, o el reparto automatico de
apartados) + un resumen narrativo generado de forma deterministica, no por
un LLM -- cada numero que aparece aqui esta respaldado por una transaccion
real en Nessie o un registro real en DynamoDB, no autoreportado.

Pensado para que Capital One lo use como señal de graduacion de sus
propios clientes de secured card (ver PITCH.md) -- no es un producto que
se le venda a bancos externos.
"""
import json
from decimal import Decimal

import agent_actions as actions


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    user_id = params.get("user_id", "mia")

    report = actions.get_trust_report(user_id)
    return _response(200, report)


def _decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, ensure_ascii=False, default=_decimal_default),
    }
