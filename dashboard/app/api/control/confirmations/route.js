/**
 * POST /api/control/confirmations
 * Создаёт короткоживущее намерение подтверждения (nonce) для LIVE-команды.
 * Суммаризация формируется на сервере — браузер не может её подделать.
 */

import { cookies } from 'next/headers';
import { v4 as uuidv4 } from 'uuid';

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

  const { kind, mode, account, payload } = body || {};

  if (!kind || !mode) {
    return fail(400, 'kind и mode обязательны');
  }

  const liveKinds = ['BROADCAST', 'DMSEND', 'CONDUCT'];
  if (mode !== 'LIVE' || !liveKinds.includes(kind)) {
    return fail(400, 'подтверждение требуется только для LIVE-команд');
  }

  // Формируем серверную суммаризацию (без user_id получателей)
  const summaryParts = [kind];
  if (payload && payload.text) summaryParts.push(`text="${payload.text.slice(0, 60)}..."`);
  if (payload && payload.group_ids) summaryParts.push(`targets=${payload.group_ids.length}`);
  if (payload && payload.limit) summaryParts.push(`limit=${payload.limit}`);
  if (payload && payload.delay_min != null) summaryParts.push(`delay=${payload.delay_min}-${payload.delay_max || 0}s`);
  const canonicalSummary = summaryParts.join(' | ');
  const requestDigest = Buffer.from(
    JSON.stringify({ kind, mode, account, payload }, (k, v) =>
      k === 'idempotency_key' || k === 'confirmation_nonce' ? undefined : v
    )
  ).toString('base64');

  const nonce = uuidv4();
  const expiresAt = new Date(Date.now() + 5 * 60 * 1000).toISOString();

  // В реальной реализации здесь была бы запись в control_confirmations через Supabase
  // Для MVP возвращаем намерение клиенту (фронт передаёт nonce back при enqueue)

  return new Response(
    JSON.stringify({
      ok: true,
      nonce,
      request_digest: requestDigest,
      expires_at: expiresAt,
      canonical_summary: canonicalSummary,
    }),
    { headers: { 'content-type': 'application/json' } }
  );
}
