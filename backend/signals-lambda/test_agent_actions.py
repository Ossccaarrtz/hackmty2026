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
import time
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


class TestGetAccountIds(unittest.TestCase):
    """Regresion directa de un hallazgo real: CHECKING_ID/SAVINGS_ID estaban
    hardcodeados a una sola cuenta -- cualquier accion de dinero disparada
    con un user_id sin cuenta propia registrada habria escrito de verdad en
    la cuenta de otra persona, aunque el registro en DynamoDB dijera el
    user_id correcto."""

    def test_known_user_gets_its_own_account(self):
        ana = aa.get_account_ids("ana")
        self.assertEqual(ana["checking"], aa.CHECKING_ID)
        self.assertEqual(ana["savings"], aa.SAVINGS_ID)

    def test_unknown_user_falls_back_to_ana(self):
        self.assertEqual(aa.get_account_ids("alguien-nuevo"), aa.get_account_ids("ana"))


class TestVerifiedMoveToSavings(BaseAgentActionsTest):
    def setUp(self):
        super().setUp()
        self.load_data_patch = patch.object(aa, "load_data", return_value=([], [], []))
        self.load_data_patch.start()
        self.addCleanup(self.load_data_patch.stop)

    def test_rejects_invalid_amount(self):
        result = aa.verified_move_to_savings("ana", "no-es-numero", "prueba")
        self.assertFalse(result["ok"])

    def test_rejects_non_positive_amount(self):
        result = aa.verified_move_to_savings("ana", 0, "prueba")
        self.assertFalse(result["ok"])

    def test_rejects_amount_over_cap(self):
        result = aa.verified_move_to_savings("ana", aa.MAX_AUTONOMOUS_SAVINGS + 1, "prueba")
        self.assertFalse(result["ok"])
        self.assertIn("limite", result["reason"].lower() + result["reason"])

    def test_rejects_when_anomaly_detected(self):
        with patch.object(aa, "get_full_signals", return_value=signals_with(anomaly_detected=True, anomaly_reason="gasto atipico")):
            result = aa.verified_move_to_savings("ana", 20, "prueba")
        self.assertFalse(result["ok"])
        self.assertIn("gasto atipico", result["reason"])

    def test_rejects_when_daily_cap_already_reached(self):
        today = date.today().isoformat()
        purchases_today = [{"category": "savings_transfer", "date": today, "amount": 90}]
        with patch.object(aa, "load_data", return_value=([], purchases_today, [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with()):
            result = aa.verified_move_to_savings("ana", 20, "prueba")
        self.assertFalse(result["ok"])

    def test_rejects_when_liquidity_floor_breached(self):
        # Balance muy bajo -- score_liquidity real calculara pocos dias de cobertura.
        purchases = [{"category": "groceries", "date": "2026-09-01", "amount": 500}]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=15, elapsed_days=30)):
            result = aa.verified_move_to_savings("ana", 10, "prueba")
        self.assertFalse(result["ok"])

    @patch.object(aa, "sweep_to_savings")
    def test_executes_legit_amount(self, mock_sweep):
        with patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=1000, elapsed_days=90)):
            result = aa.verified_move_to_savings("ana", 20, "ahorro de prueba")
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 20.0)
        mock_sweep.assert_called_once()
        aa.table.put_item.assert_called_once()

    @patch.object(aa, "sweep_to_savings", side_effect=RuntimeError("Nessie caido"))
    def test_no_dynamo_write_when_nessie_fails(self, mock_sweep):
        with patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=1000, elapsed_days=90)):
            result = aa.verified_move_to_savings("ana", 20, "ahorro de prueba")
        self.assertFalse(result["ok"])
        aa.table.put_item.assert_not_called()


