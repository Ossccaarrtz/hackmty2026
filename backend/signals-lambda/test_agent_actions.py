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


class TestGetBudgetStatus(BaseAgentActionsTest):
    """Sin saldo acumulado ni transferencias: 'gastado' se deriva de
    transacciones reales ya categorizadas, filtradas por mes -- a
    diferencia del viejo get_envelope_balances, que sumaba toda la
    historia y nunca bajaba."""

    BUDGETS = [{"category": "transport", "label": "Transporte", "monthly_target": 800}]

    def test_no_purchases_returns_empty_status(self):
        with patch.object(aa, "load_data", return_value=([], [], [])):
            status = aa.get_budget_status("ana", as_of_date=None)
        self.assertIsNone(status["month"])
        self.assertEqual(status["categories"], [])

    def test_filters_spend_by_current_month_only(self):
        purchases = [
            {"date": "2026-09-05", "category": "transport", "amount": 300},
            {"date": "2026-08-20", "category": "transport", "amount": 999},  # mes anterior, no debe contar
        ]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_category_budgets", return_value=self.BUDGETS), \
             patch.object(aa, "get_monthly_budget", return_value=None):
            status = aa.get_budget_status("ana", as_of_date="2026-09-18")
        self.assertEqual(status["categories"][0]["spent"], 300)

    def test_excludes_neutral_and_savings_categories(self):
        purchases = [
            {"date": "2026-09-05", "category": "savings_transfer", "amount": 500},
            {"date": "2026-09-05", "category": "savings_release", "amount": 200},
            {"date": "2026-09-05", "category": "envelope:legacy", "amount": 100},
        ]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_category_budgets", return_value=[]), \
             patch.object(aa, "get_monthly_budget", return_value=None):
            status = aa.get_budget_status("ana", as_of_date="2026-09-18")
        self.assertEqual(status["unbudgeted"], [])
        self.assertEqual(status["total_spent"], 0)

    def test_includes_external_source_spend(self):
        """El gasto declarado en efectivo/otra tarjeta SI cuenta -- un
        presupuesto describe comportamiento completo, al contrario de
        compute_totals (que solo mide el saldo del banco)."""
        purchases = [{"date": "2026-09-05", "category": "transport", "amount": 150, "source": "cash"}]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_category_budgets", return_value=self.BUDGETS), \
             patch.object(aa, "get_monthly_budget", return_value=None):
            status = aa.get_budget_status("ana", as_of_date="2026-09-18")
        self.assertEqual(status["categories"][0]["spent"], 150)

    def test_spend_without_a_budget_goes_to_unbudgeted(self):
        purchases = [{"date": "2026-09-05", "category": "utilities", "amount": 400}]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_category_budgets", return_value=self.BUDGETS), \
             patch.object(aa, "get_monthly_budget", return_value=None):
            status = aa.get_budget_status("ana", as_of_date="2026-09-18")
        self.assertEqual(status["categories"][0]["spent"], 0)
        self.assertEqual(status["unbudgeted"][0]["category"], "utilities")
        self.assertEqual(status["unbudgeted_spend"], 400)

    def test_pace_excedido_when_over_target(self):
        purchases = [{"date": "2026-09-05", "category": "transport", "amount": 900}]
        with patch.object(aa, "load_data", return_value=([], purchases, [])), \
             patch.object(aa, "get_category_budgets", return_value=self.BUDGETS), \
             patch.object(aa, "get_monthly_budget", return_value=None):
            status = aa.get_budget_status("ana", as_of_date="2026-09-18")
        self.assertEqual(status["categories"][0]["pace"], "excedido")


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

    @patch.object(aa, "_execute_stop_bill")
    def test_propose_never_executes_even_for_a_real_leak(self, mock_execute):
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=self.LEAK_SIGNALS):
            result = aa.propose_stop_bill("ana", "Gym Co")
        self.assertFalse(result["ok"])
        self.assertTrue(result["pending"])
        mock_execute.assert_not_called()  # el punto central del fix: NUNCA ejecuta en la propuesta
        aa.table.put_item.assert_called_once()

    def test_confirm_without_pending_rejects(self):
        aa.table.get_item.return_value = {}
        result = aa.confirm_stop_bill("ana")
        self.assertFalse(result["ok"])

    def test_confirm_executes_after_valid_pending(self):
        aa.table.get_item.return_value = {"Item": {"bill_id": "b1", "payee": "Gym Co", "payment_amount": 40.0}}
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=self.LEAK_SIGNALS):
            result = aa.confirm_stop_bill("ana")
        self.assertTrue(result["ok"])

    @patch.object(aa, "_execute_stop_bill")
    def test_confirm_revalidates_and_rejects_if_no_longer_a_leak(self, mock_execute):
        """El bill ya no esta marcado como fuga entre el propose y el confirm
        (ej. alguien lo volvio a usar) -- confirm no debe confiar ciegamente
        en la propuesta guardada."""
        aa.table.get_item.return_value = {"Item": {"bill_id": "b1", "payee": "Gym Co", "payment_amount": 40.0}}
        no_longer_leak_signals = signals_with()  # alerts vacios ahora
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=no_longer_leak_signals):
            result = aa.confirm_stop_bill("ana")
        self.assertFalse(result["ok"])
        mock_execute.assert_not_called()

    def test_verified_stop_bill_direct_execute_used_by_advance_day(self):
        """advance-day si ejecuta directo -- su propio checkpoint YA es el paso de confirmacion."""
        with patch.object(aa, "load_data", return_value=([], [], [self.LEAK_BILL])), \
             patch.object(aa, "get_full_signals", return_value=self.LEAK_SIGNALS):
            result = aa.verified_stop_bill("ana", "Gym Co")
        self.assertTrue(result["ok"])


