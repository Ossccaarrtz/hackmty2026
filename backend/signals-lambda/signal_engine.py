"""
Motor de senales: calcula el Cash-Flow Resilience Score, detecta fugas,
y arma el JSON del contrato de datos que el frontend consume.

Puro en el sentido de que no toca AWS/DynamoDB directamente -- recibe listas
de deposits/purchases/bills ya cargadas, y opcionalmente un historial real
de scores (para trend/proyeccion), y devuelve el objeto de senales. Quien
llama (lambda_function.py, lambda_advance_day.py, agent_actions.py) es
responsable de leer/escribir el historial en DynamoDB.
"""
from datetime import date, timedelta
from statistics import mean, pstdev

ESSENTIAL_CATEGORIES = {"rent", "groceries", "transport", "utilities", "income"}
NEUTRAL_CATEGORIES = {"income", "savings_transfer"}  # no cuentan como gasto discrecional ni esencial
LIQUIDITY_WARNING_DAYS = 7  # si el colchon cubre menos de esto, se genera una alerta
WEIGHTS = {"income": 0.35, "essential": 0.25, "bills": 0.20, "liquidity": 0.20}


def is_neutral(category):
    """Reasignaciones internas de dinero (ahorro, apartados) -- no son gasto
    discrecional ni esencial, y no deben disparar la alerta de anomalia.
    'envelope:<categoria>' es dinamico por usuario, no cabe en un set fijo."""
    return category in NEUTRAL_CATEGORIES or (category or "").startswith("envelope:")


def resolve_reference_date(as_of_date, purchases):
    """Fecha de referencia para 'hoy' cuando no se especifica un checkpoint
    explicito -- NUNCA el reloj de pared (date.today()), siempre la fecha
    mas reciente que de verdad existe en los datos. Asi ningun guardrail
    (anomalia, pronostico de gastos) depende de que el calendario real
    siga alineado con el ultimo dia sembrado: en cuanto "hoy" real cruce
    la fecha del ultimo checkpoint de la demo, usar date.today() habria
    movido la referencia mas alla de cualquier dato real sembrado, sin que
    nadie lo notara hasta ver numeros raros."""
    if as_of_date:
        return as_of_date
    dates = [p["date"] for p in purchases]
    return max(dates) if dates else None


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def days_between(d1, d2):
    return abs((date.fromisoformat(d1) - date.fromisoformat(d2)).days)


def compute_elapsed_days(deposits, purchases, as_of_date):
    """Dias reales cubiertos por los datos disponibles -- nunca una constante fija.
    Antes se dividia entre 90 sin importar cuantos dias de historia habia
    realmente (ej. en un checkpoint de Dia 45), lo que inflaba el colchon de
    liquidez a la mitad de su valor real."""
    all_dates = [d["date"] for d in deposits] + [p["date"] for p in purchases]
    if not all_dates:
        return 1
    earliest = min(all_dates)
    latest = as_of_date or max(all_dates)
    return max(days_between(earliest, latest), 1)


def score_income_regularity(deposits):
    # Los retornos de ahorro (savings_release) son movimientos internos, no
    # ingreso real -- si se cuentan aqui, inflan artificialmente la
    # "regularidad de ingreso" de la persona.
    deposits = [d for d in deposits if d.get("category") != "savings_release"]
    if not deposits:
        return {"value": 0, "detail": "sin depositos registrados todavia"}

    sorted_deps = sorted(deposits, key=lambda d: d["date"])
    amounts = [float(d["amount"]) for d in sorted_deps]
    gaps = [days_between(sorted_deps[i]["date"], sorted_deps[i - 1]["date"]) for i in range(1, len(sorted_deps))]

    avg_amount = mean(amounts)
    amount_cv = pstdev(amounts) / avg_amount if avg_amount else 0
    gap_cv = (pstdev(gaps) / mean(gaps)) if gaps and mean(gaps) else 0
    combined_cv = (amount_cv + gap_cv) / 2

    return {
        "value": round(clamp(100 - combined_cv * 100, 0, 100)),
        "detail": f"{len(deposits)} depositos, promedio ${round(avg_amount)}, variacion de monto {round(amount_cv * 100)}%",
    }


