"""
Tests unitarios de signal_engine.py -- motor de senales puro, sin AWS ni
Nessie, asi que no hace falta ningun mock. unittest de la stdlib, sin
dependencias nuevas.

Prioridad explicita de este archivo: fijar como regresion el fix del
"reloj de pared" -- detect_anomaly y forecast_upcoming_expenses solian
apagarse silenciosamente (o iban a depender de date.today()) cuando no
se pasaba un as_of_date explicito. Ahora resuelven la fecha de
referencia contra los datos reales (resolve_reference_date), nunca
contra el calendario real -- asi el guardrail sigue activo el dia
despues de la demo sin que nadie tenga que tocar nada.

Correr con: python -m unittest test_signal_engine -v
"""
import unittest

import signal_engine as se


def purchase(date, category, amount, category_label=None, merchant_name=None):
    return {"date": date, "category": category, "amount": amount, "category_label": category_label or category, "merchant_name": merchant_name}


class TestResolveReferenceDate(unittest.TestCase):
    def test_prefers_explicit_as_of(self):
        purchases = [purchase("2026-09-01", "groceries", 50)]
        self.assertEqual(se.resolve_reference_date("2026-07-01", purchases), "2026-07-01")

    def test_falls_back_to_max_purchase_date(self):
        purchases = [purchase("2026-06-01", "groceries", 50), purchase("2026-09-12", "rent", 650)]
        self.assertEqual(se.resolve_reference_date(None, purchases), "2026-09-12")

    def test_none_when_no_data_and_no_as_of(self):
        self.assertIsNone(se.resolve_reference_date(None, []))

    def test_never_uses_wall_clock_time(self):
        """El nombre del test es literal: no debe existir ninguna via para
        que esto devuelva la fecha real de hoy si esa fecha no aparece en
        los datos. Se simula que 'hoy' real (segun el sistema donde corre
        el test) ya paso el ultimo dato sembrado -- la referencia debe
        seguir siendo la del dato mas reciente, jamas una fecha futura."""
        purchases = [purchase("2026-09-12", "rent", 650)]
        reference = se.resolve_reference_date(None, purchases)
        self.assertEqual(reference, "2026-09-12")


class TestDetectAnomalyAnchoring(unittest.TestCase):
    """Regresion directa del bug de guardrail apagado: antes, sin
    as_of_date explicito, detect_anomaly devolvia siempre {"detected":
    False} sin evaluar nada. Ahora debe evaluar de verdad usando la
    fecha mas reciente de los datos como ancla."""

    def test_without_as_of_still_evaluates_using_data_anchor(self):
        # Gasto normal 14 dias antes del ancla (2026-09-12, la fecha mas
        # reciente en los datos), luego un pico claro justo antes del ancla.
        purchases = (
            [purchase(f"2026-08-{d:02d}", "discretionary", 10) for d in range(1, 10)]
            + [purchase(f"2026-09-{d:02d}", "discretionary", 100) for d in range(8, 13)]
        )
        result = se.detect_anomaly(purchases, as_of_date=None)
        self.assertTrue(result["detected"])

    def test_returns_false_not_crash_with_no_purchases(self):
        result = se.detect_anomaly([], as_of_date=None)
        self.assertEqual(result, {"detected": False})

    def test_explicit_as_of_still_respected(self):
        purchases = [purchase("2026-07-01", "discretionary", 10)] * 5
        result = se.detect_anomaly(purchases, as_of_date="2026-07-01")
        self.assertFalse(result["detected"])  # gasto parejo, sin pico

    def test_neutral_categories_excluded_from_anomaly_math(self):
        # Un envelope grande no debe disparar la anomalia -- es reasignacion
        # interna de dinero, no gasto.
        purchases = (
            [purchase(f"2026-08-{d:02d}", "discretionary", 10) for d in range(1, 10)]
            + [purchase("2026-09-10", "envelope:gasolina", 500)]
        )
        result = se.detect_anomaly(purchases, as_of_date=None)
        self.assertFalse(result["detected"])


