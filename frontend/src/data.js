export function normalizeData(signals, ledger) {
  if (!Number.isFinite(signals?.score?.value) || !Array.isArray(signals.score.breakdown)
      || !Array.isArray(signals.alerts) || !Number.isFinite(signals.liquidity?.days_covered)
      || !signals.score.breakdown.every(item => Number.isFinite(item.value) && typeof item.label === 'string')
      || !Array.isArray(ledger?.transactions) || !Number.isFinite(ledger.summary?.total_income)
      || !Number.isFinite(ledger.summary?.total_expense)) throw new Error('Respuesta incompatible con el contrato del dashboard.');
  const transactions = ledger.transactions.map(tx => {
    if (!Number.isFinite(tx.signed_amount) || !Number.isFinite(tx.running_balance) || !/^\d{4}-\d{2}-\d{2}$/.test(tx.date)) throw new Error('Movimiento no válido en el historial.');
    return { ...tx, name: tx.merchant_name || tx.description || tx.category_label || 'Movimiento' };
  }).sort((a, b) => a.date.localeCompare(b.date) || String(a.id).localeCompare(String(b.id)));
  return { signals, summary: ledger.summary, transactions, balance: transactions.length ? transactions.at(-1).running_balance : ledger.summary.total_income - ledger.summary.total_expense };
}
export const emptySession = () => ({ feed: [], history: [], done: false, label: '' });
export function appendCheckpoint(session, response) {
  if (response.done) return { ...session, done: true };
  if (!Array.isArray(response.new_actions) || !Number.isFinite(response.score?.value) || !response.date) throw new Error('Checkpoint no válido del agente.');
  const actions = response.new_actions.map((action, index) => ({ ...action, id: `${response.date}:${index}:${action.type}` }));
  return { done: false, label: response.label, date: response.date,
    feed: [...actions.slice().reverse(), ...session.feed.filter(old => !actions.some(action => action.id === old.id))],
    history: [...session.history.filter(point => point.date !== response.date), { date: response.date, value: response.score.value }],
  };
}
