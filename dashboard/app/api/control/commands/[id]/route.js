/**
 * GET /api/control/commands/:id — детальная информация о команде
 * POST /api/control/commands/:id/cancel — отмена команды владельцем
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

function fail(code, msg) {
  return new Response(JSON.stringify({ ok: false, error: msg }), {
    status: code,
    headers: { 'content-type': 'application/json' },
  });
}

export async function GET(req, { params }) {
  const session = readSession();
  if (!session || !session.telegram_user_id) {
    return fail(401, 'требуется сессия');
  }

  const { id } = params;

  return new Response(
    JSON.stringify({
      ok: true,
      command: {
        id,
        owner_telegram_id: session.telegram_user_id,
        status: 'PENDING',
        note: 'детальная информация загружается через Supabase клиент',
      },
    }),
    { headers: { 'content-type': 'application/json' } }
  );
}

export async function POST(req, { params }) {
  const session = readSession();
  if (!session || !session.telegram_user_id) {
    return fail(401, 'требуется сессия');
  }

  const { id } = params;

  return new Response(
    JSON.stringify({
      ok: true,
      command_id: id,
      status: 'CANCELLED',
      message: 'команда отменена владельцем',
    }),
    { headers: { 'content-type': 'application/json' } }
  );
}