def score_essential_ratio(purchases, total_income):
    discretionary_spend = sum(
        float(p["amount"]) for p in purchases
        if p["category"] not in ESSENTIAL_CATEGORIES and not is_neutral(p["category"])
    )
    ratio = discretionary_spend / total_income if total_income else 0
    return {
        "value": round(clamp(100 - ratio * 200, 0, 100)),
        "detail": f"gasto discrecional es {round(ratio * 100)}% del ingreso total",
    }


LEAK_LOOKBACK_DAYS = 60  # ventana de "actividad reciente" -- antes se comprobaba actividad relacionada en TODA la historia disponible, no en una ventana reciente, asi que un bill con una sola compra relacionada hace meses quedaba "sano" para siempre


def evaluate_bills(bills, purchases, as_of_date=None):
    """Compara cada bill contra actividad real relacionada, usando el
    merchant_name real de cada compra -- NO un mapeo categoria->comercio
    fijo. Un mapeo fijo solo puede tener los nombres de comercio de UNA
    persona sembrada; con cualquier otra persona (otros nombres de
    comercio, ej. "Telcel Plan" de Ana) el bill sano siempre saldria como
    fuga aunque exista actividad real -- encontrado al sembrar la primera
    persona nueva del proyecto, cuando todavia convivia con la persona
    original del prototipo."""
    reference = resolve_reference_date(as_of_date, purchases)
    results = []
    for bill in bills:
        merchant_name = bill["payee"]
        related = any(
            p.get("merchant_name") == merchant_name
            and (reference is None or days_between(p["date"], reference) <= LEAK_LOOKBACK_DAYS)
            for p in purchases
        )
        results.append({**bill, "healthy": bill["status"] != "recurring" or related})
    healthy_count = sum(1 for b in results if b["healthy"])
    score = round((healthy_count / len(results)) * 100) if results else 100
    return {"score": score, "bills": results}


ACTIVATION_WINDOW_DAYS = 30  # ventana de "uso reciente" para el componente de frecuencia
ACTIVATION_RECENCY_FULL_CREDIT_DAYS = 7  # actividad dentro de esta ventana = maxima puntuacion de "reciente"
ACTIVATION_RECENCY_ZERO_CREDIT_DAYS = 60  # sin actividad por esto o mas = cero puntuacion de "reciente"
ACTIVATION_DORMANT_THRESHOLD = 40  # por debajo de esto, se genera la alerta de tarjeta inactiva


def compute_activation_signal(purchases, as_of_date=None):
    """Mide que tan 'dormida' esta la tarjeta -- la senal que le importa al
    banco (activar el convenio universitario que ya pago), separada a
    proposito del Cash-Flow Resilience Score (que mide preparacion para
    credito). Son dos preguntas distintas: 'esta usando su tarjeta' no es
    lo mismo que 'esta lista para una tarjeta de credito' -- mezclarlas en
    un solo numero habria obligado a re-normalizar los pesos ya probados
    del score existente, y habria confundido a que responde cada uno.

    Solo cuenta actividad real de comercio (purchases) hecha CON LA
    TARJETA DEL BANCO (source='bank', el default) -- ni depositos, ni
    reasignaciones internas de dinero (apartados, ahorro), ni gasto
    declarado en efectivo/otra tarjeta (log_external_expense en
    agent_actions.py). Un estudiante puede gastar mucho en general y aun
    asi tener la tarjeta del banco dormida -- eso es precisamente lo que
    el banco necesita saber, y es distinto de 'wallet share' (cuanto de SU
    gasto total captura el banco, ver compute_wallet_share)."""
    reference = resolve_reference_date(as_of_date, purchases)
    real_purchases = [p for p in purchases if not is_neutral(p["category"]) and p.get("source", "bank") == "bank"]
    if not real_purchases or not reference:
        return {
            "value": 0, "status": "sin_datos",
            "days_since_last_activity": None, "transactions_last_30_days": 0,
            "detail": "No hay compras registradas todavia.",
        }

    last_date = max(p["date"] for p in real_purchases)
    days_since_last_activity = days_between(last_date, reference)

    if days_since_last_activity <= ACTIVATION_RECENCY_FULL_CREDIT_DAYS:
        recency_score = 100
    else:
        span = ACTIVATION_RECENCY_ZERO_CREDIT_DAYS - ACTIVATION_RECENCY_FULL_CREDIT_DAYS
        recency_score = clamp(100 - (days_since_last_activity - ACTIVATION_RECENCY_FULL_CREDIT_DAYS) / span * 100, 0, 100)

    recent_count = sum(1 for p in real_purchases if days_between(p["date"], reference) <= ACTIVATION_WINDOW_DAYS)
    frequency_score = clamp(recent_count * 15, 0, 100)  # ~7 compras/mes = puntuacion maxima

    value = round(0.6 * recency_score + 0.4 * frequency_score)
    status = "activa" if value >= 70 else ("en_riesgo" if value >= ACTIVATION_DORMANT_THRESHOLD else "dormida")

    return {
        "value": value,
        "status": status,
        "days_since_last_activity": days_since_last_activity,
        "transactions_last_30_days": recent_count,
        "detail": f"Ultima compra hace {days_since_last_activity} dias, {recent_count} compras en los ultimos {ACTIVATION_WINDOW_DAYS} dias.",
    }


