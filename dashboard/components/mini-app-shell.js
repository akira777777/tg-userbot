"use client";

import { useEffect, useState } from 'react';
import { initTelegramWebApp, isTelegramWebApp, getInitData } from '@/lib/telegram';

export default function MiniAppShell({ children }) {
  const [initialized, setInitialized] = useState(false);
  const [tgAvailable, setTgAvailable] = useState(false);

  useEffect(() => {
    const tg = isTelegramWebApp();
    setTgAvailable(tg);
    if (tg) {
      initTelegramWebApp();
      setInitialized(true);
    } else {
      setInitialized(true); // всё равно показываем приложение (веб-режим)
    }
  }, []);

  if (!initialized) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '40vh', color: '#8b98a9' }}>
        Инициализация Telegram Web App…
      </div>
    );
  }

  const initData = getInitData();

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'var(--tg-theme-bg-color, #0b0e14)',
        color: 'var(--tg-theme-text-color, #dbe2ea)',
        fontFamily: 'ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif',
      }}
    >
      {tgAvailable ? (
        <div style={{ padding: '8px 12px', background: 'var(--tg-theme-secondary-bg-color, #121722)', borderBottom: '1px solid var(--tg-theme-hint-color, #1f2836)' }}>
          <div style={{ fontSize: '12px', opacity: 0.7 }}>tg-userbot · панель управления</div>
        </div>
      ) : null}
      <main style={{ padding: 16px }}>{initData ? children : <div>Ожидание данных Telegram…</div>}</main>
    </div>
  );
}