class TestVerifiedReleaseBuffer(BaseAgentActionsTest):
    def test_rejects_amount_over_cap(self):
        with patch.object(aa, "load_data", return_value=([], [], [])):
            result = aa.verified_release_buffer("ana", aa.MAX_AUTONOMOUS_SAVINGS + 1, "prueba")
        self.assertFalse(result["ok"])

    def test_rejects_when_anomaly_detected(self):
        with patch.object(aa, "load_data", return_value=([], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(anomaly_detected=True, anomaly_reason="pico de gasto")):
            result = aa.verified_release_buffer("ana", 20, "prueba")
        self.assertFalse(result["ok"])
        self.assertIn("pico de gasto", result["reason"])

    def test_rejects_when_insufficient_funds_in_pool(self):
        purchases = [{"category": "savings_transfer", "date": "2026-08-01", "amount": 40}]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with()):
            result = aa.verified_release_buffer("ana", 90, "prueba")
        self.assertFalse(result["ok"])
        self.assertIn("40", result["reason"])

    @patch.object(aa, "release_from_savings")
    def test_executes_when_funds_available(self, mock_release):
        purchases = [{"category": "savings_transfer", "date": "2026-08-01", "amount": 80}]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with()):
            result = aa.verified_release_buffer("ana", 50, "semana flaca")
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
            result = aa.verified_allocate_envelopes("ana", 100, "2026-09-12")
        self.assertFalse(result["ok"])

    def test_rejects_when_no_envelopes_configured(self):
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN), \
             patch.object(aa, "get_envelopes", return_value=[]):
            result = aa.verified_allocate_envelopes("ana", 500, "2026-09-12")
        self.assertFalse(result["ok"])

    def test_rejects_when_anomaly_detected(self):
        """Regresion del hallazgo 1.1 de la auditoria: antes esto se ignoraba por completo."""
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN), \
             patch.object(aa, "get_envelopes", return_value=self.ENVELOPES), \
             patch.object(aa, "load_data", return_value=([{"date": "2026-08-28"}], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(anomaly_detected=True, anomaly_reason="gasto sospechoso")):
            result = aa.verified_allocate_envelopes("ana", 500, "2026-09-12")
        self.assertFalse(result["ok"])
        self.assertIn("gasto sospechoso", result["reason"])

    def test_pending_when_allocation_exceeds_autonomous_cap(self):
        """Regresion del hallazgo 1.1: antes no habia tope de monto en absoluto."""
        big_envelopes = [{"category": "Renta", "slug": "renta", "monthly_target": 3000}]  # 3000*15/30 = 1500 > tope
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN), \
             patch.object(aa, "get_envelopes", return_value=big_envelopes), \
             patch.object(aa, "load_data", return_value=([], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=5000, elapsed_days=90)):
            result = aa.verified_allocate_envelopes("ana", 500, "2026-09-12")
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("pending"))
        aa.table.put_item.assert_called_once()  # deja PENDING_ALLOCATION_SK

    def test_pending_when_liquidity_floor_breached(self):
        with patch.object(aa, "get_income_pattern", return_value=self.PATTERN), \
             patch.object(aa, "get_envelopes", return_value=self.ENVELOPES), \
             patch.object(aa, "load_data", return_value=([], [], [])), \
             patch.object(aa, "get_full_signals", return_value=signals_with(current_balance=20, elapsed_days=90)):
            result = aa.verified_allocate_envelopes("ana", 500, "2026-09-12")
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
            result = aa.verified_allocate_envelopes("ana", 500, "2026-09-12")
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 50.0)
        mock_sweep.assert_called_once()

    def test_get_pending_allocation_returns_none_when_nothing_pending(self):
        aa.table.get_item.return_value = {}
        self.assertIsNone(aa.get_pending_allocation("ana"))

    def test_get_pending_allocation_returns_stored_proposal(self):
        aa.table.get_item.return_value = {"Item": {
            "proposals": [{"category": "Gasolina", "slug": "gasolina", "amount": 50}],
            "deposit_date": "2026-09-12", "total": 50,
        }}
        pending = aa.get_pending_allocation("ana")
        self.assertEqual(pending["total"], 50.0)
        self.assertEqual(pending["proposals"][0]["slug"], "gasolina")

    def test_confirm_pending_allocation_without_pending_rejects(self):
        aa.table.get_item.return_value = {}
        result = aa.confirm_pending_allocation("ana")
        self.assertFalse(result["ok"])

    @patch.object(aa, "sweep_to_savings")
    def test_confirm_pending_allocation_executes_stored_proposal(self, mock_sweep):
        aa.table.get_item.return_value = {"Item": {
            "proposals": [{"category": "Gasolina", "slug": "gasolina", "amount": 50}],
            "deposit_date": "2026-09-12", "total": 50,
        }}
        result = aa.confirm_pending_allocation("ana")
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
            result = aa.propose_stop_bill("ana", "Netflix")
        self.assertFalse(result["ok"])
        self.assertNotIn("pending", result)

    def test_propose_rejects_healthy_bill(self):
        healthy_bill = {**self.LEAK_BILL, "payee": "Telco Co"}
        with patch.object(aa, "load_data", return_value=([], [], [healthy_bill])), \
             patch.object(aa, "get_full_signals", return_value=signals_with()):  # alerts vacios -> no es fuga
            result = aa.propose_stop_bill("ana", "Telco Co")
        self.assertFalse(result["ok"])
        self.assertFalse(result.get("pending"))

    @patch.object(aa, "stop_recurring_bill")
    def test_propose_never_executes_even_for_a_real_leak(self, mock_stop):
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=self.LEAK_SIGNALS):
            result = aa.propose_stop_bill("ana", "Gym Co")
        self.assertFalse(result["ok"])
        self.assertTrue(result["pending"])
        mock_stop.assert_not_called()  # el punto central del fix: NUNCA ejecuta en la propuesta
        aa.table.put_item.assert_called_once()

    def test_confirm_without_pending_rejects(self):
        aa.table.get_item.return_value = {}
        result = aa.confirm_stop_bill("ana")
        self.assertFalse(result["ok"])

    @patch.object(aa, "stop_recurring_bill")
    def test_confirm_executes_after_valid_pending(self, mock_stop):
        aa.table.get_item.return_value = {"Item": {"bill_id": "b1", "payee": "Gym Co", "payment_amount": 40.0}}
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=self.LEAK_SIGNALS):
            result = aa.confirm_stop_bill("ana")
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
            result = aa.confirm_stop_bill("ana")
        self.assertFalse(result["ok"])
        mock_stop.assert_not_called()

    @patch.object(aa, "stop_recurring_bill")
    def test_verified_stop_bill_direct_execute_used_by_advance_day(self, mock_stop):
        """advance-day si ejecuta directo -- su propio checkpoint YA es el paso de confirmacion."""
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=self.LEAK_SIGNALS):
            result = aa.verified_stop_bill("ana", "Gym Co")
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
    """Regresion directa del hallazgo: con +-25% fijo, 2 de los 8 depositos
    reales de Ana (mesada/medio tiempo real: 1150, 1200, 1350, 1450, 1700,
    1800, 1900, 2100 MXN) quedaban FUERA de su propio patron de nomina --
    justo el perfil de ingreso irregular que el proyecto dice servir,
    rechazado por su propia verificacion."""

    ANA_DEPOSITS = [{"amount": a, "category": "income"} for a in [1200, 1450, 1800, 1350, 1900, 1150, 1700, 2100]]

    def test_default_floor_with_insufficient_history(self):
        with patch.object(aa, "load_data", return_value=([{"amount": 500, "category": "income"}], [], [])):
            self.assertEqual(aa.suggested_income_tolerance("ana"), aa.DEFAULT_INCOME_TOLERANCE)

    def test_derived_tolerance_covers_all_of_anas_real_deposits(self):
        with patch.object(aa, "load_data", return_value=(self.ANA_DEPOSITS, [], [])):
            tolerance = aa.suggested_income_tolerance("ana")
        mean = sum(d["amount"] for d in self.ANA_DEPOSITS) / len(self.ANA_DEPOSITS)
        pattern = {"expected_amount": mean, "tolerance_pct": tolerance}
        for d in self.ANA_DEPOSITS:
            self.assertTrue(aa.deposit_matches_income_pattern(pattern, d["amount"]), f"deposito real ${d['amount']} deberia matchear su propio patron")

    def test_derived_tolerance_still_rejects_random_deposit(self):
        with patch.object(aa, "load_data", return_value=(self.ANA_DEPOSITS, [], [])):
            tolerance = aa.suggested_income_tolerance("ana")
        mean = sum(d["amount"] for d in self.ANA_DEPOSITS) / len(self.ANA_DEPOSITS)
        pattern = {"expected_amount": mean, "tolerance_pct": tolerance}
        self.assertFalse(aa.deposit_matches_income_pattern(pattern, 100))  # un amigo prestando $100 sigue sin colar

    def test_fixed_25_percent_would_have_missed_some_of_anas_deposits(self):
        """No es un test del codigo actual -- documenta el bug original
        para que nadie baje la tolerancia derivada de vuelta a un 25% fijo
        sin darse cuenta de lo que rompe."""
        mean = sum(d["amount"] for d in self.ANA_DEPOSITS) / len(self.ANA_DEPOSITS)
        pattern = {"expected_amount": mean, "tolerance_pct": 0.25}
        missed = [d["amount"] for d in self.ANA_DEPOSITS if not aa.deposit_matches_income_pattern(pattern, d["amount"])]
        self.assertEqual(len(missed), 2)