def compute_wallet_share(purchases, as_of_date=None):
    """Que porcion de TODO el gasto real (banco + declarado por el usuario:
    efectivo u otra tarjeta) pasa por la tarjeta del banco aliado -- una
    pregunta de negocio distinta de 'esta dormida la tarjeta'
    (compute_activation_signal). Un estudiante puede estar activo (la usa
    de vez en cuando) y aun asi mandar la mayoria de su gasto real a otro
    lado -- ese es el caso mas accionable para el banco (recuperar ese
    gasto con una oferta especifica), y sin el registro manual de gasto
    externo esta pregunta seria invisible por completo: solo veriamos lo
    que ya pasa por su tarjeta, nunca lo que se le esta yendo."""
    real_purchases = [p for p in purchases if not is_neutral(p["category"])]
    if not real_purchases:
        return {"value": None, "bank_amount": 0.0, "external_amount": 0.0, "detail": "No hay gasto registrado todavia."}

    bank_amount = sum(float(p["amount"]) for p in real_purchases if p.get("source", "bank") == "bank")
    external_amount = sum(float(p["amount"]) for p in real_purchases if p.get("source", "bank") != "bank")
    total = bank_amount + external_amount
    if total <= 0:
        return {"value": None, "bank_amount": 0.0, "external_amount": 0.0, "detail": "No hay gasto registrado todavia."}

    value = round(bank_amount / total * 100)
    return {
        "value": value,
        "bank_amount": round(bank_amount, 2),
        "external_amount": round(external_amount, 2),
        "detail": f"{value}% de tu gasto total pasa por tu tarjeta del banco; {100 - value}% es efectivo u otra tarjeta.",
    }


def score_liquidity(current_balance, purchases, elapsed_days):
    # Bank-only a proposito, igual que compute_totals: current_balance ya
    # representa solo la cuenta del banco, asi que el ritmo de consumo con
    # el que se compara tiene que salir de la misma fuente -- mezclar
    # balance de banco con ritmo de gasto que incluye efectivo/otra
    # tarjeta daria un colchon de liquidez internamente inconsistente.
    essential_spend = sum(
        float(p["amount"]) for p in purchases
        if p["category"] in ESSENTIAL_CATEGORIES and p["category"] != "income" and p.get("source", "bank") == "bank"
    )
    avg_daily = essential_spend / elapsed_days if elapsed_days else 0
    days_covered = int(current_balance / avg_daily) if avg_daily > 0 else 999
    cushion_ratio = (current_balance / (avg_daily * 14)) if avg_daily > 0 else 2
    return {
        "value": round(clamp(cushion_ratio * 50, 0, 100)),
        "days_covered": days_covered,
    }


