"""
Motor de senales: calcula el Cash-Flow Resilience Score, detecta fugas,
y arma el JSON del contrato de datos que el frontend consume.

Puerto a Python del prototipo original (backend/signal-engine.js) para
que coincida con el stack real de Jarbis (Lambda en Python 3.12).
"""
from datetime import date, timedelta
from statistics import mean, pstdev

ESSENTIAL_CATEGORIES = {"rent", "groceries", "transport", "utilities", "income"}
NEUTRAL_CATEGORIES = {"income", "savings_transfer"}  # no cuentan como gasto discrecional ni esencial
WINDOW_DAYS = 90
WEIGHTS = {"income": 0.35, "essential": 0.25, "bills": 0.20, "liquidity": 0.20}

CATEGORY_TO_MERCHANT = {
    "groceries": "SuperMart",
    "transport": "MetroTransit",
    "rent": "Landlord Properties",
    "utilities": "Telco Co",
}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def days_between(d1, d2):
    return abs((date.fromisoformat(d1) - date.fromisoformat(d2)).days)


def score_income_regularity(deposits):
    sorted_deps = sorted(deposits, key=lambda d: d["date"])
    amounts = [float(d["amount"]) for d in sorted_deps]
    gaps = [days_between(sorted_deps[i]["date"], sorted_deps[i - 1]["date"]) for i in range(1, len(sorted_deps))]

    amount_cv = pstdev(amounts) / mean(amounts) if mean(amounts) else 0
    gap_cv = (pstdev(gaps) / mean(gaps)) if gaps and mean(gaps) else 0
    combined_cv = (amount_cv + gap_cv) / 2

    return {
        "value": round(clamp(100 - combined_cv * 100, 0, 100)),
        "detail": f"{len(deposits)} depositos, promedio ${round(mean(amounts))}, variacion de monto {round(amount_cv * 100)}%",
    }


def score_essential_ratio(purchases, total_income):
    discretionary_spend = sum(
        float(p["amount"]) for p in purchases
        if p["category"] not in ESSENTIAL_CATEGORIES and p["category"] not in NEUTRAL_CATEGORIES
    )
    ratio = discretionary_spend / total_income if total_income else 0
    return {
        "value": round(clamp(100 - ratio * 200, 0, 100)),
        "detail": f"gasto discrecional es {round(ratio * 100)}% del ingreso total",
    }


def evaluate_bills(bills, purchases):
    results = []
    for bill in bills:
        merchant_name = bill["payee"]
        related = any(
            CATEGORY_TO_MERCHANT.get(p["category"]) == merchant_name for p in purchases
        )
        results.append({**bill, "healthy": bill["status"] != "recurring" or related})
    healthy_count = sum(1 for b in results if b["healthy"])
    score = round((healthy_count / len(results)) * 100) if results else 100
    return {"score": score, "bills": results}


def score_liquidity(current_balance, purchases, window_days):
    essential_spend = sum(float(p["amount"]) for p in purchases if p["category"] in ESSENTIAL_CATEGORIES and p["category"] != "income")
    avg_daily = essential_spend / window_days if window_days else 0
    days_covered = int(current_balance / avg_daily) if avg_daily > 0 else 999
    cushion_ratio = (current_balance / (avg_daily * 14)) if avg_daily > 0 else 2
    return {
        "value": round(clamp(cushion_ratio * 50, 0, 100)),
        "days_covered": days_covered,
    }