class TestForecastUpcomingExpensesAnchoring(unittest.TestCase):
    def test_without_as_of_anchors_to_latest_purchase_date(self):
        # Renta perfectamente regular cada 30 dias, la ultima el 2026-08-18
        # -- el proximo pago esperado cae el 2026-09-17, a 5 dias del ancla.
        # Se agrega una compra en otra categoria el 2026-09-12 (mas reciente
        # que la propia renta) para que el ancla sea esa fecha, igual que en
        # los datos reales de Mia -- si el ancla se resolviera solo con la
        # fecha de la propia renta (2026-08-18), este forecast no caeria en
        # la ventana de aviso y el test lo detectaria como regresion.
        purchases = [
            purchase("2026-06-19", "rent", 650),
            purchase("2026-07-19", "rent", 650),
            purchase("2026-08-18", "rent", 650),
            purchase("2026-09-12", "groceries", 30),
        ]
        forecasts = se.forecast_upcoming_expenses(purchases, as_of_date=None)
        self.assertEqual(len(forecasts), 1)
        self.assertEqual(forecasts[0]["category"], "rent")
        self.assertEqual(forecasts[0]["expected_date"], "2026-09-17")
        self.assertEqual(forecasts[0]["confidence"], 100)

    def test_requires_at_least_three_occurrences(self):
        purchases = [purchase("2026-08-01", "utilities", 45), purchase("2026-09-01", "utilities", 45)]
        self.assertEqual(se.forecast_upcoming_expenses(purchases, as_of_date=None), [])

    def test_irregular_cadence_is_discarded(self):
        # Intervalos muy dispares (5, 40, 3 dias) -- coeficiente de
        # variacion alto, no debe reportarse como predecible.
        purchases = [
            purchase("2026-07-01", "discretionary", 20),
            purchase("2026-07-06", "discretionary", 20),
            purchase("2026-08-15", "discretionary", 20),
            purchase("2026-08-18", "discretionary", 20),
        ]
        self.assertEqual(se.forecast_upcoming_expenses(purchases, as_of_date=None), [])

    def test_empty_purchases_returns_empty_not_crash(self):
        self.assertEqual(se.forecast_upcoming_expenses([], as_of_date=None), [])


class TestEvaluateBillsRecencyWindow(unittest.TestCase):
    """Regresion del hallazgo de la auditoria: antes se comprobaba actividad
    relacionada en TODA la historia disponible ("alguna vez"), no en una
    ventana reciente -- un bill con una sola compra relacionada hace meses
    quedaba sano para siempre."""

    def test_bill_with_recent_activity_is_healthy(self):
        bills = [{"payee": "Telco Co", "status": "recurring", "payment_amount": 45, "bill_id": "b1"}]
        purchases = [purchase("2026-09-03", "utilities", 45, merchant_name="Telco Co")]
        result = se.evaluate_bills(bills, purchases, as_of_date="2026-09-12")
        self.assertTrue(result["bills"][0]["healthy"])

    def test_bill_with_only_old_activity_is_unhealthy(self):
        bills = [{"payee": "Telco Co", "status": "recurring", "payment_amount": 45, "bill_id": "b1"}]
        purchases = [purchase("2026-06-01", "utilities", 45, merchant_name="Telco Co")]  # > 60 dias antes del as_of
        result = se.evaluate_bills(bills, purchases, as_of_date="2026-09-12")
        self.assertFalse(result["bills"][0]["healthy"])

    def test_bill_with_no_matching_merchant_never_healthy(self):
        """Regresion directa de un hallazgo real: evaluate_bills usaba un mapeo
        categoria->comercio fijo con los nombres de UNA sola persona sembrada
        (Mia) en vez del merchant_name real de cada compra -- con cualquier
        otra persona (otros nombres de comercio), un bill sano con actividad
        real de todos modos salia como fuga. Aqui la categoria SI matchea pero
        el nombre de comercio NO -- debe seguir sin salud, sin importar
        cuantas compras haya en esa categoria."""
        bills = [{"payee": "Gym Co", "status": "recurring", "payment_amount": 40, "bill_id": "b2"}]
        result = se.evaluate_bills(bills, [purchase("2026-09-10", "discretionary", 40, merchant_name="Otro Comercio")], as_of_date="2026-09-12")
        self.assertFalse(result["bills"][0]["healthy"])

    def test_bill_matches_by_real_merchant_name_not_hardcoded_persona(self):
        """Con una persona distinta a Mia (otros nombres de comercio reales),
        el bill debe salir sano si el merchant_name real de la compra
        coincide con el payee del bill -- sin depender de ningun mapeo fijo."""
        bills = [{"payee": "Telcel Plan", "status": "recurring", "payment_amount": 200, "bill_id": "b3"}]
        purchases = [purchase("2026-09-01", "utilities", 200, merchant_name="Telcel Plan")]
        result = se.evaluate_bills(bills, purchases, as_of_date="2026-09-12")
        self.assertTrue(result["bills"][0]["healthy"])

    def test_cancelled_bill_is_always_healthy_regardless_of_activity(self):
        bills = [{"payee": "Gym Co", "status": "cancelled", "payment_amount": 40, "bill_id": "b2"}]
        result = se.evaluate_bills(bills, [], as_of_date="2026-09-12")
        self.assertTrue(result["bills"][0]["healthy"])


class TestIsNeutral(unittest.TestCase):
    def test_known_neutral_categories(self):
        self.assertTrue(se.is_neutral("income"))
        self.assertTrue(se.is_neutral("savings_transfer"))

    def test_envelope_prefix_is_neutral(self):
        self.assertTrue(se.is_neutral("envelope:gasolina"))
        self.assertTrue(se.is_neutral("envelope:comida"))

    def test_regular_spend_categories_are_not_neutral(self):
        self.assertFalse(se.is_neutral("groceries"))
        self.assertFalse(se.is_neutral("discretionary"))

    def test_savings_release_is_not_neutral_by_design(self):
        # savings_release se trata aparte (como ingreso) en compute_totals,
        # no deberia colarse aqui como "neutral" tambien -- confirma que
        # is_neutral no es donde vive esa regla.
        self.assertFalse(se.is_neutral("savings_release"))


