/**
 * Клиент-помощник для Telegram Web App.
 * Работает только в браузере после монтирования компонента.
 */
import { init, ready, expand, MainButton, HapticFeedback, themeColor, popup } from '@telegram-apps/sdk';

let initialized = false;

export function initTelegramWebApp() {
  if (typeof window === 'undefined' || initialized) return;
  try {
    init();
    ready();
    expand();
    initialized = true;
  } catch (e) {
    // Не браузер Telegram — продолжаем как обычный веб-сайт
    console.warn('Telegram WebApp не доступен:', e.message);
  }
}

export function isTelegramWebApp() {
  return typeof window !== 'undefined' && !!window.Telegram?.WebApp;
}

export function setMainButton(text, onClick, show = true) {
  if (!isTelegramWebApp()) return;
  try {
    MainButton.setText(text);
    MainButton.onClick(onClick);
    if (show) MainButton.show();
    else MainButton.hide();
  } catch (e) {
    console.warn('MainButton:', e.message);
  }
}

export function showAlert(message) {
  if (!isTelegramWebApp()) {
    alert(message);
    return;
  }
  try {
    popup.show(message, { type: 'alert' });
  } catch (e) {
    alert(message);
  }
}

export function hapticImpact(style = 'light') {
  if (!isTelegramWebApp()) return;
  try {
    HapticFeedback.impactOccurred({ type: style });
  } catch (e) {
    // ignored
  }
}

export function getThemeColor(key) {
  if (!isTelegramWebApp()) return null;
  try {
    return themeColor.get(key);
  } catch (e) {
    return null;
  }
}

export function getInitData() {
  if (!isTelegramWebApp()) return null;
  try {
    return window.Telegram.WebApp.initData;
  } catch (e) {
    return null;
  }
}

export function getInitDataUnsafe() {
  if (!isTelegramWebApp()) return null;
  try {
    return window.Telegram.WebApp.initDataUnsafe;
  } catch (e) {
    return null;
  }
}