def forecast_upcoming_expenses(purchases, as_of_date, lookahead_days=5):
    """Detecta gastos recurrentes por categoria (ej. gasolina cada ~14 dias)
    a partir de la cadencia real observada -- no asume periodicidad fija.
    Requiere al menos 3 ocurrencias reales para calcular un intervalo
    promedio, y descarta categorias donde el intervalo es demasiado
    irregular (coeficiente de variacion > 50%) para no fingir que un gasto
    genuinamente aleatorio es 'predecible'."""
    reference = resolve_reference_date(as_of_date, purchases)
    if not reference:
        return []
    today = date.fromisoformat(reference)
    by_category = {}
    for p in purchases:
        if is_neutral(p["category"]) or p["date"] > reference:
            continue
        by_category.setdefault(p["category"], []).append(p)

    forecasts = []
    for category, items in by_category.items():
        items = sorted(items, key=lambda p: p["date"])
        if len(items) < 3:
            continue
        dates = [date.fromisoformat(p["date"]) for p in items]
        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_interval = mean(intervals)
        if avg_interval <= 0:
            continue
        cv = pstdev(intervals) / avg_interval if len(intervals) > 1 else 0
        if cv > 0.5:
            continue

        expected_next = dates[-1] + timedelta(days=round(avg_interval))
        days_until = (expected_next - today).days
        if -2 <= days_until <= lookahead_days:
            forecasts.append({
                "category": category,
                "category_label": items[-1].get("category_label", category),
                "expected_date": expected_next.isoformat(),
                "expected_amount": round(mean(float(p["amount"]) for p in items), 2),
                "days_until": days_until,
                "confidence": round(clamp(100 - cv * 100, 0, 100)),
            })

    forecasts.sort(key=lambda f: f["days_until"])
    return forecasts


def compute_trend(score_history, current_score):
    """Tendencia real comparada contra el ultimo punto persistido -- antes
    siempre decia 'up' sin calcular nada."""
    if not score_history:
        return "flat"
    last_value = score_history[-1]["value"]
    if current_score > last_value:
        return "up"
    if current_score < last_value:
        return "down"
    return "flat"