class TestSetMonthlyBudget(BaseAgentActionsTest):
    def test_rejects_invalid_amount(self):
        result = aa.set_monthly_budget("ana", "no-es-numero")
        self.assertFalse(result["ok"])

    def test_rejects_non_positive_amount(self):
        result = aa.set_monthly_budget("ana", 0)
        self.assertFalse(result["ok"])

    def test_saves_valid_amount(self):
        result = aa.set_monthly_budget("ana", 8000)
        self.assertTrue(result["ok"])
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(saved_item["sk"], aa.MONTHLY_BUDGET_SK)
        self.assertEqual(float(saved_item["amount"]), 8000)


class TestSetCategoryBudget(BaseAgentActionsTest):
    """set_category_budget reemplaza al viejo create_envelope: la categoria
    tiene que ser una de las 5 reales (rent/groceries/transport/utilities/
    discretionary), no texto libre -- para poder compararse despues contra
    gasto real sin necesitar transacciones sinteticas. monthly_target=0
    borra la meta, asi no hace falta una ruta DELETE nueva."""

    def test_rejects_invalid_category(self):
        result = aa.set_category_budget("ana", "gasolina", 800)
        self.assertFalse(result["ok"])

    def test_creating_new_budget_says_creada(self):
        aa.table.get_item.return_value = {}
        result = aa.set_category_budget("ana", "transport", 800, "Gasolina y camion")
        self.assertTrue(result["ok"])
        self.assertIn("creada", result["message"])
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(saved_item["category"], "transport")
        self.assertEqual(saved_item["label"], "Gasolina y camion")

    def test_updating_existing_budget_keeps_created_at_and_label(self):
        aa.table.get_item.return_value = {"Item": {"category": "transport", "label": "Transporte", "monthly_target": 800, "created_at": "2026-01-01"}}
        result = aa.set_category_budget("ana", "transport", 900)
        self.assertTrue(result["ok"])
        self.assertIn("actualizada", result["message"])
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(float(saved_item["monthly_target"]), 900)
        self.assertEqual(saved_item["created_at"], "2026-01-01")  # no se pisa la fecha de creacion original
        self.assertEqual(saved_item["label"], "Transporte")  # conserva la etiqueta si no se manda una nueva

    def test_zero_target_deletes_budget_instead_of_saving(self):
        result = aa.set_category_budget("ana", "transport", 0)
        self.assertTrue(result["ok"])
        aa.table.delete_item.assert_called_once()
        aa.table.put_item.assert_not_called()

    def test_rejects_negative_target(self):
        result = aa.set_category_budget("ana", "transport", -100)
        self.assertFalse(result["ok"])

    def test_rejects_non_numeric_target(self):
        result = aa.set_category_budget("ana", "transport", "no-es-numero")
        self.assertFalse(result["ok"])


class TestMonthsBetween(unittest.TestCase):
    def test_full_months_apart(self):
        self.assertEqual(aa._months_between("2026-01-15", "2026-07-15"), 6)

    def test_same_month_still_counts_as_one(self):
        self.assertEqual(aa._months_between("2026-01-05", "2026-01-20"), 1)


