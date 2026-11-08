import "server-only";

/**
 * Серверный клиент очереди команд Supabase (control_commands / control_command_events).
 * Используется только из authenticated /api/control/* роутов после requireOwnerSession().
 */

function setting(name) {
  return process.env[name]?.trim() ?? "";
}

function supabaseHeaders() {
  const key = setting("SUPABASE_SECRET_KEY");
  return {
    apikey: key,
    Authorization: `Bearer ${key}`,
    "content-type": "application/json",
    Prefer: "return=representation",
  };
}

function supabaseUrl(path) {
  const url = setting("SUPABASE_URL");
  if (!url) throw new Error("Cloud mirror is not configured");
  return `${url}/rest/v1/${path}`;
}

const LIVE_KINDS = new Set(["BROADCAST", "DMSEND", "CONDUCT"]);
const MAX_TEXT_LENGTH = 4000;
const MAX_GROUP_IDS = 200;
const MAX_DM_LIMIT = 200;

export function requiresConfirmation(kind, mode) {
  return mode === "LIVE" && LIVE_KINDS.has(kind);
}

export function validateCommandPayload(kind, payload) {
  if (!payload || typeof payload !== "object") return "payload обязателен";
  if (payload.text && payload.text.length > MAX_TEXT_LENGTH) return "текст слишком длинный";
  if (payload.group_ids && payload.group_ids.length > MAX_GROUP_IDS) return "слишком много group_ids";
  if (kind === "DMSEND" && payload.limit && payload.limit > MAX_DM_LIMIT) return "превышен лимит получателей";
  return null;
}

export function buildCanonicalSummary(kind, payload) {
  const parts = [kind];
  if (payload?.text) parts.push(`text="${String(payload.text).slice(0, 60)}"`);
  if (payload?.group_ids?.length) parts.push(`targets=${payload.group_ids.length}`);
  if (payload?.limit) parts.push(`limit=${payload.limit}`);
  if (payload?.delay_min != null) parts.push(`delay=${payload.delay_min}-${payload.delay_max ?? 0}s`);
  return parts.join(" | ");
}

export async function enqueueCommand({ ownerId, kind, mode, account, payload, idempotencyKey }) {
  const validationError = validateCommandPayload(kind, payload);
  if (validationError) throw new Error(validationError);

  // Идемпотентная проверка
  const existing = await fetch(
    supabaseUrl(
      `control_commands?owner_telegram_id=eq.${ownerId}&idempotency_key=eq.${idempotencyKey}&select=id,status`
    ),
    { headers: supabaseHeaders(), cache: "no-store" }
  );
  if (existing.ok) {
    const rows = await existing.json();
    if (rows.length) return { id: rows[0].id, status: rows[0].status, idempotent: true };
  }

  const insertRes = await fetch(supabaseUrl("control_commands"), {
    method: "POST",
    headers: supabaseHeaders(),
    body: JSON.stringify([
      {
        idempotency_key: idempotencyKey,
        owner_telegram_id: ownerId,
        kind,
        mode,
        account: account || "main",
        payload: payload || {},
        request_digest: Buffer.from(JSON.stringify(payload || {})).toString("base64").slice(0, 128),
        status: "PENDING",
        version: 0,
      },
    ]),
  });

  if (!insertRes.ok) {
    throw new Error("Не удалось поставить команду в очередь");
  }

  const rows = await insertRes.json();
  return { id: rows[0]?.id, status: "PENDING", idempotent: false };
}

export async function listCommands({ ownerId, limit = 50, offset = 0 }) {
  const res = await fetch(
    supabaseUrl(
      `control_commands?owner_telegram_id=eq.${ownerId}&order=created_at.desc&limit=${limit}&offset=${offset}`
    ),
    { headers: supabaseHeaders(), cache: "no-store" }
  );
  if (!res.ok) return [];
  return res.json();
}

export async function getCommand({ ownerId, commandId }) {
  const res = await fetch(
    supabaseUrl(`control_commands?id=eq.${commandId}&owner_telegram_id=eq.${ownerId}&limit=1`),
    { headers: supabaseHeaders(), cache: "no-store" }
  );
  if (!res.ok) return null;
  const rows = await res.json();
  return rows[0] || null;
}

export async function cancelCommand({ ownerId, commandId }) {
  const res = await fetch(
    supabaseUrl(`control_commands?id=eq.${commandId}&owner_telegram_id=eq.${ownerId}&status=eq.PENDING`),
    {
      method: "PATCH",
      headers: supabaseHeaders(),
      body: JSON.stringify({ status: "CANCELLED", finished_at: new Date().toISOString() }),
    }
  );
  if (!res.ok) return false;
  const rows = await res.json();
  return rows.length > 0;
}