class TestSetIncomePattern(BaseAgentActionsTest):
    def test_rejects_invalid_amount(self):
        result = aa.set_income_pattern("ana", "no-es-numero", 15)
        self.assertFalse(result["ok"])

    def test_uses_derived_tolerance_when_not_specified(self):
        with patch.object(aa, "load_data", return_value=([{"amount": a, "category": "income"} for a in [300, 720]] * 2, [], [])):
            result = aa.set_income_pattern("ana", 500, 15)
        self.assertTrue(result["ok"])
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertNotEqual(float(saved_item["tolerance_pct"]), 0.25)

    def test_explicit_tolerance_overrides_derived_one(self):
        with patch.object(aa, "load_data", return_value=([{"amount": a, "category": "income"} for a in [300, 720]] * 2, [], [])):
            result = aa.set_income_pattern("ana", 500, 15, tolerance_pct=0.10)
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
        result = aa.create_envelope("ana", "Gasolina", 2000)
        self.assertTrue(result["ok"])
        self.assertIn("creado", result["message"])

    def test_updating_existing_envelope_says_actualizado_not_duplicado(self):
        aa.table.get_item.return_value = {"Item": {"category": "Gasolina", "slug": "gasolina", "monthly_target": 2000, "created_at": "2026-01-01"}}
        result = aa.create_envelope("ana", "Gasolina", 2500)
        self.assertTrue(result["ok"])
        self.assertIn("actualizado", result["message"])
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(float(saved_item["monthly_target"]), 2500)
        self.assertEqual(saved_item["created_at"], "2026-01-01")  # no se pisa la fecha de creacion original

    def test_rejects_missing_category(self):
        result = aa.create_envelope("ana", "", 500)
        self.assertFalse(result["ok"])

    def test_rejects_non_positive_target(self):
        aa.table.get_item.return_value = {}
        result = aa.create_envelope("ana", "Gasolina", 0)
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
        result = aa.simulate_third_party_payroll("ana")
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 565.0)
        self.assertTrue(result["matches_income_pattern"])
        mock_receive.assert_called_once()
        self.assertEqual(mock_receive.call_args.args[2], 565.0)

    def test_rejects_when_no_amount_and_no_pattern_declared(self):
        aa.table.get_item.return_value = {}
        result = aa.simulate_third_party_payroll("ana")
        self.assertFalse(result["ok"])

    @patch.object(aa, "receive_from_third_party")
    def test_flags_amount_that_does_not_match_income_pattern(self, mock_receive):
        aa.table.get_item.return_value = {"Item": {"expected_amount": 565, "frequency_days": 15, "tolerance_pct": 0.3}}
        mock_receive.return_value = {"withdrawal": {}, "deposit": {}}
        result = aa.simulate_third_party_payroll("ana", amount=100)
        self.assertTrue(result["ok"])  # el deposito si se hace/registra
        self.assertFalse(result["matches_income_pattern"])  # pero no se reparte solo

    @patch.object(aa, "receive_from_third_party", side_effect=RuntimeError("Nessie caido"))
    def test_nessie_failure_does_not_write_deposit(self, mock_receive):
        aa.table.get_item.return_value = {"Item": {"expected_amount": 565, "frequency_days": 15, "tolerance_pct": 0.3}}
        result = aa.simulate_third_party_payroll("ana")
        self.assertFalse(result["ok"])
        aa.table.put_item.assert_not_called()

    @patch.object(aa, "receive_from_third_party")
    def test_writes_deposit_with_third_party_category_for_reset_cleanup(self, mock_receive):
        aa.table.get_item.return_value = {"Item": {"expected_amount": 565, "frequency_days": 15, "tolerance_pct": 0.3}}
        mock_receive.return_value = {"withdrawal": {}, "deposit": {}}
        aa.simulate_third_party_payroll("ana")
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(saved_item["type"], "deposit")
        self.assertEqual(saved_item["category"], aa.THIRD_PARTY_INCOME_CATEGORY)

    def test_rejects_non_positive_explicit_amount(self):
        aa.table.get_item.return_value = {}
        result = aa.simulate_third_party_payroll("ana", amount=0)
        self.assertFalse(result["ok"])


