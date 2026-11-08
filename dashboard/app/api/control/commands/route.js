/**
 * GET /api/control/commands — история команд владельца
 * POST /api/control/commands — поставить новую команду в очередь (через Supabase RPC)
 */

import { createServerClient } from '@supabase/ssr';
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

async function getSupabaseClient() {
  const cookieStore = cookies();
  return createServerClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_SECRET_KEY,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll().map((c) => ({ name: c.name, value: c.value }));
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options)
          );
        },
      },
    }
  );
}

export async function GET(req) {
  const session = readSession();
  if (!session || !session.telegram_user_id) {
    return fail(401, 'требуется сессия');
  }

  const sb = await getSupabaseClient();
  const { searchParams } = new URL(req.url);
  const limit = Math.min(parseInt(searchParams.get('limit') || '50', 10), 100);
  const offset = parseInt(searchParams.get('offset') || '0', 10);

  const { data, error } = await sb
    .from('control_commands')
    .select('*')
    .eq('owner_telegram_id', session.telegram_user_id)
    .order('created_at', { ascending: false })
    .range(offset, offset + limit - 1);

  if (error) {
    return fail(500, error.message);
  }

  return new Response(
    JSON.stringify({ ok: true, commands: data || [] }),
    { headers: { 'content-type': 'application/json' } }
  );
}

export async function POST(req) {
  const session = readSession();
  if (!session || !session.telegram_user_id) {
    return fail(401, 'требуется сессия');
  }

  let body;
  try {
    body = await req.json();
  } catch (e) {
    return fail(400, 'неверный JSON');
  }

  const { kind, mode, account, payload, idempotency_key, confirmation_nonce } = body || {};

  if (!kind || !mode) {
    return fail(400, 'kind и mode обязательны');
  }

  // Валидация подтверждения дляライブ команд
  const liveKinds = ['BROADCAST', 'DMSEND', 'CONDUCT'];
  if (mode === 'LIVE' && liveKinds.includes(kind) && !confirmation_nonce) {
    return fail(400, 'LIVE режим требует confirmation_nonce');
  }

  const sb = await getSupabaseClient();

  // Идемпотентная вставка
  const { data: existing } = await sb
    .from('control_commands')
    .select('id,status')
    .eq('owner_telegram_id', session.telegram_user_id)
    .eq('idempotency_key', idempotency_key)
    .maybeSingle();

  if (existing) {
    return new Response(
      JSON.stringify({ ok: true, command_id: existing.id, status: existing.status, idempotent: true }),
      { headers: { 'content-type': 'application/json' } }
    );
  }

  const { data, error } = await sb
    .from('control_commands')
    .insert([
      {
        idempotency_key,
        owner_telegram_id: session.telegram_user_id,
        kind,
        mode,
        account: account || 'main',
        payload: payload || {},
        request_digest: '', // заполнится триггером или на стороне воркера
        status: 'PENDING',
        version: 0,
      },
    ])
    .select('id')
    .single();

  if (error) {
    return fail(500, error.message);
  }

  return new Response(
    JSON.stringify({ ok: true, command_id: data.id, status: 'PENDING' }),
    { headers: { 'content-type': 'application/json' } }
  );
}
