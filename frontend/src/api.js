const DEFAULT_BASE = 'https://qj0vumzrfa.execute-api.us-east-1.amazonaws.com';

export function createApi({ baseUrl = DEFAULT_BASE, userId = 'ana', fetchImpl = fetch, timeoutMs = 30000 } = {}) {
  async function request(path, { method = 'GET', reset = false, body = null, params = null } = {}) {
    const url = new URL(`${baseUrl.replace(/\/+$/, '')}${path}`);
    url.searchParams.set('user_id', userId);
    if (reset) url.searchParams.set('reset', 'true');
    if (params) Object.entries(params).forEach(([key, value]) => { if (value != null) url.searchParams.set(key, value); });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const init = { method, signal: controller.signal };
      if (body) { init.headers = { 'Content-Type': 'application/json' }; init.body = JSON.stringify(body); }
      // Never retry a POST automatically: the server may have executed it already.
      const response = await fetchImpl(url.toString(), init);
      if (response.status === 429) {
        let msg = 'Kivo está saturado ahorita mismo (límite de solicitudes), intenta de nuevo en un minuto.';
        try { const body = await response.json(); if (body?.reply) msg = body.reply; } catch { /* usa el mensaje por default */ }
        throw new Error(msg);
      }
      if (!response.ok) {
        let reason = `El servidor respondió HTTP ${response.status}.`;
        try { const body = await response.json(); if (body?.reason || body?.error) reason = body.reason || body.error; } catch { /* usa el mensaje por default */ }
        throw new Error(reason);
      }
      const data = await response.json();
      if (!data || typeof data !== 'object' || data.error) throw new Error(data?.error || 'Respuesta no válida del servidor.');
      return data;
    } catch (error) {
      if (controller.signal.aborted) throw new Error('La solicitud agotó el tiempo de espera.');
      if (error instanceof SyntaxError) throw new Error('El servidor no devolvió JSON válido.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  return {
    getSignals: (params) => request('/signals', { params }),
    getTransactions: () => request('/transactions'),
    getNotifications: () => request('/notifications'),
    getBudget: () => request('/envelopes'),
    getSmartAllocation: () => request('/envelopes?smart_allocation=1'),
    setCategoryBudget: (category, monthlyTarget, label) => request('/envelopes', { method: 'POST', body: { category, monthly_target: monthlyTarget, label: label || undefined } }),
    setMonthlyBudget: (amount) => request('/envelopes', { method: 'POST', body: { monthly_budget: amount } }),
    createGoal: (label, targetAmount, targetDate) => request('/envelopes', { method: 'POST', body: { goal_label: label, goal_target_amount: targetAmount, goal_target_date: targetDate || undefined } }),
    logGoalContribution: (slug, amount) => request('/envelopes', { method: 'POST', body: { goal_contribution_slug: slug, goal_contribution_amount: amount } }),
    simulateThirdPartyPayroll: (amount, employerLabel) => request('/envelopes/simulate-payroll', { method: 'POST', body: { amount, employer_label: employerLabel || undefined } }),
    advanceDay: () => request('/simulation/advance-day', { method: 'POST' }),
    resetSimulation: () => request('/simulation/advance-day', { method: 'POST', reset: true }),
    sendChatMessage: (message) => request('/chat/message', { method: 'POST', body: { message, user_id: userId } }),
  };
}
export const api = createApi({ baseUrl: import.meta.env?.VITE_API_BASE_URL || DEFAULT_BASE, userId: import.meta.env?.VITE_USER_ID || 'ana' });
export const sessionKey = `kivo:${import.meta.env?.VITE_API_BASE_URL || DEFAULT_BASE}:${import.meta.env?.VITE_USER_ID || 'ana'}`;
