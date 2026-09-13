"""
Acciones reales sobre Nessie: lo unico que el agente puede ejecutar de
verdad, siguiendo la politica de riesgo definida en el README.
"""
import os
import urllib.request
import json
from datetime import date

NESSIE_KEY = os.environ.get("NESSIE_API_KEY", "")
# Cuenta de un tercero real (otra app/otro dueno registrado en el sandbox de
# Nessie, con su propia api key) que representa al cliente/empleador de Ana
# para la demo de nomina de un tercero. No es nuestra cuenta.
EMPLOYER_NESSIE_KEY = os.environ.get("EMPLOYER_NESSIE_API_KEY", "")
BASE = "https://api.nessieisreal.com"


def _request(method, path, body=None, key=None):
    url = f"{BASE}{path}?key={key or NESSIE_KEY}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def stop_recurring_bill(bill_id, payee, payment_amount, recurring_date=1, on_date=None):
    """Detiene un cargo recurrente. SOLO se llama despues de que el humano confirmo (politica de riesgo)."""
    on_date = on_date or date.today().isoformat()
    return _request("PUT", f"/bills/{bill_id}", {
        "status": "cancelled",
        "payee": payee,
        "nickname": payee,
        "payment_date": on_date,
        "recurring_date": recurring_date,
        "payment_amount": payment_amount,
    })


def sweep_to_savings(checking_id, savings_id, amount, reason, on_date=None):
    """Mueve dinero de checking a ahorro. Autonomo (reversible, sin terceros) segun la politica de riesgo."""
    on_date = on_date or date.today().isoformat()
    withdrawal = _request("POST", f"/accounts/{checking_id}/withdrawals", {
        "medium": "balance", "transaction_date": on_date, "status": "completed",
        "amount": amount, "description": reason,
    })
    deposit = _request("POST", f"/accounts/{savings_id}/deposits", {
        "medium": "balance", "transaction_date": on_date, "status": "completed",
        "amount": amount, "description": reason,
    })
    return {"withdrawal": withdrawal, "deposit": deposit}


def receive_from_third_party(employer_account_id, checking_id, amount, reason, on_date=None):
    """Retiro real de una cuenta que NO es nuestra (autenticado con la api
    key del tercero) + deposito real a la cuenta de Ana. A diferencia de
    sweep_to_savings/release_from_savings (dinero movido entre las DOS
    cuentas de Ana con nuestra propia key), aqui el dinero sale de una
    cuenta ajena -- simula que un empleador de verdad le paga a Ana."""
    on_date = on_date or date.today().isoformat()
    withdrawal = _request("POST", f"/accounts/{employer_account_id}/withdrawals", {
        "medium": "balance", "transaction_date": on_date, "status": "completed",
        "amount": amount, "description": reason,
    }, key=EMPLOYER_NESSIE_KEY)
    deposit = _request("POST", f"/accounts/{checking_id}/deposits", {
        "medium": "balance", "transaction_date": on_date, "status": "completed",
        "amount": amount, "description": reason,
    })
    return {"withdrawal": withdrawal, "deposit": deposit}


def release_from_savings(checking_id, savings_id, amount, reason, on_date=None):
    """Libera dinero de ahorro de vuelta a checking -- el lado inverso del
    suavizado de ingreso: en una semana flaca, se libera parte de lo
    acumulado en semanas buenas para mantener un ingreso mas estable."""
    on_date = on_date or date.today().isoformat()
    withdrawal = _request("POST", f"/accounts/{savings_id}/withdrawals", {
        "medium": "balance", "transaction_date": on_date, "status": "completed",
        "amount": amount, "description": reason,
    })
    deposit = _request("POST", f"/accounts/{checking_id}/deposits", {
        "medium": "balance", "transaction_date": on_date, "status": "completed",
        "amount": amount, "description": reason,
    })
    return {"withdrawal": withdrawal, "deposit": deposit}
