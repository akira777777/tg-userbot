/**
 * Клиентский помощник для Telegram Web App.
 * SDK `@telegram-apps/sdk` (v2.x) экспортирует компоненты неймспейсами:
 * `hapticFeedback.impactOccurred(...)`, `miniApp.ready()` и т.д.
 * Все вызовы безопасны вне Telegram — там они просто ничего не делают.
 */
import { init, hapticFeedback, miniApp, viewport } from "@telegram-apps/sdk";

let initialized = false;

function call(fn) {
  try {
    return fn();
  } catch {
    return undefined;
  }
}

export function initTelegramWebApp() {
  if (typeof window === "undefined" || initialized) return;
  initialized = true;
  call(() => init());
  call(() => miniApp.ready?.());
  call(() => viewport.expand?.());
  call(() => miniApp.setHeaderColor?.("bg_color"));
  call(() => miniApp.setBackgroundColor?.("bg_color"));
}

export function isTelegramWebApp() {
  return typeof window !== "undefined" && Boolean(window.Telegram?.WebApp);
}

/** Тактильный отклик. style: light | medium | heavy | rigid | soft. */
export function hapticImpact(style = "light") {
  if (!isTelegramWebApp()) return;
  call(() => hapticFeedback.impactOccurred(style));
}

/** Второй сигнал: "error" для ошибок, "success" для успеха. */
export function hapticNotify(type = "success") {
  if (!isTelegramWebApp()) return;
  call(() => hapticFeedback.notificationOccurred(type));
}

export function showAlert(message) {
  if (typeof window === "undefined") return;
  if (!isTelegramWebApp()) {
    window.alert(message);
    return;
  }
  try {
    window.Telegram.WebApp.showAlert(message);
  } catch {
    window.alert(message);
  }
}

export function getInitData() {
  if (typeof window === "undefined") return null;
  try {
    return window.Telegram?.WebApp?.initData || null;
  } catch {
    return null;
  }
}

export function getInitDataUnsafe() {
  if (typeof window === "undefined") return null;
  try {
    return window.Telegram?.WebApp?.initDataUnsafe || null;
  } catch {
    return null;
  }
}
