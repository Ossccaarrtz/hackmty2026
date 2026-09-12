"""
Tests unitarios de agent_actions.py -- las UNICAS puertas de entrada para
tocar dinero real. Sin pytest ni dependencias nuevas (unittest + mock, ya
en la stdlib) para no meter nada al empaquetado del Lambda.

Se mockea `table` (DynamoDB) y las funciones de nessie_actions -- estos
tests nunca tocan AWS ni Nessie real, corren en milisegundos.

Prioridad explicita: cubrir los mismos escenarios donde ya se encontraron
bugs de seguridad reales en esta sesion (guardrail de anomalia calculado
pero nunca leido, accion ejecutada sin paso de confirmacion) para que no
puedan volver a colarse sin que un test falle.

Correr con: python -m unittest test_agent_actions -v
"""
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import agent_actions as aa


def fresh_table_mock():
    """Nuevo mock de tabla por test -- evita que llamadas de un test
    contaminen el conteo de otro."""
    return MagicMock()


class BaseAgentActionsTest(unittest.TestCase):
    def setUp(self):
        self.table_patch = patch.object(aa, "table", fresh_table_mock())
        self.table_patch.start()
        self.addCleanup(self.table_patch.stop)


def signals_with(current_balance=500.0, elapsed_days=90, anomaly_detected=False, anomaly_reason=""):
    return {
        "score": {"value": 70, "trend": "flat", "breakdown": []},
        "alerts": [],
        "anomaly": {"detected": anomaly_detected, **({"reason": anomaly_reason} if anomaly_detected else {})},
        "liquidity": {"days_covered": 30},
        "projection": {"weeks_to_ready": 1, "product": "tarjeta secured"},
        "_debug": {"total_income": 0, "total_expense": 0, "current_balance": current_balance, "elapsed_days": elapsed_days},
    }


class TestVerifiedMoveToSavings(BaseAgentActionsTest):
    def setUp(self):
        super().setUp()
        self.load_data_patch = patch.object(aa, "load_data", return_value=([], [], []))
        self.load_data_patch.start()
        self.addCleanup(self.load_data_patch.stop)

    def test_rejects_invalid_amount(self):
        result = aa.verified_move_to_savings("mia", "no-es-numero", "prueba")
        self.assertFalse(result["ok"])

    def test_rejects_non_positive_amount(self):
        result = aa.verified_move_to_savings("mia", 0, "prueba")
        self.assertFalse(result["ok"])

    def test_rejects_amount_over_cap(self):
        result = aa.verified_move_to_savings("mia", aa.MAX_AUTONOMOUS_SAVINGS + 1, "prueba")
        self.assertFalse(result["ok"])
        self.assertIn("limite", result["reason"].lower() + result["reason"])

    def test_rejects_when_anomaly_detected(self):
        with patch.object(aa, "get_full_signals", return_value=signals_with(anomaly_detected=True, anomaly_reason="gasto atipico")):
            result = aa.verified_move_to_savings("mia", 20, "prueba")
        self.assertFalse(result["ok"])
        self.assertIn("gasto atipico", result["reason"])

    def test_rejects_when_daily_cap_already_reached(self):
        today = date.today().isoformat()
        purchases_today = [{"category": "savings_transfer", "date": today, "amount": 90}]
        with patch.object(aa, "load_data", return_value=([], purchases_today, [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with()):
            result = aa.verified_move_to_savings("mia", 20, "prueba")
        self.assertFalse(result["ok"])

    def test_rejects_when_liquidity_floor_breached(self):
        # Balance muy bajo -- score_liquidity real calculara pocos dias de cobertura.
        purchases = [{"category": "groceries", "date": "2026-09-01", "amount": 500}]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=15, elapsed_days=30)):
            result = aa.verified_move_to_savings("mia", 10, "prueba")
        self.assertFalse(result["ok"])

    @patch.object(aa, "sweep_to_savings")
    def test_executes_legit_amount(self, mock_sweep):
        with patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=1000, elapsed_days=90)):
            result = aa.verified_move_to_savings("mia", 20, "ahorro de prueba")
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 20.0)
        mock_sweep.assert_called_once()
        aa.table.put_item.assert_called_once()

    @patch.object(aa, "sweep_to_savings", side_effect=RuntimeError("Nessie caido"))
    def test_no_dynamo_write_when_nessie_fails(self, mock_sweep):
        with patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=1000, elapsed_days=90)):
            result = aa.verified_move_to_savings("mia", 20, "ahorro de prueba")
        self.assertFalse(result["ok"])
        aa.table.put_item.assert_not_called()


