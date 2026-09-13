"""
Tests de lambda_transactions.py -- regresion directa de un hallazgo real:
running_balance/total_expense restaban gasto declarado en efectivo/otra
tarjeta (log_external_expense), aunque ese dinero nunca salio de la cuenta
que representan (mismo criterio que compute_totals/score_liquidity en
agent_actions.py). El sintoma en produccion: el saldo mostrado en Home y
el Estado de cuenta quedaba mas bajo -- incluso negativo -- de lo real,
cada vez que alguien registraba un gasto externo por chat.

Correr con: python -m unittest test_lambda_transactions -v
"""
import unittest
from unittest.mock import MagicMock, patch

import lambda_transactions as lt


def make_event():
    return {"queryStringParameters": {"user_id": "ana"}}


class TestLambdaTransactions(unittest.TestCase):
    def setUp(self):
        self.table_patch = patch.object(lt, "table", MagicMock())
        self.table_patch.start()
        self.addCleanup(self.table_patch.stop)

    def _run(self, items):
        lt.table.query.return_value = {"Items": items}
        import json
        return json.loads(lt.lambda_handler(make_event(), None)["body"])

    def test_bank_purchase_reduces_running_balance(self):
        items = [
            {"sk": "TXN#2026-09-01#dep0", "type": "deposit", "date": "2026-09-01", "amount": 1000, "category": "income"},
            {"sk": "TXN#2026-09-02#pur0", "type": "purchase", "date": "2026-09-02", "amount": 300, "category": "groceries"},
        ]
        body = self._run(items)
        self.assertEqual(body["transactions"][-1]["running_balance"], 700)
        self.assertEqual(body["summary"]["total_expense"], 300)

    def test_external_expense_does_not_move_running_balance(self):
        """El hallazgo real: un gasto en efectivo se seguia restando del
        saldo, aunque ese dinero nunca salio del banco."""
        items = [
            {"sk": "TXN#2026-09-01#dep0", "type": "deposit", "date": "2026-09-01", "amount": 1000, "category": "income"},
            {"sk": "TXN#2026-09-02#external1", "type": "purchase", "date": "2026-09-02", "amount": 300, "category": "groceries", "source": "cash"},
        ]
        body = self._run(items)
        self.assertEqual(body["transactions"][-1]["running_balance"], 1000)
        self.assertEqual(body["summary"]["total_expense"], 0)

    def test_external_expense_still_appears_in_the_list_with_its_real_amount(self):
        """No se oculta el gasto -- Ana lo registro, debe poder verlo -- solo
        no debe restarse del saldo bancario."""
        items = [
            {"sk": "TXN#2026-09-02#external1", "type": "purchase", "date": "2026-09-02", "amount": 300, "category": "groceries", "source": "cash"},
        ]
        body = self._run(items)
        self.assertEqual(len(body["transactions"]), 1)
        self.assertEqual(body["transactions"][0]["signed_amount"], -300)
        self.assertEqual(body["transactions"][0]["source"], "cash")

    def test_mixed_bank_and_external_expenses_only_bank_affects_balance(self):
        items = [
            {"sk": "TXN#2026-09-01#dep0", "type": "deposit", "date": "2026-09-01", "amount": 1000, "category": "income"},
            {"sk": "TXN#2026-09-02#pur0", "type": "purchase", "date": "2026-09-02", "amount": 100, "category": "transport"},
            {"sk": "TXN#2026-09-03#external1", "type": "purchase", "date": "2026-09-03", "amount": 250, "category": "discretionary", "source": "other_card", "card_name": "Banorte"},
        ]
        body = self._run(items)
        self.assertEqual(body["transactions"][-1]["running_balance"], 900)  # 1000 - 100, el gasto externo no cuenta
        self.assertEqual(body["summary"]["total_expense"], 100)

    def test_by_category_still_includes_external_spend_for_complete_behavior(self):
        """A diferencia del saldo, el desglose por categoria SI quiere la
        foto completa del comportamiento (mismo criterio que get_budget_status)."""
        items = [
            {"sk": "TXN#2026-09-02#external1", "type": "purchase", "date": "2026-09-02", "amount": 300, "category": "groceries", "source": "cash"},
        ]
        body = self._run(items)
        self.assertEqual(body["summary"]["by_category"]["groceries"], 300)

    def test_savings_release_is_an_inflow_regardless_of_source_default(self):
        items = [
            {"sk": "TXN#2026-09-01#rel0", "type": "purchase", "date": "2026-09-01", "amount": 200, "category": "savings_release"},
        ]
        body = self._run(items)
        self.assertEqual(body["transactions"][0]["signed_amount"], 200)
        self.assertEqual(body["transactions"][0]["running_balance"], 200)
        self.assertEqual(body["summary"]["total_income"], 200)


if __name__ == "__main__":
    unittest.main()
