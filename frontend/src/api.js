const DEFAULT_BASE = 'https://qj0vumzrfa.execute-api.us-east-1.amazonaws.com';

export function createApi({ baseUrl = DEFAULT_BASE, userId = 'mia', fetchImpl = fetch, timeoutMs = 30000 } = {}) {
  async function request(path, { method = 'GET', reset = false } = {}) {
    const url = new URL(`${baseUrl.replace(/\/+$/, '')}${path}`);
    url.searchParams.set('user_id', userId);
    if (reset) url.searchParams.set('reset', 'true');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      // Never retry a POST automatically: the server may have executed it already.
      const response = await fetchImpl(url.toString(), { method, signal: controller.signal });
      if (!response.ok) throw new Error(`El servidor respondió HTTP ${response.status}.`);
      const data = await response.json();
      if (!data || typeof data !== 'object' || data.error) throw new Error('Respuesta no válida del servidor.');
      return data;
    } catch (error) {
      if (controller.signal.aborted) throw new Error('La solicitud agotó el tiempo de espera.');
      if (error instanceof SyntaxError) throw new Error('El servidor no devolvió JSON válido.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  return {
    getSignals: () => request('/signals'),
    getTransactions: () => request('/transactions'),
    advanceDay: () => request('/simulation/advance-day', { method: 'POST' }),
    resetSimulation: () => request('/simulation/advance-day', { method: 'POST', reset: true }),
  };
}
export const api = createApi({ baseUrl: import.meta.env?.VITE_API_BASE_URL || DEFAULT_BASE, userId: import.meta.env?.VITE_USER_ID || 'mia' });
export const sessionKey = `centinel:${import.meta.env?.VITE_API_BASE_URL || DEFAULT_BASE}:${import.meta.env?.VITE_USER_ID || 'mia'}`;