class TestVerifiedReleaseBuffer(BaseAgentActionsTest):
    def test_rejects_amount_over_cap(self):
        with patch.object(aa, "load_data", return_value=([], [], [])):
            result = aa.verified_release_buffer("mia", aa.MAX_AUTONOMOUS_SAVINGS + 1, "prueba")
        self.assertFalse(result["ok"])

    def test_rejects_when_anomaly_detected(self):
        with patch.object(aa, "load_data", return_value=([], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(anomaly_detected=True, anomaly_reason="pico de gasto")):
            result = aa.verified_release_buffer("mia", 20, "prueba")
        self.assertFalse(result["ok"])
        self.assertIn("pico de gasto", result["reason"])

    def test_rejects_when_insufficient_funds_in_pool(self):
        purchases = [{"category": "savings_transfer", "date": "2026-08-01", "amount": 40}]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with()):
            result = aa.verified_release_buffer("mia", 90, "prueba")
        self.assertFalse(result["ok"])
        self.assertIn("40", result["reason"])

    @patch.object(aa, "release_from_savings")
    def test_executes_when_funds_available(self, mock_release):
        purchases = [{"category": "savings_transfer", "date": "2026-08-01", "amount": 80}]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with()):
            result = aa.verified_release_buffer("mia", 50, "semana flaca")
        self.assertTrue(result["ok"])
        mock_release.assert_called_once()


class TestVerifiedAllocateEnvelopes(BaseAgentActionsTest):
    """Regresion directa del hallazgo de la auditoria: esta funcion calculaba
    signals["anomaly"] pero nunca lo leia, y no tenia tope de monto -- es la
    UNICA accion que se dispara 100% sola (webhook de deposito)."""

    PATTERN = {"expected_amount": 500, "tolerance_pct": 0.25, "frequency_days": 15}
    ENVELOPES = [{"category": "Gasolina", "slug": "gasolina", "monthly_target": 2000}]

    def test_rejects_deposit_that_does_not_match_pattern(self):
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN):
            result = aa.verified_allocate_envelopes("mia", 100, "2026-09-12")
        self.assertFalse(result["ok"])

    def test_rejects_when_no_envelopes_configured(self):
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN), \
             patch.object(aa, "get_envelopes", return_value=[]):
            result = aa.verified_allocate_envelopes("mia", 500, "2026-09-12")
        self.assertFalse(result["ok"])

    def test_rejects_when_anomaly_detected(self):
        """Regresion del hallazgo 1.1 de la auditoria: antes esto se ignoraba por completo."""
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN), \
             patch.object(aa, "get_envelopes", return_value=self.ENVELOPES), \
             patch.object(aa, "load_data", return_value=([{"date": "2026-08-28"}], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(anomaly_detected=True, anomaly_reason="gasto sospechoso")):
            result = aa.verified_allocate_envelopes("mia", 500, "2026-09-12")
        self.assertFalse(result["ok"])
        self.assertIn("gasto sospechoso", result["reason"])

    def test_pending_when_allocation_exceeds_autonomous_cap(self):
        """Regresion del hallazgo 1.1: antes no habia tope de monto en absoluto."""
        big_envelopes = [{"category": "Renta", "slug": "renta", "monthly_target": 3000}]  # 3000*15/30 = 1500 > tope
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN), \
             patch.object(aa, "get_envelopes", return_value=big_envelopes), \
             patch.object(aa, "load_data", return_value=([], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=5000, elapsed_days=90)):
            result = aa.verified_allocate_envelopes("mia", 500, "2026-09-12")
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("pending"))
        aa.table.put_item.assert_called_once()  # deja PENDING_ALLOCATION_SK

    def test_pending_when_liquidity_floor_breached(self):
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN), \
             patch.object(aa, "get_envelopes", return_value=self.ENVELOPES), \
             patch.object(aa, "load_data", return_value=([], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=20, elapsed_days=90)):
            result = aa.verified_allocate_envelopes("mia", 500, "2026-09-12")
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("pending"))

    @patch.object(aa, "sweep_to_savings")
    def test_executes_with_correct_proportional_amount(self, mock_sweep):
        # Nomina anterior hace 15 dias exactos -- proporcional = 2000 * 15/30 = 1000... excede el tope,
        # asi que usamos un envelope mas chico para probar el camino feliz completo.
        small_envelopes = [{"category": "Telefono", "slug": "telefono", "monthly_target": 100}]  # 100*15/30 = 50
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN), \
             patch.object(aa, "get_envelopes", return_value=small_envelopes), \
             patch.object(aa, "load_data", return_value=([{"date": "2026-08-28"}], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=5000, elapsed_days=90)):
            result = aa.verified_allocate_envelopes("mia", 500, "2026-09-12")
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 50.0)
        mock_sweep.assert_called_once()

    def test_get_pending_allocation_returns_none_when_nothing_pending(self):
        aa.table.get_item.return_value = {}
        self.assertIsNone(aa.get_pending_allocation("mia"))

    def test_get_pending_allocation_returns_stored_proposal(self):
        aa.table.get_item.return_value = {"Item": {
            "proposals": [{"category": "Gasolina", "slug": "gasolina", "amount": 50}],
            "deposit_date": "2026-09-12", "total": 50,
        }}
        pending = aa.get_pending_allocation("mia")
        self.assertEqual(pending["total"], 50.0)
        self.assertEqual(pending["proposals"][0]["slug"], "gasolina")

    def test_confirm_pending_allocation_without_pending_rejects(self):
        aa.table.get_item.return_value = {}
        result = aa.confirm_pending_allocation("mia")
        self.assertFalse(result["ok"])

    @patch.object(aa, "sweep_to_savings")
    def test_confirm_pending_allocation_executes_stored_proposal(self, mock_sweep):
        aa.table.get_item.return_value = {"Item": {
            "proposals": [{"category": "Gasolina", "slug": "gasolina", "amount": 50}],
            "deposit_date": "2026-09-12", "total": 50,
        }}
        result = aa.confirm_pending_allocation("mia")
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 50.0)
        mock_sweep.assert_called_once()


