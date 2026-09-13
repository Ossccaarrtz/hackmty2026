"""
Tests de las funciones puras de verificacion anti-alucinacion en
lambda_chat.py -- no requieren mocks (son manipulacion de texto/dicts).

Motivadas por un hallazgo real en produccion: el modelo llamaba
get_status correctamente, pero la tool solo trae 'annual_cost' para una
fuga (nunca un monto mensual) -- el modelo inventaba un monto mensual
plausible ('$299/mes') en la narrativa de su respuesta en vez de admitir
que no tenia ese dato exacto. Ninguna accion real se vio afectada, pero
la respuesta le "mintio" a la usuaria sobre una cifra de su cuenta --
exactamente el tipo de alucinacion que el proyecto dice prevenir, solo
que en el TEXTO de la respuesta, no en una accion ejecutada.

Correr con: python -m unittest test_lambda_chat -v
"""
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")  # lambda_chat.py la exige al importarse
import lambda_chat as chat  # noqa: E402


def status_action(alerts=None, score_value=64):
    return {
        "tool": "get_status", "args": {},
        "result": {
            "score": {"value": score_value},
            "alerts": alerts or [{"id": "b1", "title": "Gym Co", "annual_cost": 480.0, "status": "detected"}],
            "liquidity": {"days_covered": 12},
        },
    }


class TestExtractDollarAmounts(unittest.TestCase):
    def test_extracts_simple_amount(self):
        self.assertEqual(chat.extract_dollar_amounts("Tu fuga es de $299/mes"), {299.0})

    def test_extracts_amount_with_thousands_separator(self):
        self.assertEqual(chat.extract_dollar_amounts("Tienes $1,500 ahorrados"), {1500.0})

    def test_extracts_multiple_amounts(self):
        self.assertEqual(chat.extract_dollar_amounts("De $40 a $480 al año"), {40.0, 480.0})

    def test_no_amounts_in_plain_text(self):
        self.assertEqual(chat.extract_dollar_amounts("Tu score subio esta semana"), set())


class TestCollectVerifiedNumbers(unittest.TestCase):
    def test_flattens_nested_tool_results(self):
        numbers = chat.collect_verified_numbers([status_action()])
        self.assertIn(480.0, numbers)
        self.assertIn(64.0, numbers)
        self.assertIn(12.0, numbers)

    def test_booleans_are_not_treated_as_numbers(self):
        action = {"tool": "get_status", "result": {"anomaly": {"detected": False}}}
        numbers = chat.collect_verified_numbers([action])
        self.assertNotIn(0.0, numbers)  # False no debe colarse como 0


class TestFindUnverifiedAmounts(unittest.TestCase):
    """Regresion directa del hallazgo en produccion."""

    def test_flags_amount_not_in_any_tool_result(self):
        reply = "Detecte que Gym Co cuesta $299/mes, revisa eso."
        bad = chat.find_unverified_amounts(reply, [status_action()])
        self.assertEqual(bad, {299.0})

    def test_does_not_flag_amount_that_matches_a_real_tool_number(self):
        reply = "El costo anual potencial de la fuga es de $480."
        bad = chat.find_unverified_amounts(reply, [status_action()])
        self.assertEqual(bad, set())

    def test_no_actions_taken_means_nothing_to_verify(self):
        # Si el modelo respondio sin llamar ninguna tool (ej. saludo), no
        # hay contra que verificar -- no se le exige citar una tool para
        # texto que no depende de datos de la cuenta.
        bad = chat.find_unverified_amounts("Hola, ¿en que te ayudo?", [])
        self.assertEqual(bad, set())

    def test_tolerates_small_rounding_differences(self):
        reply = "Tu colchon cubre unos $479.99 de esa fuga anual."
        bad = chat.find_unverified_amounts(reply, [status_action()])
        self.assertEqual(bad, set())


