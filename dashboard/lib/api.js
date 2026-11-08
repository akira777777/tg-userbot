/**
 * Типизированный клиент для внутренних /api/control/* роутов Next.js.
 * Автоматически подставляет заголовок X-Telegram-Init-Data из WebApp.
 * Обрабатывает 401 -> сброс сессии.
 */

export async function apiRequest(path, { method = 'GET', body, initData } = {}) {
  const headers = {
    'content-type': 'application/json',
  };
  if (initData) headers['x-telegram-init-data'] = initData;

  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });

  if (res.status === 401) {
    const err = new Error('Unauthorized');
    err.status = 401;
    throw err;
  }

  let data;
  try {
    data = await res.json();
  } catch (e) {
    data = { ok: false, error: `HTTP ${res.status}` };
  }

  if (!res.ok) {
    const err = new Error(data.error || 'Ошибка запроса');
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export async function getBootstrap(initData) {
  return apiRequest('/api/control/bootstrap', { initData });
}

export async function enqueueCommand(commandRequest, initData) {
  return apiRequest('/api/control/commands', {
    method: 'POST',
    body: commandRequest,
    initData,
  });
}

export async function getCommands(initData, { limit = 50, offset = 0 } = {}) {
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) }).toString();
  return apiRequest(`/api/control/commands?${qs}`, { initData });
}

export async function cancelCommand(commandId, initData) {
  return apiRequest(`/api/control/commands/${encodeURIComponent(commandId)}`, {
    method: 'POST',
    initData,
  });
}

export async function createConfirmation(commandRequest, initData) {
  return apiRequest('/api/control/confirmations', {
    method: 'POST',
    body: commandRequest,
    initData,
  });
}