class TestSlugifyLabel(unittest.TestCase):
    def test_strips_accents_and_spaces(self):
        self.assertEqual(aa._slugify_label("Viaje a Japón"), "viaje_a_japon")

    def test_empty_label_falls_back_to_meta(self):
        self.assertEqual(aa._slugify_label("   "), "meta")


def months_ago(n):
    """Fecha ISO exactamente n meses calendario antes de hoy -- para tests
    de on_track que no dependan del dia del mes en que corran."""
    today = date.today()
    month = today.month - n
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1).isoformat()


class TestCreateGoal(BaseAgentActionsTest):
    """create_goal arma un plan (meses + aporte mensual) segun el
    disponible real de Ana -- nunca mueve ni aparta nada, solo lo guarda.
    El avance real se registra despues con log_goal_contribution."""

    def test_rejects_missing_label(self):
        result = aa.create_goal("ana", "", 30000)
        self.assertFalse(result["ok"])

    def test_rejects_non_numeric_target(self):
        result = aa.create_goal("ana", "Viaje a Japon", "no-es-numero")
        self.assertFalse(result["ok"])

    def test_rejects_non_positive_target(self):
        result = aa.create_goal("ana", "Viaje a Japon", 0)
        self.assertFalse(result["ok"])

    def test_rejects_when_no_disposable_income_and_no_date(self):
        aa.table.get_item.return_value = {}
        with patch.object(aa, "estimate_monthly_disposable", return_value=0):
            result = aa.create_goal("ana", "Viaje a Japon", 30000)
        self.assertFalse(result["ok"])
        aa.table.put_item.assert_not_called()

    def test_estimates_months_from_disposable_income_when_no_date_given(self):
        aa.table.get_item.return_value = {}
        with patch.object(aa, "estimate_monthly_disposable", return_value=3000):
            result = aa.create_goal("ana", "Viaje a Japon", 30000)
        self.assertTrue(result["ok"])
        self.assertTrue(result["realistic"])
        self.assertEqual(result["months"], 10)
        self.assertEqual(result["monthly_contribution"], 3000)
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(saved_item["slug"], "viaje_a_japon")
        self.assertEqual(float(saved_item["contributed"]), 0)

    def test_slugifies_accented_label(self):
        aa.table.get_item.return_value = {}
        with patch.object(aa, "estimate_monthly_disposable", return_value=1000):
            result = aa.create_goal("ana", "Viaje a Japón", 5000)
        self.assertEqual(result["slug"], "viaje_a_japon")

    def test_uses_target_date_instead_of_disposable_when_given(self):
        aa.table.get_item.return_value = {}
        target_date = "2099-12-31"  # bien en el futuro, sin importar cuando corra el test
        expected_months = aa._months_between(date.today().isoformat(), target_date)
        with patch.object(aa, "estimate_monthly_disposable", return_value=1):
            result = aa.create_goal("ana", "Viaje a Japon", expected_months * 1000, target_date=target_date)
        self.assertEqual(result["months"], expected_months)
        self.assertEqual(result["monthly_contribution"], 1000)
        self.assertFalse(result["realistic"])  # pide 1000/mes, solo hay 1 disponible

    def test_updating_existing_goal_keeps_contributed_and_created_at(self):
        aa.table.get_item.return_value = {"Item": {
            "slug": "viaje_a_japon", "label": "Viaje a Japon", "target_amount": 20000,
            "monthly_contribution": 2000, "estimated_months": 10, "contributed": 4000, "created_at": "2026-01-01",
        }}
        with patch.object(aa, "estimate_monthly_disposable", return_value=3000):
            result = aa.create_goal("ana", "Viaje a Japon", 30000)
        self.assertIn("actualizada", result["message"])
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(float(saved_item["contributed"]), 4000)
        self.assertEqual(saved_item["created_at"], "2026-01-01")


