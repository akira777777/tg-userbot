/**
 * GET /api/control/bootstrap
 * Возвращает безопасное состояние для владельца: разрешенные команды,
 * активные/недавние команды, агрегированные данные без PII.
 */

import { cookies } from 'next/headers';

export const dynamic = 'force-dynamic';

function readSession() {
  const cookieStore = cookies();
  const raw = cookieStore.get('control_session')?.value;
  if (!raw) return null;
  try {
    return JSON.parse(decodeURIComponent(raw));
  } catch (e) {
    return null;
  }
}

export async function GET(req) {
  const fail = (code, msg) =>
    new Response(JSON.stringify({ ok: false, error: msg }), {
      status: code,
      headers: { 'content-type': 'application/json' },
    });

  const session = readSession();
  if (!session || !session.telegram_user_id) {
    return fail(401, 'требуется сессия');
  }

  const ownerId = session.telegram_user_id;

  // Безопасные данные для Mini App (без user_id получателей, без сырых имён сессий)
  const bootstrap = {
    ok: true,
    owner: {
      id: session.telegram_user_id,
      username: session.username,
      first_name: session.first_name,
    },
    capabilities: {
      readOnly: ['STATUS', 'GROUPS', 'CAMPAIGNS', 'MEDIA_LIST'],
      liveRequiresConfirmation: ['BROADCAST', 'DMSEND', 'CONDUCT'],
      dryRunDefault: true,
      forwardPrivacy: true,
    },
    buttons: [
      { id: 'scrape', label: 'Сбор информации', kind: 'READ', confirm: false },
      { id: 'collect_users', label: 'Сбор юзеров', kind: 'READ', confirm: false },
      { id: 'broadcast_chat', label: 'Рассылка по чатам', kind: 'LIVE', confirm: true },
      { id: 'broadcast_channel', label: 'Рассылка по каналам', kind: 'LIVE', confirm: true },
      { id: 'dm_users', label: 'Рассылка по юзерам', kind: 'LIVE', confirm: true },
      { id: 'media_add', label: 'Добавить медиа', kind: 'READ', confirm: false },
      { id: 'set_interval', label: 'Настройка интервала', kind: 'READ', confirm: false },
      { id: 'start_campaign', label: 'Запуск', kind: 'LIVE', confirm: true },
      { id: 'stop_campaign', label: 'Остановка', kind: 'READ', confirm: false },
    ],
    limits: {
      maxGroupIds: 200,
      maxDmLimit: 200,
      maxTextLength: 4000,
      delayMinSeconds: 0,
      delayMaxSeconds: 3600,
      defaultIntervalMin: 20,
      defaultIntervalMax: 45,
    },
    accounts: [], // заполняется воркером через Supabase, здесь заглушка
    recentCommands: [], // заполняется через /api/control/commands
  };

  return new Response(JSON.stringify(bootstrap), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}
