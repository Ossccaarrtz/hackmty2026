const API_BASE = import.meta.env.VITE_API_BASE_URL || 'https://qj0vumzrfa.execute-api.us-east-1.amazonaws.com';

async function get(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

async function post(path) {
  const res = await fetch(`${API_BASE}${path}`, { method: 'POST' });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

export const getSignals = (userId = 'mia') => get(`/signals?user_id=${userId}`);
export const getTransactions = (userId = 'mia') => get(`/transactions?user_id=${userId}`);
export const advanceDay = (userId = 'mia') => post(`/simulation/advance-day?user_id=${userId}`);
export const resetSimulation = (userId = 'mia') => post(`/simulation/advance-day?user_id=${userId}&reset=true`);