class TestLogExternalExpense(BaseAgentActionsTest):
    """Gasto declarado por el usuario en efectivo u otra tarjeta -- no paso
    por la tarjeta del banco aliado. Prioridad de estos tests: (1)
    validacion estricta (monto, fuente, categoria, nombre de tarjeta), y
    (2) que quede marcado con el 'source' correcto para que el resto del
    motor (activacion, balance) lo excluya donde debe."""

    def setUp(self):
        super().setUp()
        self.load_data_patch = patch.object(aa, "load_data", return_value=([], [], []))
        self.load_data_patch.start()
        self.addCleanup(self.load_data_patch.stop)

    def test_rejects_invalid_amount(self):
        result = aa.log_external_expense("ana", "no-es-numero", "groceries", "tacos", "cash")
        self.assertFalse(result["ok"])

    def test_rejects_non_positive_amount(self):
        result = aa.log_external_expense("ana", 0, "groceries", "tacos", "cash")
        self.assertFalse(result["ok"])

    def test_rejects_unknown_source(self):
        result = aa.log_external_expense("ana", 100, "groceries", "tacos", "crypto")
        self.assertFalse(result["ok"])

    def test_rejects_unknown_category(self):
        result = aa.log_external_expense("ana", 100, "mascotas", "perrijunior", "cash")
        self.assertFalse(result["ok"])

    def test_other_card_without_card_name_asks_for_it(self):
        result = aa.log_external_expense("ana", 100, "groceries", "tacos", "other_card")
        self.assertFalse(result["ok"])
        self.assertIn("tarjeta", result["reason"].lower())
        aa.table.put_item.assert_not_called()

    def test_valid_cash_expense_writes_correct_item(self):
        result = aa.log_external_expense("ana", 150, "discretionary", "tacos con amigos", "cash")
        self.assertTrue(result["ok"])
        saved = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(saved["source"], "cash")
        self.assertIsNone(saved["card_name"])
        self.assertEqual(saved["type"], "purchase")
        self.assertEqual(float(saved["amount"]), 150)

    def test_valid_other_card_expense_writes_card_name(self):
        result = aa.log_external_expense("ana", 300, "transport", "gasolina", "other_card", card_name="Banorte")
        self.assertTrue(result["ok"])
        saved = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(saved["source"], "other_card")
        self.assertEqual(saved["card_name"], "Banorte")

    def test_duplicate_within_window_is_rejected(self):
        existing = {
            "sk": "TXN#2026-09-12#external1", "type": "purchase", "date": "2026-09-12",
            "amount": 150, "category": "discretionary", "source": "cash", "card_name": None,
            "logged_at_ms": int(time.time() * 1000),
        }
        with patch.object(aa, "load_data", return_value=([], [existing], [])):
            result = aa.log_external_expense("ana", 150, "discretionary", "tacos", "cash")
        self.assertFalse(result["ok"])
        aa.table.put_item.assert_not_called()

    def test_different_amount_within_window_is_not_a_duplicate(self):
        existing = {
            "sk": "TXN#2026-09-12#external1", "type": "purchase", "date": "2026-09-12",
            "amount": 150, "category": "discretionary", "source": "cash", "card_name": None,
            "logged_at_ms": int(time.time() * 1000),
        }
        with patch.object(aa, "load_data", return_value=([], [existing], [])):
            result = aa.log_external_expense("ana", 200, "discretionary", "cine", "cash")
        self.assertTrue(result["ok"])

    def test_old_entry_outside_window_is_not_a_duplicate(self):
        stale = {
            "sk": "TXN#2026-09-12#external1", "type": "purchase", "date": "2026-09-12",
            "amount": 150, "category": "discretionary", "source": "cash", "card_name": None,
            "logged_at_ms": int(time.time() * 1000) - (aa.EXTERNAL_DEDUP_WINDOW_MS + 5000),
        }
        with patch.object(aa, "load_data", return_value=([], [stale], [])):
            result = aa.log_external_expense("ana", 150, "discretionary", "tacos otra vez", "cash")
        self.assertTrue(result["ok"])