class TestGetGoalsWithProgress(BaseAgentActionsTest):
    def test_computes_percent_and_remaining(self):
        with patch.object(aa, "get_goals", return_value=[{
            "slug": "japon", "label": "Japon", "target_amount": 10000,
            "monthly_contribution": 1000, "estimated_months": 10,
            "contributed": 2500, "created_at": date.today().isoformat(),
        }]):
            goals = aa.get_goals_with_progress("ana")
        self.assertEqual(goals[0]["remaining"], 7500)
        self.assertEqual(goals[0]["percent"], 25)

    def test_on_track_when_contributed_meets_expected_pace(self):
        with patch.object(aa, "get_goals", return_value=[{
            "slug": "japon", "label": "Japon", "target_amount": 10000,
            "monthly_contribution": 1000, "estimated_months": 10,
            "contributed": 2000, "created_at": months_ago(2),
        }]):
            goals = aa.get_goals_with_progress("ana")
        self.assertTrue(goals[0]["on_track"])

    def test_off_track_when_contributed_below_expected_pace(self):
        with patch.object(aa, "get_goals", return_value=[{
            "slug": "japon", "label": "Japon", "target_amount": 10000,
            "monthly_contribution": 1000, "estimated_months": 10,
            "contributed": 500, "created_at": months_ago(2),
        }]):
            goals = aa.get_goals_with_progress("ana")
        self.assertFalse(goals[0]["on_track"])


class TestLogGoalContribution(BaseAgentActionsTest):
    def test_rejects_unknown_goal(self):
        aa.table.get_item.return_value = {}
        result = aa.log_goal_contribution("ana", "japon", 500)
        self.assertFalse(result["ok"])

    def test_rejects_non_positive_amount(self):
        result = aa.log_goal_contribution("ana", "japon", 0)
        self.assertFalse(result["ok"])

    def test_accumulates_on_top_of_existing_contribution(self):
        aa.table.get_item.return_value = {"Item": {
            "slug": "japon", "label": "Japon", "target_amount": 10000, "contributed": 2000,
        }}
        result = aa.log_goal_contribution("ana", "japon", 500)
        self.assertTrue(result["ok"])
        self.assertEqual(result["contributed"], 2500)
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(float(saved_item["contributed"]), 2500)

    def test_marks_done_when_target_reached(self):
        aa.table.get_item.return_value = {"Item": {
            "slug": "japon", "label": "Japon", "target_amount": 1000, "contributed": 800,
        }}
        result = aa.log_goal_contribution("ana", "japon", 300)
        self.assertTrue(result["done"])
        self.assertEqual(result["remaining"], 0)


class TestComputeSmartAllocation(BaseAgentActionsTest):
    """Reparto inteligente bajo demanda: a diferencia del viejo plan de
    nomina, corre sobre el saldo actual, no espera un deposito que
    matchee un patron. Sigue sin mover nada -- protege el mismo colchon
    minimo (LIQUIDITY_WARNING_DAYS) que cualquier movimiento real, y
    escala proporcionalmente en vez de solo reportar que no alcanza."""

    def setUp(self):
        super().setUp()
        self.load_data_patch = patch.object(aa, "load_data", return_value=([], [], []))
        self.load_data_patch.start()
        self.addCleanup(self.load_data_patch.stop)

    def test_stops_when_anomaly_detected(self):
        with patch.object(aa, "get_full_signals", return_value=signals_with(anomaly_detected=True, anomaly_reason="gasto raro")):
            result = aa.compute_smart_allocation("ana")
        self.assertFalse(result["ok"])
        self.assertIn("gasto raro", result["reason"])

    def test_rejects_when_no_categories_and_no_goals(self):
        with patch.object(aa, "get_full_signals", return_value=signals_with()), \
             patch.object(aa, "get_budget_status", return_value={"categories": []}), \
             patch.object(aa, "get_goals_with_progress", return_value=[]):
            result = aa.compute_smart_allocation("ana")
        self.assertFalse(result["ok"])

    def test_reserves_cushion_before_suggesting_anything(self):
        signals = signals_with(current_balance=1000)
        signals["liquidity"] = {"days_covered": 20}  # avg diario = 1000/20 = 50; colchon = 50*7 = 350
        budgets = {"categories": [{"category": "transport", "label": "Transporte", "monthly_target": 800, "spent": 0}]}
        with patch.object(aa, "get_full_signals", return_value=signals), \
             patch.object(aa, "get_budget_status", return_value=budgets), \
             patch.object(aa, "get_goals_with_progress", return_value=[]):
            result = aa.compute_smart_allocation("ana")
        self.assertEqual(result["cushion_floor"], 350)
        self.assertEqual(result["available"], 650)

    def test_scales_down_proportionally_when_overcommitted(self):
        signals = signals_with(current_balance=1000)
        signals["liquidity"] = {"days_covered": 999}  # sin gasto esencial detectado -> colchon 0
        budgets = {"categories": [
            {"category": "transport", "label": "Transporte", "monthly_target": 800, "spent": 0},
            {"category": "rent", "label": "Renta", "monthly_target": 800, "spent": 0},
        ]}
        with patch.object(aa, "get_full_signals", return_value=signals), \
             patch.object(aa, "get_budget_status", return_value=budgets), \
             patch.object(aa, "get_goals_with_progress", return_value=[]):
            result = aa.compute_smart_allocation("ana")
        self.assertTrue(result["scaled"])
        self.assertAlmostEqual(result["lines"][0]["suggested"], 500, places=2)
        self.assertAlmostEqual(result["reserved"], 1000, places=2)

    def test_no_scaling_when_everything_fits(self):
        signals = signals_with(current_balance=1000)
        signals["liquidity"] = {"days_covered": 999}
        budgets = {"categories": [{"category": "transport", "label": "Transporte", "monthly_target": 300, "spent": 0}]}
        with patch.object(aa, "get_full_signals", return_value=signals), \
             patch.object(aa, "get_budget_status", return_value=budgets), \
             patch.object(aa, "get_goals_with_progress", return_value=[]):
            result = aa.compute_smart_allocation("ana")
        self.assertFalse(result["scaled"])
        self.assertEqual(result["lines"][0]["suggested"], 300)
        self.assertEqual(result["free"], 700)

    def test_goal_need_is_capped_by_what_is_still_missing(self):
        signals = signals_with(current_balance=1000)
        signals["liquidity"] = {"days_covered": 999}
        goal = {"slug": "japon", "label": "Japon", "monthly_contribution": 2000, "remaining": 300}
        with patch.object(aa, "get_full_signals", return_value=signals), \
             patch.object(aa, "get_budget_status", return_value={"categories": []}), \
             patch.object(aa, "get_goals_with_progress", return_value=[goal]):
            result = aa.compute_smart_allocation("ana")
        self.assertEqual(result["lines"][0]["needed"], 300)  # tope: lo que falta, no el aporte mensual completo


