/** Signed Mini App initData from WebApp API or Telegram Desktop launch hash. */

export function extractTelegramInitData({ initData, hash, search } = {}) {
  if (typeof initData === "string" && initData.trim()) return initData.trim();
  for (const source of [hash, search]) {
    if (!source || typeof source !== "string") continue;
    const raw = source.replace(/^[#?]/, "");
    if (!raw) continue;
    const value = new URLSearchParams(raw).get("tgWebAppData");
    if (value) return value;
  }
  return "";
}

export function readBrowserInitData() {
  if (typeof window === "undefined") return "";
  return extractTelegramInitData({
    initData: window.Telegram?.WebApp?.initData,
    hash: window.location.hash,
    search: window.location.search,
  });
}

export async function waitForTelegramInitData(timeoutMs = 5000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    window.Telegram?.WebApp?.ready?.();
    const initData = readBrowserInitData();
    if (initData) {
      window.Telegram?.WebApp?.expand?.();
      return initData;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 50));
  }
  throw new Error("Telegram не передал данные входа. Нажми «Открыть панель» внизу чата, не ссылку в браузере.");
}
