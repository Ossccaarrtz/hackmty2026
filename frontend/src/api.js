const BASE_URL = import.meta.env.VITE_API_BASE_URL;
const USER_ID = import.meta.env.VITE_USER_ID || 'mia';

async function get(path) {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

async function post(path) {
  const res = await fetch(`${BASE_URL}${path}`, { method: 'POST' });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

// GET /signals?user_id=... -> Cash-Flow Resilience Score + alerts + liquidity + projection
export function fetchSignals() {
  return get(`/signals?user_id=${USER_ID}`);
}

// GET /transactions?user_id=... -> raw deposits+purchases with running_balance already computed
export function fetchTransactions() {
  return get(`/transactions?user_id=${USER_ID}`);
}

// POST /simulation/advance-day?user_id=... -> moves the demo forward one checkpoint,
// executing the real risk policy against the shared Nessie sandbox.
export function advanceDay() {
  return post(`/simulation/advance-day?user_id=${USER_ID}`);
}
