"""
Lambda disparado por DynamoDB Streams -- el "webhook tras cada transaccion".
Se ejecuta automaticamente cuando algo cambia en jarbis-financiero-data,
sin que nadie tenga que llamar nada. Reacciona a los dos eventos que
realmente importan para Mia: un bill que se detiene, o dinero que se mueve
a ahorro. Funciona igual sin importar si la accion vino del chat o de
advance-day -- ambos escriben en la misma tabla.
"""
import json
import time
import boto3
from decimal import Decimal
from boto3.dynamodb.conditions import Key

REGION = "us-east-1"
TABLE_NAME = "jarbis-financiero-data"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)


def to_decimal(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_decimal(v) for v in obj]
    return obj


def from_dynamo_image(image):
    """Convierte el formato AttributeValue de streams a un dict normal."""
    result = {}
    for k, v in image.items():
        if "S" in v:
            result[k] = v["S"]
        elif "N" in v:
            result[k] = float(v["N"])
        elif "BOOL" in v:
            result[k] = v["BOOL"]
        elif "NULL" in v:
            result[k] = None
    return result


def write_notification(user_id, text, date):
    table.put_item(Item=to_decimal({
        "user_id": user_id,
        "sk": f"NOTIFICATION#{date}#{int(time.time() * 1000)}",
        "type": "notification",
        "date": date,
        "text": text,
    }))


def lambda_handler(event, context):
    for record in event.get("Records", []):
        event_name = record["eventName"]
        new_image = from_dynamo_image(record["dynamodb"].get("NewImage", {}))
        old_image = from_dynamo_image(record["dynamodb"].get("OldImage", {}))
        user_id = new_image.get("user_id") or old_image.get("user_id")
        if not user_id:
            continue

        if event_name == "INSERT" and new_image.get("type") == "purchase" and new_image.get("category") == "savings_transfer":
            write_notification(
                user_id,
                f"Nuevo movimiento detectado: se movieron ${new_image.get('amount')} a tu ahorro ({new_image.get('description', '')}).",
                new_image.get("date", "")
            )

        elif event_name == "INSERT" and new_image.get("type") == "purchase" and new_image.get("category") == "savings_release":
            write_notification(
                user_id,
                f"Nuevo movimiento detectado: se liberaron ${new_image.get('amount')} de tu ahorro a tu cuenta corriente ({new_image.get('description', '')}).",
                new_image.get("date", "")
            )

        elif event_name == "MODIFY" and old_image.get("status") == "recurring" and new_image.get("status") == "cancelled":
            write_notification(
                user_id,
                f"Nuevo movimiento detectado: se detuvo el cargo automatico de {new_image.get('payee', 'un cargo')}.",
                time.strftime("%Y-%m-%d")
            )

    return {"statusCode": 200}