class TestSimulateDecision(BaseAgentActionsTest):
    """Simulador financiero educativo -- responde '¿que pasaria con mi
    score si...?' sin ejecutar nada real. Prioridad de estos tests: (1)
    nunca debe escribir nada (ni put_item ni update_item), sin importar la
    accion o el resultado, y (2) el numero que regresa debe salir del MISMO
    signal_engine.compute_signals que usa el dashboard real, no una formula
    aparte inventada para la simulacion."""

    DEPOSITS = [
        {"date": "2026-07-01", "amount": 600, "category": "income"},
        {"date": "2026-08-01", "amount": 600, "category": "income"},
        {"date": "2026-09-01", "amount": 600, "category": "income"},
    ]
    PURCHASES = [
        {"date": "2026-07-05", "amount": 400, "category": "rent", "merchant_name": "Landlord"},
        {"date": "2026-08-05", "amount": 400, "category": "rent", "merchant_name": "Landlord"},
        {"date": "2026-09-05", "amount": 400, "category": "rent", "merchant_name": "Landlord"},
        {"date": "2026-09-08", "amount": 300, "category": "discretionary", "merchant_name": "CineMax"},
        {"date": "2026-09-09", "amount": 200, "category": "discretionary", "merchant_name": "CineMax"},
    ]
    BILLS = [{"payee": "Gym Co", "status": "recurring", "payment_amount": 40, "bill_id": "b1"}]

    def setUp(self):
        super().setUp()
        self.load_data_patch = patch.object(aa, "load_data", return_value=(self.DEPOSITS, self.PURCHASES, self.BILLS))
        self.load_data_patch.start()
        self.addCleanup(self.load_data_patch.stop)
        self.history_patch = patch.object(aa, "get_score_history", return_value=[])
        self.history_patch.start()
        self.addCleanup(self.history_patch.stop)

    def test_rejects_unknown_action(self):
        result = aa.simulate_decision("ana", "delete_everything")
        self.assertFalse(result["ok"])

    def test_stop_bill_rejects_nonexistent_payee(self):
        result = aa.simulate_decision("ana", "stop_bill", {"bill_payee": "Netflix"})
        self.assertFalse(result["ok"])

    def test_stop_bill_rejects_already_cancelled_bill(self):
        bills = [{"payee": "Gym Co", "status": "cancelled", "payment_amount": 40, "bill_id": "b1"}]
        with patch.object(aa, "load_data", return_value=(self.DEPOSITS, self.PURCHASES, bills)):
            result = aa.simulate_decision("ana", "stop_bill", {"bill_payee": "Gym Co"})
        self.assertFalse(result["ok"])

    def test_stop_bill_projects_equal_or_better_score(self):
        result = aa.simulate_decision("ana", "stop_bill", {"bill_payee": "Gym Co"})
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["score_projected"], result["score_now"])
        self.assertIn("Gym Co", result["lesson"])

    def test_stop_bill_exposes_monthly_amount_as_explicit_field(self):
        """Regresion directa de un hallazgo real en produccion: el monto solo
        vivia dentro del texto de 'lesson' -- find_unverified_amounts (la
        capa anti-alucinacion de lambda_chat.py) no puede verificar una
        cifra que solo aparece en prosa, asi que el chat la reemplazaba por
        '[monto no confirmado]' aunque el numero fuera correcto. Mismo
        patron que ya se corrigio una vez con 'monthly_amount' en las
        alertas de get_status."""
        result = aa.simulate_decision("ana", "stop_bill", {"bill_payee": "Gym Co"})
        self.assertEqual(result["monthly_amount"], 40.0)

    def test_reduce_discretionary_rejects_invalid_amount(self):
        result = aa.simulate_decision("ana", "reduce_discretionary", {"monthly_amount": "no-es-numero"})
        self.assertFalse(result["ok"])

    def test_reduce_discretionary_rejects_non_positive_amount(self):
        result = aa.simulate_decision("ana", "reduce_discretionary", {"monthly_amount": 0})
        self.assertFalse(result["ok"])

    def test_reduce_discretionary_rejects_when_no_discretionary_spend(self):
        purchases = [p for p in self.PURCHASES if p["category"] != "discretionary"]
        with patch.object(aa, "load_data", return_value=(self.DEPOSITS, purchases, self.BILLS)):
            result = aa.simulate_decision("ana", "reduce_discretionary", {"monthly_amount": 100})
        self.assertFalse(result["ok"])

    def test_reduce_discretionary_projects_equal_or_better_score(self):
        result = aa.simulate_decision("ana", "reduce_discretionary", {"monthly_amount": 200})
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["score_projected"], result["score_now"])

    def test_reduce_discretionary_caps_at_available_amount(self):
        # Solo hay $500 de discrecional en total (300+200) -- pedir 1000 se topa ahi.
        result = aa.simulate_decision("ana", "reduce_discretionary", {"monthly_amount": 1000})
        self.assertTrue(result["ok"])
        self.assertIn("solo tienes", result["lesson"].lower())
        self.assertEqual(result["monthly_amount"], 500.0)  # topado al disponible, no al pedido

    def test_reduce_discretionary_exposes_actual_reduction_as_explicit_field(self):
        result = aa.simulate_decision("ana", "reduce_discretionary", {"monthly_amount": 200})
        self.assertEqual(result["monthly_amount"], 200.0)

    def test_never_writes_anything_regardless_of_action_or_outcome(self):
        aa.simulate_decision("ana", "stop_bill", {"bill_payee": "Gym Co"})
        aa.simulate_decision("ana", "stop_bill", {"bill_payee": "No Existe"})
        aa.simulate_decision("ana", "reduce_discretionary", {"monthly_amount": 50})
        aa.simulate_decision("ana", "reduce_discretionary", {"monthly_amount": -5})
        aa.table.put_item.assert_not_called()
        aa.table.update_item.assert_not_called()