class TestSimulateThirdPartyPayroll(BaseAgentActionsTest):
    """Nomina real de un tercero (otra cuenta Nessie, no la nuestra) -- ver
    backend/signals-lambda/agent_actions.py::simulate_third_party_payroll.
    Ya no hay patron de nomina que matchear: el monto siempre es explicito,
    y esta funcion solo es responsable de mover el dinero y escribir el
    deposito -- compute_smart_allocation (bajo demanda) es lo que despues
    usa ese saldo, no un trigger reactivo al depositar."""

    def setUp(self):
        super().setUp()
        self.load_data_patch = patch.object(aa, "load_data", return_value=([], [{"date": "2026-09-12", "amount": 10}], []))
        self.load_data_patch.start()
        self.addCleanup(self.load_data_patch.stop)

    @patch.object(aa, "receive_from_third_party")
    def test_moves_the_explicit_amount(self, mock_receive):
        mock_receive.return_value = {"withdrawal": {}, "deposit": {}}
        result = aa.simulate_third_party_payroll("ana", 565)
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 565.0)
        mock_receive.assert_called_once()
        self.assertEqual(mock_receive.call_args.args[2], 565.0)

    def test_rejects_non_numeric_amount(self):
        result = aa.simulate_third_party_payroll("ana", "no-es-numero")
        self.assertFalse(result["ok"])

    def test_rejects_non_positive_amount(self):
        result = aa.simulate_third_party_payroll("ana", 0)
        self.assertFalse(result["ok"])

    @patch.object(aa, "receive_from_third_party", side_effect=RuntimeError("Nessie caido"))
    def test_nessie_failure_does_not_write_deposit(self, mock_receive):
        result = aa.simulate_third_party_payroll("ana", 565)
        self.assertFalse(result["ok"])
        aa.table.put_item.assert_not_called()

    @patch.object(aa, "receive_from_third_party")
    def test_writes_deposit_with_third_party_category_for_reset_cleanup(self, mock_receive):
        mock_receive.return_value = {"withdrawal": {}, "deposit": {}}
        aa.simulate_third_party_payroll("ana", 565)
        saved_item = aa.table.put_item.call_args.kwargs["Item"]
        self.assertEqual(saved_item["type"], "deposit")
        self.assertEqual(saved_item["category"], aa.THIRD_PARTY_INCOME_CATEGORY)


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


if __name__ == "__main__":
    unittest.main()
