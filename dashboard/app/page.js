"use client";

import { useCallback, useEffect, useState } from 'react';
import MiniAppShell from '@/components/mini-app-shell';
import ButtonBar from '@/components/button-bar';
import CommandForm from '@/components/command-form';
import CommandTimeline from '@/components/command-timeline';
import OperationsDashboard from '@/components/operations-dashboard';
import { initTelegramWebApp, isTelegramWebApp, getInitData, setMainButton, hapticImpact } from '@/lib/telegram';
import { enqueueCommand, createConfirmation, getBootstrap } from '@/lib/api';

export default function Home() {
  const [tgReady, setTgReady] = useState(false);
  const [initData, setInitData] = useState(null);
  const [activeAction, setActiveAction] = useState(null);
  const [bootstrap, setBootstrap] = useState(null);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const tg = isTelegramWebApp();
    if (tg) {
      initTelegramWebApp();
      const data = getInitData();
      setInitData(data);
      setTgReady(true);
    } else {
      setTgReady(true);
    }
  }, []);

  const loadBootstrap = useCallback(async () => {
    if (!initData) return;
    try {
      const data = await getBootstrap(initData);
      setBootstrap(data);
    } catch (e) {
      console.error('bootstrap:', e.message);
    }
  }, [initData]);

  useEffect(() => {
    if (initData) loadBootstrap();
  }, [initData, loadBootstrap]);

  const handleAction = (btn) => {
    hapticImpact('light');
    setActiveAction(btn);
    setMainButton(btn.kind === 'LIVE' ? 'Подтвердить запуск' : 'Выполнить', () => {
      if (!initData) return;
      const command = {
        kind: btn.id === 'broadcast_chat' || btn.id === 'broadcast_channel' ? 'BROADCAST' : 'READ',
        mode: btn.kind,
        account: 'main',
        payload: {
          text: '',
          delay_min: 20,
          delay_max: 45,
          group_ids: [],
          limit: 200,
          repeat_ok: false,
          dry_run: btn.kind === 'READ',
        },
        idempotency_key: crypto.randomUUID(),
      };

      if (btn.kind === 'LIVE') {
        createConfirmation(command, initData)
          .then((conf) => {
            if (conf.ok) {
              command.confirmation_nonce = conf.nonce;
              return enqueueCommand(command, initData);
            }
          })
          .then((res) => {
            if (res) console.log('команда поставлена:', res);
          })
          .catch((err) => console.error('ошибка:', err.message));
      } else {
        enqueueCommand(command, initData)
          .then((res) => console.log('команда поставлена:', res))
          .catch((err) => console.error('ошибка:', err.message));
      }
    }, true);
  };

  if (!tgReady) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '40vh', color: '#8b98a9' }}>
        Подключение к Telegram Web App…
      </div>
    );
  }

  return (
    <MiniAppShell>
      <div style={{ maxWidth: 500, margin: '0 auto' }}>
        <h1 style={{ fontSize: 20, margin: '0 0 16px', color: '#7fd1ff' }}>tg-userbot · мини-ап</h1>

        <div style={{ marginBottom: 16, padding: 12, background: 'var(--tg-theme-secondary-bg-color, #121722)', border: '1px solid #2a3548', borderRadius: 10 }}>
          <div style={{ fontSize: 12, color: '#8b98a9', marginBottom: 6 }}>Статус подключения</div>
          <div style={{ fontSize: 14, color: '#6ee7a0', fontWeight: 600 }}>
            {isTelegramWebApp() ? 'Telegram Web App активен' : 'Веб-режим (без Telegram)'}
          </div>
        </div>

        <ButtonBar onAction={handleAction} />

        {activeAction && (
          <CommandForm
            action={activeAction}
            onSubmit={(cmd) => {
              if (activeAction.kind === 'LIVE') {
                createConfirmation(cmd, initData)
                  .then((conf) => {
                    if (conf.ok) {
                      cmd.confirmation_nonce = conf.nonce;
                      return enqueueCommand(cmd, initData);
                    }
                  })
                  .catch((err) => console.error('ошибка подтверждения:', err.message));
              } else {
                enqueueCommand(cmd, initData).catch((err) =>
                  console.error('ошибка отправки:', err.message)
                );
              }
              setActiveAction(null);
            }}
            initData={initData}
          />
        )}

        <CommandTimeline initData={initData} />
        <OperationsDashboard />
      </div>
    </MiniAppShell>
  );
}
