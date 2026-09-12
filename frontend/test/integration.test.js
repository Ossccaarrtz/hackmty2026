import test from 'node:test';
import assert from 'node:assert/strict';
import { createApi } from '../src/api.js';
import { normalizeData, appendCheckpoint, emptySession } from '../src/data.js';

const signals = { score: { value: 64, breakdown: [{ key: 'income', label: 'Ingresos', value: 73, weight: 35 }] }, alerts: [], liquidity: { days_covered: 12 } };
const ledger = { summary: { total_income: 620, total_expense: 114 }, transactions: [
  { id: '2', date: '2026-06-19', signed_amount: -114, running_balance: 506, merchant_name: 'Comercio' },
  { id: '1', date: '2026-06-16', signed_amount: 620, running_balance: 620, merchant_name: null, description: 'Pago freelance' },
] };

test('API uses documented paths, encoded user_id and POST only for mutations', async () => {
  const calls = [];
  const api = createApi({ baseUrl: 'https://example.test/', userId: 'mia & equipo', fetchImpl: async (url, options) => {
    calls.push({ url: new URL(url), method: options.method }); return { ok: true, json: async () => ({}) };
  } });
  await api.getSignals(); await api.getTransactions(); await api.advanceDay(); await api.resetSimulation();
  assert.deepEqual(calls.map(call => call.url.pathname), ['/signals', '/transactions', '/simulation/advance-day', '/simulation/advance-day']);
  assert.deepEqual(calls.map(call => call.method), ['GET', 'GET', 'POST', 'POST']);
  assert.ok(calls.every(call => call.url.searchParams.get('user_id') === 'mia & equipo'));
  assert.equal(calls[3].url.searchParams.get('reset'), 'true');
});
test('HTTP errors are surfaced without retrying mutations', async () => {
  let calls = 0;
  const api = createApi({ fetchImpl: async () => { calls++; return { ok: false, status: 502 }; } });
  await assert.rejects(api.advanceDay(), /HTTP 502/); assert.equal(calls, 1);
});
test('timeouts abort and do not replay a POST', async () => {
  let calls = 0;
  const api = createApi({ timeoutMs: 5, fetchImpl: (_url, { signal }) => { calls++; return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')))); } });
  await assert.rejects(api.advanceDay(), /tiempo de espera/); assert.equal(calls, 1);
});
test('invalid JSON is reported', async () => {
  const api = createApi({ fetchImpl: async () => ({ ok: true, json: async () => { throw new SyntaxError(); } }) });
  await assert.rejects(api.getSignals(), /JSON válido/);
});
test('all transactions are kept and signed ledger balances are preserved', () => {
  const result = normalizeData(signals, ledger);
  assert.equal(result.balance, 506); assert.equal(result.transactions.length, 2);
  assert.equal(result.transactions[0].name, 'Pago freelance'); assert.equal(result.transactions[1].signed_amount, -114);
});
test('empty ledger and alerts do not retain fictitious values', () => {
  const result = normalizeData(signals, { transactions: [], summary: { total_income: 0, total_expense: 0 } });
  assert.equal(result.balance, 0); assert.deepEqual(result.transactions, []); assert.deepEqual(result.signals.alerts, []);
});
test('malformed financial payloads are rejected', () => {
  assert.throws(() => normalizeData({}, ledger), /contrato/);
  assert.throws(() => normalizeData(signals, { ...ledger, transactions: [{ ...ledger.transactions[0], signed_amount: '114' }] }), /Movimiento/);
});
test('checkpoints preserve backend messages and deduplicate replayed responses', () => {
  const response = { date: '2026-08-15', label: 'Dia 62', score: { value: 64 }, new_actions: [{ date: '2026-08-15', type: 'leak_detected', requires_confirmation: true, text: 'Revisa el contrato antes de detener el cargo.' }] };
  const once = appendCheckpoint(emptySession(), response);
  const twice = appendCheckpoint(once, response);
  assert.equal(twice.feed.length, 1); assert.equal(twice.history.length, 1);
  assert.equal(twice.feed[0].text, response.new_actions[0].text); assert.equal(twice.feed[0].requires_confirmation, true);
  assert.equal(appendCheckpoint(twice, { done: true }).done, true);
  assert.throws(() => appendCheckpoint(twice, {}), /Checkpoint/);
});