class TestGetWeakestFactorLesson(BaseAgentActionsTest):
    """Lección de educación financiera atada al comportamiento real de la
    persona -- identifica el factor más débil del score y explica por qué,
    usando el 'detail' que signal_engine ya calculó de datos reales, no una
    lista de tips genéricos (eso ya se descartó explícitamente en el
    README como contrario a la tesis del proyecto)."""

    def test_identifies_lowest_value_factor(self):
        fake_signals = {"score": {"value": 60, "breakdown": [
            {"key": "income_regularity", "label": "Regularidad de ingreso", "weight": 35, "value": 90, "detail": "8 depositos regulares"},
            {"key": "essential_ratio", "label": "Ratio esencial/discrecional", "weight": 25, "value": 20, "detail": "gasto discrecional es 60% del ingreso total"},
            {"key": "bill_health", "label": "Recurrencia sana", "weight": 20, "value": 100, "detail": "2/2 bills sanos"},
            {"key": "liquidity_cushion", "label": "Colchon de liquidez", "weight": 20, "value": 80, "detail": "cubre 20 dias"},
        ]}}
        with patch.object(aa, "get_current_signals", return_value=fake_signals):
            result = aa.get_weakest_factor_lesson("ana")
        self.assertTrue(result["ok"])
        self.assertEqual(result["factor"], "essential_ratio")
        self.assertIn("60%", result["detail"])
        self.assertTrue(result["why_it_matters"])
        self.assertTrue(result["how_to_improve"])

    def test_empty_breakdown_is_handled_gracefully(self):
        with patch.object(aa, "get_current_signals", return_value={"score": {"value": 0, "breakdown": []}}):
            result = aa.get_weakest_factor_lesson("ana")
        self.assertFalse(result["ok"])

    def test_all_four_known_factors_have_a_lesson_defined(self):
        for key in ("income_regularity", "essential_ratio", "bill_health", "liquidity_cushion"):
            self.assertIn(key, aa.FACTOR_LESSONS)
            self.assertTrue(aa.FACTOR_LESSONS[key]["why"])
            self.assertTrue(aa.FACTOR_LESSONS[key]["how"])


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
        events = aa.get_verified_action_history("ana")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "bill_stopped")

    def test_events_far_apart_are_not_deduplicated(self):
        action_items = [{"sk": "ACTION#2026-08-16#1000000000000", "date": "2026-08-16", "type": "bill_stopped", "text": "detuve el cargo", "requires_confirmation": False}]
        notif_items = [{"sk": "NOTIFICATION#2026-08-16#1000030000000", "date": "2026-08-16", "text": "se movieron $15 a tu ahorro"}]  # 30s despues, evento distinto
        self._mock_two_queries(action_items, notif_items)
        events = aa.get_verified_action_history("ana")
        self.assertEqual(len(events), 2)

    def test_sorted_by_date_then_timestamp(self):
        action_items = [
            {"sk": "ACTION#2026-08-16#2000000000000", "date": "2026-08-16", "type": "savings_moved", "text": "b", "requires_confirmation": False},
            {"sk": "ACTION#2026-08-15#1000000000000", "date": "2026-08-15", "type": "leak_detected", "text": "a", "requires_confirmation": True},
        ]
        self._mock_two_queries(action_items, [])
        events = aa.get_verified_action_history("ana")
        self.assertEqual([e["text"] for e in events], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