class TestStopBillTwoStepFlow(BaseAgentActionsTest):
    """Regresion directa del hallazgo 1.2: antes stop_subscription ejecutaba
    en una sola llamada del chat, sin ningun paso de confirmacion a nivel
    de codigo."""

    LEAK_BILL = {"payee": "Gym Co", "status": "recurring", "payment_amount": 40.0, "bill_id": "b1"}
    LEAK_SIGNALS = {**signals_with(), "alerts": [{"title": "Gym Co"}]}

    def test_propose_rejects_unknown_bill(self):
        with patch.object(aa, "load_data", return_value=([], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with()):
            result = aa.propose_stop_bill("mia", "Netflix")
        self.assertFalse(result["ok"])
        self.assertNotIn("pending", result)

    def test_propose_rejects_healthy_bill(self):
        healthy_bill = {**self.LEAK_BILL, "payee": "Telco Co"}
        with patch.object(aa, "load_data", return_value=([], [], [healthy_bill])), \
             patch.object(aa, "get_full_signals", return_value=signals_with()):  # alerts vacios -> no es fuga
            result = aa.propose_stop_bill("mia", "Telco Co")
        self.assertFalse(result["ok"])
        self.assertFalse(result.get("pending"))

    @patch.object(aa, "stop_recurring_bill")
    def test_propose_never_executes_even_for_a_real_leak(self, mock_stop):
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=self.LEAK_SIGNALS):
            result = aa.propose_stop_bill("mia", "Gym Co")
        self.assertFalse(result["ok"])
        self.assertTrue(result["pending"])
        mock_stop.assert_not_called()  # el punto central del fix: NUNCA ejecuta en la propuesta
        aa.table.put_item.assert_called_once()

    def test_confirm_without_pending_rejects(self):
        aa.table.get_item.return_value = {}
        result = aa.confirm_stop_bill("mia")
        self.assertFalse(result["ok"])

    @patch.object(aa, "stop_recurring_bill")
    def test_confirm_executes_after_valid_pending(self, mock_stop):
        aa.table.get_item.return_value = {"Item": {"bill_id": "b1", "payee": "Gym Co", "payment_amount": 40.0}}
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=self.LEAK_SIGNALS):
            result = aa.confirm_stop_bill("mia")
        self.assertTrue(result["ok"])
        mock_stop.assert_called_once()

    @patch.object(aa, "stop_recurring_bill")
    def test_confirm_revalidates_and_rejects_if_no_longer_a_leak(self, mock_stop):
        """El bill ya no esta marcado como fuga entre el propose y el confirm
        (ej. alguien lo volvio a usar) -- confirm no debe confiar ciegamente
        en la propuesta guardada."""
        aa.table.get_item.return_value = {"Item": {"bill_id": "b1", "payee": "Gym Co", "payment_amount": 40.0}}
        no_longer_leak_signals = signals_with()  # alerts vacios ahora
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=no_longer_leak_signals):
            result = aa.confirm_stop_bill("mia")
        self.assertFalse(result["ok"])
        mock_stop.assert_not_called()

    @patch.object(aa, "stop_recurring_bill")
    def test_verified_stop_bill_direct_execute_used_by_advance_day(self, mock_stop):
        """advance-day si ejecuta directo -- su propio checkpoint YA es el paso de confirmacion."""
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=self.LEAK_SIGNALS):
            result = aa.verified_stop_bill("mia", "Gym Co")
        self.assertTrue(result["ok"])
        mock_stop.assert_called_once()


