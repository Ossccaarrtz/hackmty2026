"""
Carga el seed de Mia (seed/jarbis-seed-output.json) a DynamoDB.

Tabla: jarbis-financiero-data
  PK: user_id (S)   -- "mia" para la demo
  SK: sk (S)        -- "TXN#<fecha>#<idx>" | "BILL#<nombre>"

Uso: python load_seed.py
"""
import json
import boto3
import os

REGION = "us-east-1"
TABLE_NAME = "jarbis-financiero-data"
USER_ID = "mia"
SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "jarbis-seed-output.json")

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)


def to_decimal(obj):
    import decimal
    if isinstance(obj, float):
        return decimal.Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_decimal(v) for v in obj]
    return obj


def main():
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        seed = json.load(f)

    items = []

    for i, dep in enumerate(seed["ledger"]["deposits"]):
        items.append({
            "user_id": USER_ID,
            "sk": f"TXN#{dep['date']}#dep{i}",
            "type": "deposit",
            "date": dep["date"],
            "amount": dep["amount"],
            "category": "income",
        })

    for i, p in enumerate(seed["ledger"]["purchases"]):
        items.append({
            "user_id": USER_ID,
            "sk": f"TXN#{p['date']}#pur{i}",
            "type": "purchase",
            "date": p["date"],
            "amount": p["amount"],
            "category": p["category"],
        })

    bills = [
        {"name": "Gym Co", "payee": "Gym Co", "status": "recurring", "payment_amount": 40, "bill_id": seed["gym_bill_id"]},
        {"name": "Telco Co", "payee": "Telco Co", "status": "recurring", "payment_amount": 45, "bill_id": seed["phone_bill_id"]},
    ]
    for b in bills:
        items.append({
            "user_id": USER_ID,
            "sk": f"BILL#{b['name']}",
            "type": "bill",
            **b,
        })

    items.append({
        "user_id": USER_ID,
        "sk": "META#totals",
        "type": "meta",
        "total_income": seed["totals"]["totalIn"],
        "total_expense": seed["totals"]["totalOut"],
        "window_days": seed["window_days"],
    })

    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=to_decimal(item))

    print(f"Cargados {len(items)} items a {TABLE_NAME} para user_id={USER_ID}")


if __name__ == "__main__":
    main()