class TestComputeTrendAndProjection(unittest.TestCase):
    def test_trend_flat_with_no_history(self):
        self.assertEqual(se.compute_trend([], 70), "flat")

    def test_trend_up_down_flat(self):
        history = [{"date": "2026-09-01", "value": 60}]
        self.assertEqual(se.compute_trend(history, 70), "up")
        self.assertEqual(se.compute_trend(history, 50), "down")
        self.assertEqual(se.compute_trend(history, 60), "flat")

    def test_projection_none_with_insufficient_history(self):
        self.assertIsNone(se.project_readiness([], 70))

    def test_projection_none_when_score_not_improving(self):
        history = [{"date": "2026-08-01", "value": 70}]
        self.assertIsNone(se.project_readiness(history, 65))  # bajando, no hay ETA honesto

    def test_projection_real_positive_rate(self):
        history = [{"date": "2026-08-01", "value": 60}]
        steps = se.project_readiness(history, 70, threshold=90)
        self.assertIsInstance(steps, int)
        self.assertGreater(steps, 0)


class TestScoreLiquidity(unittest.TestCase):
    def test_no_essential_spend_gives_max_days_covered(self):
        result = se.score_liquidity(500, [], elapsed_days=30)
        self.assertEqual(result["days_covered"], 999)

    def test_liquidity_scales_with_balance(self):
        purchases = [purchase("2026-08-01", "rent", 900)]  # 30 dias de gasto esencial -> 30/dia
        low = se.score_liquidity(30, purchases, elapsed_days=30)
        high = se.score_liquidity(300, purchases, elapsed_days=30)
        self.assertLess(low["value"], high["value"])
        self.assertLess(low["days_covered"], high["days_covered"])


class TestComputeActivationSignal(unittest.TestCase):
    """Señal de 'activación' del pivote a Spark -- que tan dormida esta la
    tarjeta, la metrica que le importa al banco (KPI del convenio
    universitario), separada a proposito del Cash-Flow Resilience Score."""

    def test_no_purchases_returns_sin_datos(self):
        result = se.compute_activation_signal([], as_of_date="2026-09-12")
        self.assertEqual(result["value"], 0)
        self.assertEqual(result["status"], "sin_datos")
        self.assertIsNone(result["days_since_last_activity"])

    def test_recent_frequent_activity_is_activa(self):
        purchases = [purchase(f"2026-09-{d:02d}", "groceries", 50) for d in range(5, 12)]  # 7 compras, la ultima hace 1 dia
        result = se.compute_activation_signal(purchases, as_of_date="2026-09-12")
        self.assertEqual(result["status"], "activa")
        self.assertEqual(result["days_since_last_activity"], 1)
        self.assertEqual(result["transactions_last_30_days"], 7)

    def test_only_old_activity_is_dormida(self):
        purchases = [purchase("2026-06-01", "groceries", 50)]  # ~103 dias antes del as_of
        result = se.compute_activation_signal(purchases, as_of_date="2026-09-12")
        self.assertEqual(result["status"], "dormida")

    def test_internal_reassignments_do_not_count_as_activity(self):
        """Regresion directa: una mesada que llega o un reparto a apartados
        no prueba que el banco vea la tarjeta en uso -- solo compras reales
        de comercio cuentan."""
        purchases = [
            purchase("2026-09-10", "savings_transfer", 100),
            purchase("2026-09-11", "envelope:transporte", 50),
        ]
        result = se.compute_activation_signal(purchases, as_of_date="2026-09-12")
        self.assertEqual(result["status"], "sin_datos")

    def test_single_recent_purchase_has_high_recency_but_capped_frequency(self):
        result = se.compute_activation_signal([purchase("2026-09-11", "groceries", 50)], as_of_date="2026-09-12")
        self.assertEqual(result["transactions_last_30_days"], 1)
        self.assertLess(result["value"], 100)  # recencia perfecta, pero frecuencia baja lo topa

    def test_compute_signals_includes_activation_and_alert_when_dormant(self):
        purchases = [purchase("2026-06-01", "groceries", 50)]
        deposits = [{"date": "2026-06-01", "amount": 500, "category": "income"}]
        result = se.compute_signals(deposits, purchases, [], as_of_date="2026-09-12")
        self.assertEqual(result["activation"]["status"], "dormida")
        self.assertTrue(any(a["type"] == "activation_warning" for a in result["alerts"]))

    def test_compute_signals_no_activation_alert_when_active(self):
        purchases = [purchase(f"2026-09-{d:02d}", "groceries", 50) for d in range(5, 12)]
        deposits = [{"date": "2026-06-01", "amount": 500, "category": "income"}]
        result = se.compute_signals(deposits, purchases, [], as_of_date="2026-09-12")
        self.assertEqual(result["activation"]["status"], "activa")
        self.assertFalse(any(a["type"] == "activation_warning" for a in result["alerts"]))


if __name__ == "__main__":
    unittest.main()