class TestDepositMatchesIncomePattern(unittest.TestCase):
    def test_no_pattern_never_matches(self):
        self.assertFalse(aa.deposit_matches_income_pattern(None, 500))

    def test_within_tolerance_matches(self):
        pattern = {"expected_amount": 500, "tolerance_pct": 0.25}
        self.assertTrue(aa.deposit_matches_income_pattern(pattern, 580))  # 16% de diferencia

    def test_outside_tolerance_does_not_match(self):
        pattern = {"expected_amount": 500, "tolerance_pct": 0.25}
        self.assertFalse(aa.deposit_matches_income_pattern(pattern, 100))  # un deposito random tipo "amigo te presta $100"


class TestSuggestedIncomeTolerance(BaseAgentActionsTest):
    """Regresion directa del hallazgo: con +-25% fijo, 4 de los 8 depositos
    reales de Mia (ingreso freelance real: 300, 380, 450, 620, 650, 690,
    710, 720) quedaban FUERA de su propio patron de nomina -- justo el
    perfil de ingreso irregular que el proyecto dice servir, rechazado por
    su propia verificacion."""

    MIA_DEPOSITS = [{"amount": a, "category": "income"} for a in [300, 380, 450, 620, 650, 690, 710, 720]]

    def test_default_floor_with_insufficient_history(self):
        with patch.object(aa, "load_data", return_value=([{"amount": 500, "category": "income"}], [], [])):
            self.assertEqual(aa.suggested_income_tolerance("mia"), aa.DEFAULT_INCOME_TOLERANCE)

    def test_derived_tolerance_covers_all_of_mias_real_deposits(self):
        with patch.object(aa, "load_data", return_value=(self.MIA_DEPOSITS, [], [])):
            tolerance = aa.suggested_income_tolerance("mia")
        mean = sum(d["amount"] for d in self.MIA_DEPOSITS) / len(self.MIA_DEPOSITS)
        pattern = {"expected_amount": mean, "tolerance_pct": tolerance}
        for d in self.MIA_DEPOSITS:
            self.assertTrue(aa.deposit_matches_income_pattern(pattern, d["amount"]), f"deposito real ${d['amount']} deberia matchear su propio patron")

    def test_derived_tolerance_still_rejects_random_deposit(self):
        with patch.object(aa, "load_data", return_value=(self.MIA_DEPOSITS, [], [])):
            tolerance = aa.suggested_income_tolerance("mia")
        mean = sum(d["amount"] for d in self.MIA_DEPOSITS) / len(self.MIA_DEPOSITS)
        pattern = {"expected_amount": mean, "tolerance_pct": tolerance}
        self.assertFalse(aa.deposit_matches_income_pattern(pattern, 100))  # el amigo prestando $100 sigue sin colar

    def test_fixed_25_percent_would_have_missed_half_of_mias_deposits(self):
        """No es un test del codigo actual -- documenta el bug original
        para que nadie baje la tolerancia derivada de vuelta a un 25% fijo
        sin darse cuenta de lo que rompe."""
        mean = sum(d["amount"] for d in self.MIA_DEPOSITS) / len(self.MIA_DEPOSITS)
        pattern = {"expected_amount": mean, "tolerance_pct": 0.25}
        missed = [d["amount"] for d in self.MIA_DEPOSITS if not aa.deposit_matches_income_pattern(pattern, d["amount"])]
        self.assertEqual(len(missed), 4)


