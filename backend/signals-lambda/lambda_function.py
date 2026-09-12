"""
Lambda: GET /signals?user_id=mia[&as_of=YYYY-MM-DD]
Lee las transacciones/bills de DynamoDB (jarbis-financiero-data) y devuelve
el JSON del Cash-Flow Resilience Score + alertas + liquidez + proyeccion.
"""
import json
import boto3
from datetime import date
from decimal import Decimal
from boto3.dynamodb.conditions import Key

from signal_engine import compute_signals, compute_totals
from agent_actions import get_score_history, record_score

REGION = "us-east-1"
TABLE_NAME = "jarbis-financiero-data"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    user_id = params.get("user_id", "mia")

    as_of = params.get("as_of")
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
                "body": json.dumps({"error": f"'as_of' debe ser una fecha YYYY-MM-DD valida, recibi '{as_of}'."}),
            }

    response = table.query(KeyConditionExpression=Key("user_id").eq(user_id))
    items = response["Items"]

    deposits = [i for i in items if i.get("type") == "deposit"]
    purchases = [i for i in items if i.get("type") == "purchase"]
    bills = [i for i in items if i.get("type") == "bill"]

    # Los totales se calculan sumando las transacciones reales, no un registro
    # estatico -- asi cualquier movimiento nuevo (ej. el sweep de ahorro del
    # agente) se refleja solo, sin tener que sincronizar nada aparte.
    total_income, total_expense = compute_totals(deposits, purchases)
    history = get_score_history(user_id)

    result = compute_signals(
        deposits=deposits,
        purchases=purchases,
        bills=bills,
        total_income=total_income,
        total_expense=total_expense,
        as_of_date=as_of,
        score_history=history,
    )
    # Solo se persiste historial en consultas del "ahora" real -- un as_of
    # exploratorio (ej. alguien probando una fecha vieja) no debe contaminar
    # la trayectoria real de score que usan trend/proyeccion.
    if not as_of:
        record_score(user_id, None, result["score"]["value"])

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(result, default=decimal_default),
    }
