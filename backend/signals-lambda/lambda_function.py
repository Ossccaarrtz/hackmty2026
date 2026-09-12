"""
Lambda: GET /signals?user_id=mia
Lee las transacciones/bills de DynamoDB (jarbis-financiero-data) y devuelve
el JSON del Cash-Flow Resilience Score + alertas + liquidez + proyeccion.
"""
import json
import boto3
from decimal import Decimal
from boto3.dynamodb.conditions import Key

from signal_engine import compute_signals

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

    response = table.query(KeyConditionExpression=Key("user_id").eq(user_id))
    items = response["Items"]

    deposits = [i for i in items if i.get("type") == "deposit"]
    purchases = [i for i in items if i.get("type") == "purchase"]
    bills = [i for i in items if i.get("type") == "bill"]

    # Los totales se calculan sumando las transacciones reales, no un registro
    # estatico -- asi cualquier movimiento nuevo (ej. el sweep de ahorro del
    # agente) se refleja solo, sin tener que sincronizar nada aparte.
    total_income = sum(float(d["amount"]) for d in deposits)
    total_expense = sum(float(p["amount"]) for p in purchases)

    result = compute_signals(
        deposits=deposits,
        purchases=purchases,
        bills=bills,
        total_income=total_income,
        total_expense=total_expense,
        as_of_date=params.get("as_of"),
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(result, default=decimal_default),
    }
