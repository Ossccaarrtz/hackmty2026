"""
Carga el seed de Ana (seed/ana-seed-output.json) a DynamoDB.

Ana es la persona nueva del pivote a Spark (estudiante universitaria, montos
en MXN) -- vive en paralelo a Mia bajo un user_id distinto ("ana"), sin
tocar ni reemplazar sus datos. Mismo patron de tabla que load_seed.py.

Tabla: jarbis-financiero-data
  PK: user_id (S)   -- "ana" para esta persona
  SK: sk (S)        -- "TXN#<fecha>#<idx>" | "BILL#<nombre>"

Uso: python load_ana_seed.py
"""
import json
import boto3
import os

REGION = "us-east-1"
TABLE_NAME = "jarbis-financiero-data"
USER_ID = "ana"
SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "seed", "ana-seed-output.json")

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

    CATEGORY_TO_MERCHANT = {
        "groceries": "Cafeteria Central",
        "transport": "Ruta Universidad",
        "rent": "Casa Renta Estudiantil",
        "utilities": "Telcel Plan",
        "discretionary": "Cafe Central / CineExtra",
    }
    CATEGORY_LABEL = {
        "groceries": "Cafetería",
        "transport": "Transporte",
        "rent": "Renta de cuarto",
        "utilities": "Plan celular",
        "discretionary": "Discrecional",
        "income": "Mesada / medio tiempo",
    }

    for i, dep in enumerate(seed["ledger"]["deposits"]):
        items.append({
            "user_id": USER_ID,
            "sk": f"TXN#{dep['date']}#dep{i}",
            "type": "deposit",
            "date": dep["date"],
            "amount": dep["amount"],
            "category": "income",
            "category_label": CATEGORY_LABEL["income"],
            "merchant_name": None,
            "description": "Mesada / pago medio tiempo",
        })

    for i, p in enumerate(seed["ledger"]["purchases"]):
        items.append({
            "user_id": USER_ID,
            "sk": f"TXN#{p['date']}#pur{i}",
            "type": "purchase",
            "date": p["date"],
            "amount": p["amount"],
            "category": p["category"],
            "category_label": CATEGORY_LABEL.get(p["category"], p["category"]),
            "merchant_name": CATEGORY_TO_MERCHANT.get(p["category"]),
        })

    bills = [
        {"name": "FitZone Campus", "payee": "FitZone Campus", "status": "recurring", "payment_amount": 250, "bill_id": seed["gym_bill_id"]},
        {"name": "Telcel Plan", "payee": "Telcel Plan", "status": "recurring", "payment_amount": 200, "bill_id": seed["phone_bill_id"]},
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
