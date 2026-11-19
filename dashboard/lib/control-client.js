/**
 * Клиент запросов к /api/control/* с автоматической авторизацией Telegram Mini App
 * и однократным обновлением cookie-сессии при HTTP 401.
 */
import { readBrowserInitData, waitForTelegramInitData } from "./telegram-init-data";

let inflightSession = null;

export async function establishControlSession({ waitMs = 3000 } = {}) {
  if (inflightSession) return inflightSession;
  inflightSession = (async () => {
    const immediate = readBrowserInitData();
    const initData = immediate || (waitMs > 0 ? await waitForTelegramInitData(waitMs) : "");
    if (!initData) {
      throw new Error("Telegram не передал данные входа. Нажми «Открыть панель» внизу чата, не ссылку в браузере.");
    }
    const response = await fetch("/api/control/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "include",
      cache: "no-store",
      body: JSON.stringify({ initData }),
    });
    let body = {};
    try {
      body = await response.json();
    } catch {
      body = { ok: false, error: `HTTP ${response.status}` };
    }
    if (!response.ok || !body.ok) {
      throw new Error(body.error || "Не удалось подтвердить доступ Telegram.");
    }
    return body;
  })();
  try {
    return await inflightSession;
  } finally {
    inflightSession = null;
  }
}

async function parseJsonSafe(response) {
  try {
    return await response.json();
  } catch {
    return { ok: false, error: `HTTP ${response.status}` };
  }
}

export async function controlFetch(path, options = {}) {
  const { retryAuth = true, ...fetchOptions } = options;
  const run = () =>
    fetch(path, {
      cache: "no-store",
      credentials: "include",
      ...fetchOptions,
    });

  let response = await run();
  if (response.status === 401 && retryAuth) {
    await establishControlSession({ waitMs: 1500 });
    response = await run();
  }
  const body = await parseJsonSafe(response);
  return { response, body };
}