class TestSetIncomePattern(BaseAgentActionsTest):
    def test_rejects_invalid_amount(self):
        result = aa.set_income_pattern("mia", "no-es-numero", 15)
        self.assertFalse(result["ok"])

    def test_uses_derived_tolerance_when_not_specified(self):
        with patch.object(aa, "load_data", return_value=([{"amount": a, "category": "income"} for a in [300, 720]] * 2, [], [])):
            result = aa.set_income_pattern("mia", 500, 15)
        self.assertTrue(result["ok"])
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertNotEqual(float(saved_item["tolerance_pct"]), 0.25)

    def test_explicit_tolerance_overrides_derived_one(self):
        with patch.object(aa, "load_data", return_value=([{"amount": a, "category": "income"} for a in [300, 720]] * 2, [], [])):
            result = aa.set_income_pattern("mia", 500, 15, tolerance_pct=0.10)
        self.assertTrue(result["ok"])
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(float(saved_item["tolerance_pct"]), 0.10)


class TestCreateEnvelope(BaseAgentActionsTest):
    """create_envelope hace upsert por slug -- es la misma funcion que usa
    la UI tanto para crear un apartado nuevo como para editar uno
    existente (regresion del hallazgo: no habia forma de editar la meta
    mensual de un apartado ya creado)."""

    def test_creating_new_envelope_says_creado(self):
        aa.table.get_item.return_value = {}
        result = aa.create_envelope("mia", "Gasolina", 2000)
        self.assertTrue(result["ok"])
        self.assertIn("creado", result["message"])

    def test_updating_existing_envelope_says_actualizado_not_duplicado(self):
        aa.table.get_item.return_value = {"Item": {"category": "Gasolina", "slug": "gasolina", "monthly_target": 2000, "created_at": "2026-01-01"}}
        result = aa.create_envelope("mia", "Gasolina", 2500)
        self.assertTrue(result["ok"])
        self.assertIn("actualizado", result["message"])
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(float(saved_item["monthly_target"]), 2500)
        self.assertEqual(saved_item["created_at"], "2026-01-01")  # no se pisa la fecha de creacion original

    def test_rejects_missing_category(self):
        result = aa.create_envelope("mia", "", 500)
        self.assertFalse(result["ok"])

    def test_rejects_non_positive_target(self):
        aa.table.get_item.return_value = {}
        result = aa.create_envelope("mia", "Gasolina", 0)
        self.assertFalse(result["ok"])