def project_readiness(score_history, current_score, threshold=75):
    if len(score_history) < 2:
        return None
    weeks = len(score_history) - 1
    weekly_rate = (current_score - score_history[0]) / weeks
    if weekly_rate <= 0:
        return None
    weeks_to_ready = -(-(threshold - current_score) // weekly_rate) if weekly_rate else None  # ceil
    return max(int(weeks_to_ready), 0) if weeks_to_ready is not None else None


def detect_anomaly(purchases, as_of_date, window_days=14):
    """Guardrail de seguridad: compara el gasto reciente contra el propio historial
    de la persona. Si algo se ve muy fuera de lo normal, el agente no debe actuar
    solo -- debe escalar a un humano en vez de asumir que todo esta bien."""
    if not as_of_date:
        return {"detected": False}

    spend = [p for p in purchases if p["category"] not in NEUTRAL_CATEGORIES and p["date"] <= as_of_date]
    if len(spend) < 4:
        return {"detected": False}

    cutoff = (date.fromisoformat(as_of_date) - timedelta(days=window_days)).isoformat()
    recent = [p for p in spend if p["date"] > cutoff]
    older = [p for p in spend if p["date"] <= cutoff]
    if not older or not recent:
        return {"detected": False}

    recent_daily_avg = sum(float(p["amount"]) for p in recent) / window_days
    older_span = max(days_between(older[0]["date"], cutoff), 1)
    older_daily_avg = sum(float(p["amount"]) for p in older) / older_span

    if older_daily_avg > 0 and recent_daily_avg > older_daily_avg * 2:
        return {
            "detected": True,
            "reason": f"Tu gasto de los ultimos {window_days} dias (${round(recent_daily_avg)}/dia) es mas del doble "
                      f"de tu promedio historico (${round(older_daily_avg)}/dia) -- no se ve como tu patron normal.",
        }
    return {"detected": False}


def filter_up_to(items, as_of_date):
    """Filtra deposits/purchases a solo lo ocurrido hasta as_of_date (inclusive). Para 'avanzar dia'."""
    if not as_of_date:
        return items
    return [i for i in items if i["date"] <= as_of_date]


def compute_signals(deposits, purchases, bills, total_income=None, total_expense=None, as_of_date=None):
    deposits = filter_up_to(deposits, as_of_date)
    purchases = filter_up_to(purchases, as_of_date)
    if as_of_date:
        total_income = sum(float(d["amount"]) for d in deposits)
        total_expense = sum(float(p["amount"]) for p in purchases)

    current_balance = total_income - total_expense

    income_regularity = score_income_regularity(deposits)
    essential_ratio = score_essential_ratio(purchases, total_income)
    bill_health = evaluate_bills(bills, purchases)
    liquidity = score_liquidity(current_balance, purchases, WINDOW_DAYS)
    anomaly = detect_anomaly(purchases, as_of_date)

    final_score = round(
        income_regularity["value"] * WEIGHTS["income"]
        + essential_ratio["value"] * WEIGHTS["essential"]
        + bill_health["score"] * WEIGHTS["bills"]
        + liquidity["value"] * WEIGHTS["liquidity"]
    )

    leaks = [
        {
            "id": b["bill_id"],
            "type": "leak",
            "severity": "high",
            "title": b["payee"],
            "detail": f"Sin actividad relacionada en {WINDOW_DAYS} dias",
            "annual_cost": float(b["payment_amount"]) * 12,
            "status": "detected",
        }
        for b in bill_health["bills"] if not b["healthy"]
    ]

    return {
        "score": {
            "value": final_score,
            "trend": "up",
            "breakdown": [
                {"key": "income_regularity", "label": "Regularidad de ingreso", "weight": 35, **income_regularity},
                {"key": "essential_ratio", "label": "Ratio esencial/discrecional", "weight": 25, **essential_ratio},
                {"key": "bill_health", "label": "Recurrencia sana", "weight": 20, "value": bill_health["score"],
                 "detail": f"{sum(1 for b in bill_health['bills'] if b['healthy'])}/{len(bill_health['bills'])} bills sanos"},
                {"key": "liquidity_cushion", "label": "Colchon de liquidez", "weight": 20, "value": liquidity["value"],
                 "detail": f"cubre {liquidity['days_covered']} dias de gasto esencial"},
            ],
        },
        "alerts": leaks,
        "anomaly": anomaly,
        "liquidity": {"days_covered": liquidity["days_covered"]},
        "projection": {"weeks_to_ready": project_readiness([45, 52, 58, final_score], final_score) or 6, "product": "tarjeta secured"},
        "_debug": {"total_income": total_income, "total_expense": total_expense, "current_balance": current_balance},
    }
