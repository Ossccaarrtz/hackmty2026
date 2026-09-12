"""
Lambda: GET /transactions?user_id=mia
Devuelve TODOS los movimientos crudos (deposits + purchases), sin filtrar
ni agregar - el frontend decide despues que mostrar. Incluye running_balance
ya calculado por conveniencia.
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

    response = table.query(KeyConditionExpression=Key("user_id").eq(user_id))
    items = response["Items"]

    transactions = [i for i in items if i["type"] in ("deposit", "purchase")]
    transactions.sort(key=lambda t: (t["date"], t["sk"]))

    running_balance = 0.0
    enriched = []
    for t in transactions:
        amount = float(t["amount"])
        signed = amount if t["type"] == "deposit" else -amount
        running_balance += signed
        enriched.append({
            "id": t["sk"],
            "date": t["date"],
            "type": t["type"],
            "category": t["category"],
            "category_label": t.get("category_label", t["category"]),
            "merchant_name": t.get("merchant_name"),
            "description": t.get("description"),
            "amount": amount,
            "signed_amount": signed,
            "running_balance": round(running_balance, 2),
        })

    summary = {
        "count": len(enriched),
        "total_income": sum(t["amount"] for t in enriched if t["type"] == "deposit"),
        "total_expense": sum(t["amount"] for t in enriched if t["type"] == "purchase"),
        "by_category": {},
    }
    for t in enriched:
        if t["type"] == "purchase":
            summary["by_category"].setdefault(t["category"], 0)
            summary["by_category"][t["category"]] += t["amount"]

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"transactions": enriched, "summary": summary}, default=decimal_default),
    }
