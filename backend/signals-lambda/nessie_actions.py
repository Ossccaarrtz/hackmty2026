"""
Acciones reales sobre Nessie: las unicas dos cosas que el agente puede
ejecutar de verdad, siguiendo la politica de riesgo definida en el README.
"""
import os
import urllib.request
import json

NESSIE_KEY = os.environ.get("NESSIE_API_KEY", "")
BASE = "https://api.nessieisreal.com"


def _request(method, path, body=None):
    url = f"{BASE}{path}?key={NESSIE_KEY}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def stop_recurring_bill(bill_id, payee, payment_amount, recurring_date=1):
    """Detiene un cargo recurrente. SOLO se llama despues de que el humano confirmo (politica de riesgo)."""
    return _request("PUT", f"/bills/{bill_id}", {
        "status": "cancelled",
        "payee": payee,
        "nickname": payee,
        "payment_date": "2026-09-12",
        "recurring_date": recurring_date,
        "payment_amount": payment_amount,
    })


def sweep_to_savings(checking_id, savings_id, amount, reason):
    """Mueve dinero de checking a ahorro. Autonomo (reversible, sin terceros) segun la politica de riesgo."""
    withdrawal = _request("POST", f"/accounts/{checking_id}/withdrawals", {
        "medium": "balance", "transaction_date": "2026-09-12", "status": "completed",
        "amount": amount, "description": reason,
    })
    deposit = _request("POST", f"/accounts/{savings_id}/deposits", {
        "medium": "balance", "transaction_date": "2026-09-12", "status": "completed",
        "amount": amount, "description": reason,
    })
    return {"withdrawal": withdrawal, "deposit": deposit}