class TestRedactUnverifiedAmounts(unittest.TestCase):
    """Regresion directa: create_goal devolvia el monto objetivo solo
    embebido en `message` (texto), nunca como campo propio -- el modelo lo
    marcaba como 'inventado' al mencionarlo, y el reemplazo de emergencia
    (antes un DOLLAR_AMOUNT_RE.sub sobre TODA la respuesta) borraba de paso
    montos legitimos que si estaban verificados, dejando varios
    '[monto no confirmado]' en vez de solo el que de verdad era sospechoso."""

    def test_only_redacts_the_unverified_amount(self):
        reply = "Tu meta es de $80,000 con un aporte de $69.32/mes."
        redacted = chat.redact_unverified_amounts(reply, verified={69.32})
        self.assertEqual(redacted, "Tu meta es de [monto no confirmado] con un aporte de $69.32/mes.")

    def test_leaves_reply_unchanged_when_everything_is_verified(self):
        reply = "Tu meta es de $80,000, con un aporte de $69.32/mes."
        redacted = chat.redact_unverified_amounts(reply, verified={80000.0, 69.32})
        self.assertEqual(redacted, reply)


class TestLambdaHandlerMultipleToolCallsInOneTurn(unittest.TestCase):
    """Unica clase de este archivo que si mockea (call_gemini/execute_tool/
    historial) -- justificado por un hallazgo real: Gemini pedia VARIAS
    tools en un solo turno (ej. "compre 3 pizzas, 2 aguas y pague
    transporte" -> tres llamadas a log_external_expense de una vez), pero
    el loop de lambda_handler solo ejecutaba la primera (next(...) sobre la
    lista de parts) y descartaba las demas en silencio. El modelo de todos
    modos las mencionaba en su resumen final, y como esas dos nunca se
    ejecutaron de verdad, find_unverified_amounts las marcaba (con razon)
    como '[monto no confirmado]' -- pero el problema real no era el texto,
    era que esos dos gastos NUNCA se guardaron en DynamoDB."""

    def _calls(self, *name_args_pairs):
        return {"candidates": [{"content": {"parts": [
            {"functionCall": {"name": name, "args": args}} for name, args in name_args_pairs
        ]}}]}

    def _text(self, text):
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    def _event(self, message):
        return {"body": json.dumps({"message": message, "user_id": "ana"})}

    @patch.object(chat, "save_chat_history")
    @patch.object(chat, "get_chat_history", return_value=[])
    @patch.object(chat, "execute_tool")
    @patch.object(chat, "call_gemini")
    def test_executes_every_function_call_requested_in_a_single_turn(self, mock_gemini, mock_execute, mock_history, mock_save):
        mock_execute.side_effect = [
            {"ok": True, "amount": 300.0}, {"ok": True, "amount": 40.0}, {"ok": True, "amount": 60.0},
        ]
        mock_gemini.side_effect = [
            self._calls(
                ("log_external_expense", {"amount": 300, "category": "groceries", "source": "cash"}),
                ("log_external_expense", {"amount": 40, "category": "groceries", "source": "cash"}),
                ("log_external_expense", {"amount": 60, "category": "transport", "source": "cash"}),
            ),
            self._text("Registre $300 en pizzas, $40 en aguas y $60 en transporte."),
        ]
        response = chat.lambda_handler(self._event("compre 3 pizzas de 100, 2 aguas de 20 y pague 60 de transporte, todo en efectivo"), None)
        body = json.loads(response["body"])
        self.assertEqual(mock_execute.call_count, 3)
        self.assertEqual(len(body["actions_taken"]), 3)
        self.assertNotIn("no confirmado", body["reply"])

    @patch.object(chat, "save_chat_history")
    @patch.object(chat, "get_chat_history", return_value=[])
    @patch.object(chat, "execute_tool")
    @patch.object(chat, "call_gemini")
    def test_sends_one_function_response_per_function_call(self, mock_gemini, mock_execute, mock_history, mock_save):
        """Gemini espera un functionResponse por cada functionCall del turno
        -- mandar menos de los que pidio deja la conversacion desincronizada
        para el siguiente turno."""
        mock_execute.side_effect = [{"ok": True, "amount": 40.0}, {"ok": True, "amount": 60.0}]
        mock_gemini.side_effect = [
            self._calls(
                ("log_external_expense", {"amount": 40, "category": "groceries", "source": "cash"}),
                ("log_external_expense", {"amount": 60, "category": "transport", "source": "cash"}),
            ),
            self._text("Listo."),
        ]
        chat.lambda_handler(self._event("compre aguas y pague transporte en efectivo"), None)
        second_call_contents = mock_gemini.call_args_list[1].args[0]
        function_response_parts = [p for p in second_call_contents[-1]["parts"] if "functionResponse" in p]
        self.assertEqual(len(function_response_parts), 2)