def project_readiness(score_history, current_score, threshold=75):
    """Proyeccion basada en el historial REAL de scores persistidos -- antes
    usaba una lista inventada ([45, 52, 58]) sin relacion a datos reales.
    Devuelve None si todavia no hay suficiente historial (es mas honesto
    que fabricar un numero)."""
    points = [h["value"] for h in score_history] + [current_score]
    if len(points) < 2:
        return None
    steps = len(points) - 1
    rate = (current_score - points[0]) / steps
    if rate <= 0:
        return None
    steps_to_ready = -(-(threshold - current_score) // rate)  # ceil
    return max(int(steps_to_ready), 0)


def detect_anomaly(purchases, as_of_date, window_days=14):
    """Guardrail de seguridad: compara el gasto reciente contra el propio historial
    de la persona. Si algo se ve muy fuera de lo normal, el agente no debe actuar
    solo -- debe escalar a un humano en vez de asumir que todo esta bien."""
    reference = resolve_reference_date(as_of_date, purchases)
    if not reference:
        return {"detected": False}

    spend = [p for p in purchases if not is_neutral(p["category"]) and p["date"] <= reference]
    if len(spend) < 4:
        return {"detected": False}

    cutoff = (date.fromisoformat(reference) - timedelta(days=window_days)).isoformat()
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


def compute_totals(deposits, purchases):
    """Total de ingreso/gasto para el balance. 'savings_release' se guarda
    con type=purchase (para que la verificacion en agent_actions.py opere
    parejo sobre la lista de purchases), pero para el balance es dinero
    ENTRANDO, no saliendo -- se trata como ingreso aqui pase lo que pase.

    Gasto declarado por el usuario que NO paso por la tarjeta del banco
    (source='cash'/'other_card', ver log_external_expense en
    agent_actions.py) se EXCLUYE de este total -- ese dinero nunca salio
    de la cuenta que este balance representa. Si se sumara aqui, el
    colchon de liquidez mostraria menos dinero del que la cuenta del banco
    de verdad tiene, solo porque el usuario gasto efectivo. Ese gasto SI
    debe contar para el ratio esencial/discrecional (ve mas abajo en
    score_essential_ratio, que no filtra por source a proposito) -- son
    dos preguntas distintas sobre los mismos datos."""
    income = sum(float(d["amount"]) for d in deposits)
    expense = 0.0
    for p in purchases:
        if p.get("source", "bank") != "bank":
            continue
        amt = float(p["amount"])
        if p.get("category") == "savings_release":
            income += amt
        else:
            expense += amt
    return income, expense


def filter_up_to(items, as_of_date):
    """Filtra deposits/purchases a solo lo ocurrido hasta as_of_date (inclusive). Para 'avanzar dia'."""
    if not as_of_date:
        return items
    return [i for i in items if i["date"] <= as_of_date]


def compute_signals(deposits, purchases, bills, total_income=None, total_expense=None, as_of_date=None, score_history=None, upcoming_lookahead_days=5):
    """as_of_date debe ser None o una fecha ISO (YYYY-MM-DD) ya validada por
    quien llama -- este modulo no valida el formato, eso es responsabilidad
    del Lambda handler (que si conoce el contexto de un request HTTP)."""
    score_history = score_history or []
    deposits = filter_up_to(deposits, as_of_date)
    purchases = filter_up_to(purchases, as_of_date)
    if as_of_date or total_income is None or total_expense is None:
        total_income, total_expense = compute_totals(deposits, purchases)

    current_balance = (total_income or 0) - (total_expense or 0)
    elapsed_days = compute_elapsed_days(deposits, purchases, as_of_date)

    # Para el ratio esencial/discrecional, el ingreso "real" excluye
    # retornos de ahorro -- de lo contrario un release infla el denominador
    # y hace parecer que gasta menos discrecional de lo real.
    real_income = sum(float(d["amount"]) for d in deposits if d.get("category") != "savings_release")

    income_regularity = score_income_regularity(deposits)
    essential_ratio = score_essential_ratio(purchases, real_income)
    bill_health = evaluate_bills(bills, purchases, as_of_date)
    liquidity = score_liquidity(current_balance, purchases, elapsed_days)
    anomaly = detect_anomaly(purchases, as_of_date)
    activation = compute_activation_signal(purchases, as_of_date)
    wallet_share = compute_wallet_share(purchases, as_of_date)

    final_score = round(
        income_regularity["value"] * WEIGHTS["income"]
        + essential_ratio["value"] * WEIGHTS["essential"]
        + bill_health["score"] * WEIGHTS["bills"]
        + liquidity["value"] * WEIGHTS["liquidity"]
    )

    alerts = [
        {
            "id": b["bill_id"],
            "type": "leak",
            "severity": "high",
            "title": b["payee"],
            "detail": f"Sin actividad relacionada en {elapsed_days} dias",
            "monthly_amount": float(b["payment_amount"]),
            "annual_cost": float(b["payment_amount"]) * 12,
            "status": "detected",
        }
        for b in bill_health["bills"] if not b["healthy"]
    ]
    if liquidity["days_covered"] < LIQUIDITY_WARNING_DAYS:
        alerts.append({
            "id": "liquidity-warning",
            "type": "liquidity_warning",
            "severity": "high",
            "title": "Colchon bajo",
            "detail": f"Tu balance actual solo cubre {liquidity['days_covered']} dias de gasto esencial -- menos de una semana.",
            "annual_cost": 0,
            "status": "detected",
        })
    if activation["status"] == "dormida":
        alerts.append({
            "id": "activation-warning",
            "type": "activation_warning",
            "severity": "high",
            "title": "Tarjeta inactiva",
            "detail": f"Sin compras en los ultimos {activation['days_since_last_activity']} dias -- el banco no ve actividad reciente en tu cuenta.",
            "annual_cost": 0,
            "status": "detected",
        })

    return {
        "score": {
            "value": final_score,
            "trend": compute_trend(score_history, final_score),
            "breakdown": [
                {"key": "income_regularity", "label": "Regularidad de ingreso", "weight": 35, **income_regularity},
                {"key": "essential_ratio", "label": "Ratio esencial/discrecional", "weight": 25, **essential_ratio},
                {"key": "bill_health", "label": "Recurrencia sana", "weight": 20, "value": bill_health["score"],
                 "detail": f"{sum(1 for b in bill_health['bills'] if b['healthy'])}/{len(bill_health['bills'])} bills sanos"},
                {"key": "liquidity_cushion", "label": "Colchon de liquidez", "weight": 20, "value": liquidity["value"],
                 "detail": f"cubre {liquidity['days_covered']} dias de gasto esencial"},
            ],
        },
        "alerts": alerts,
        "anomaly": anomaly,
        "activation": activation,
        "wallet_share": wallet_share,
        "upcoming_expenses": forecast_upcoming_expenses(purchases, as_of_date, lookahead_days=upcoming_lookahead_days),
        "liquidity": {"days_covered": liquidity["days_covered"]},
        "projection": {"weeks_to_ready": project_readiness(score_history, final_score), "product": "tarjeta secured"},
        "_debug": {"total_income": total_income, "total_expense": total_expense, "current_balance": current_balance, "elapsed_days": elapsed_days},
    }