class TestSimulateThirdPartyPayroll(BaseAgentActionsTest):
    """Nomina real de un tercero (otra cuenta Nessie, no la nuestra) -- ver
    backend/signals-lambda/agent_actions.py::simulate_third_party_payroll.
    A proposito NO se prueba que reparta a apartados aqui: eso lo decide el
    mismo camino reactivo de cualquier deposito (lambda_notifier ->
    verified_allocate_envelopes), ya cubierto por TestVerifiedAllocateEnvelopes.
    Esta funcion solo es responsable de mover el dinero y escribir el deposito."""

    def setUp(self):
        super().setUp()
        self.load_data_patch = patch.object(aa, "load_data", return_value=([], [{"date": "2026-09-12", "amount": 10}], []))
        self.load_data_patch.start()
        self.addCleanup(self.load_data_patch.stop)

    @patch.object(aa, "receive_from_third_party")
    def test_uses_declared_income_pattern_amount_when_not_specified(self, mock_receive):
        aa.table.get_item.return_value = {"Item": {"expected_amount": 565, "frequency_days": 15, "tolerance_pct": 0.3}}
        mock_receive.return_value = {"withdrawal": {}, "deposit": {}}
        result = aa.simulate_third_party_payroll("mia")
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 565.0)
        self.assertTrue(result["matches_income_pattern"])
        mock_receive.assert_called_once()
        self.assertEqual(mock_receive.call_args.args[2], 565.0)

    def test_rejects_when_no_amount_and_no_pattern_declared(self):
        aa.table.get_item.return_value = {}
        result = aa.simulate_third_party_payroll("mia")
        self.assertFalse(result["ok"])

    @patch.object(aa, "receive_from_third_party")
    def test_flags_amount_that_does_not_match_income_pattern(self, mock_receive):
        aa.table.get_item.return_value = {"Item": {"expected_amount": 565, "frequency_days": 15, "tolerance_pct": 0.3}}
        mock_receive.return_value = {"withdrawal": {}, "deposit": {}}
        result = aa.simulate_third_party_payroll("mia", amount=100)
        self.assertTrue(result["ok"])  # el deposito si se hace/registra
        self.assertFalse(result["matches_income_pattern"])  # pero no se reparte solo

    @patch.object(aa, "receive_from_third_party", side_effect=RuntimeError("Nessie caido"))
    def test_nessie_failure_does_not_write_deposit(self, mock_receive):
        aa.table.get_item.return_value = {"Item": {"expected_amount": 565, "frequency_days": 15, "tolerance_pct": 0.3}}
        result = aa.simulate_third_party_payroll("mia")
        self.assertFalse(result["ok"])
        aa.table.put_item.assert_not_called()

    @patch.object(aa, "receive_from_third_party")
    def test_writes_deposit_with_third_party_category_for_reset_cleanup(self, mock_receive):
        aa.table.get_item.return_value = {"Item": {"expected_amount": 565, "frequency_days": 15, "tolerance_pct": 0.3}}
        mock_receive.return_value = {"withdrawal": {}, "deposit": {}}
        aa.simulate_third_party_payroll("mia")
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(saved_item["type"], "deposit")
        self.assertEqual(saved_item["category"], aa.THIRD_PARTY_INCOME_CATEGORY)

    def test_rejects_non_positive_explicit_amount(self):
        aa.table.get_item.return_value = {}
        result = aa.simulate_third_party_payroll("mia", amount=0)
        self.assertFalse(result["ok"])


class TestVerifiedActionHistoryDedup(BaseAgentActionsTest):
    def _mock_two_queries(self, action_items, notif_items):
        # get_verified_action_history llama table.query() dos veces en orden
        # fijo: ACTION# primero, NOTIFICATION# despues (ver el codigo fuente) --
        # el objeto KeyConditionExpression de boto3 no se puede inspeccionar
        # por texto (su __str__ es solo la direccion de memoria), asi que se
        # usa el orden de las llamadas en vez de intentar parsear la expresion.
        aa.table.query.side_effect = [{"Items": action_items}, {"Items": notif_items}]

    def test_dedup_within_five_seconds_keeps_only_action_entry(self):
        action_items = [{"sk": "ACTION#2026-08-16#1000000000000", "date": "2026-08-16", "type": "bill_stopped", "text": "detuve el cargo", "requires_confirmation": False}]
        notif_items = [{"sk": "NOTIFICATION#2026-08-16#1000000002000", "date": "2026-08-16", "text": "se detuvo el cargo automatico"}]  # 2s de diferencia
        self._mock_two_queries(action_items, notif_items)
        events = aa.get_verified_action_history("mia")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "bill_stopped")

    def test_events_far_apart_are_not_deduplicated(self):
        action_items = [{"sk": "ACTION#2026-08-16#1000000000000", "date": "2026-08-16", "type": "bill_stopped", "text": "detuve el cargo", "requires_confirmation": False}]
        notif_items = [{"sk": "NOTIFICATION#2026-08-16#1000030000000", "date": "2026-08-16", "text": "se movieron $15 a tu ahorro"}]  # 30s despues, evento distinto
        self._mock_two_queries(action_items, notif_items)
        events = aa.get_verified_action_history("mia")
        self.assertEqual(len(events), 2)

    def test_sorted_by_date_then_timestamp(self):
        action_items = [
            {"sk": "ACTION#2026-08-16#2000000000000", "date": "2026-08-16", "type": "savings_moved", "text": "b", "requires_confirmation": False},
            {"sk": "ACTION#2026-08-15#1000000000000", "date": "2026-08-15", "type": "leak_detected", "text": "a", "requires_confirmation": True},
        ]
        self._mock_two_queries(action_items, [])
        events = aa.get_verified_action_history("mia")
        self.assertEqual([e["text"] for e in events], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
