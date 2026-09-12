"""
Lambda: GET /notifications?user_id=mia
Devuelve las notificaciones generadas por el stream de DynamoDB -- eventos
reales, no simulados, disparados automaticamente cuando algo cambio en la
cuenta (sin importar si vino del chat o de advance-day).
"""
import json
import boto3
from decimal import Decimal
from boto3.dynamodb.conditions import Key

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

    resp = table.query(KeyConditionExpression=Key("user_id").eq(user_id))
    notifications = [i for i in resp["Items"] if i.get("type") == "notification"]
    notifications.sort(key=lambda n: n["sk"], reverse=True)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"notifications": notifications}, default=decimal_default),
    }
