import "server-only";
import crypto from "node:crypto";

import { accountAliasMap } from "./account-aliases";
import { isLocalMode } from "./control-auth";

/**
 * Серверный клиент очереди команд Supabase (control_commands / control_command_events).
 * Вызывается только из серверных /api/control/* роутов после requireOwnerSession().
 */

export class ControlHttpError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ControlHttpError";
    this.status = status;
  }
}

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ACCOUNT_RE = /^[a-z0-9_\-]{1,32}$/;
const SAFE_COMMAND_COLUMNS = "id,kind,mode,account,status,created_at,claimed_at,finished_at,error_code,result_summary";

export function isUuid(value) {
  return typeof value === "string" && UUID_RE.test(value);
}

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
  if (!url) throw new ControlHttpError(503, "Cloud mirror is not configured");
  return `${url}/rest/v1/${path}`;
}

const LIVE_KINDS = new Set(["BROADCAST", "DMSEND", "CONDUCT"]);
const READ_KINDS = new Set(["STATUS", "GROUPS", "CAMPAIGNS", "MEDIA_LIST"]);
export const SUPPORTED_COMMANDS = new Set([...READ_KINDS, ...LIVE_KINDS, "BLACKLIST_ADD", "STOP"]);

export function commandRejection(kind, mode) {
  if (!SUPPORTED_COMMANDS.has(kind)) return "неподдерживаемая команда";
  if (READ_KINDS.has(kind) && mode !== "READ") return "read-only команда требует режим READ";
  if (LIVE_KINDS.has(kind) && mode === "READ") return "команда доставки не может быть READ";
  if (!["READ", "DRY_RUN", "LIVE"].includes(mode)) return "неподдерживаемый режим";
  return null;
}

const MAX_TEXT_LENGTH = 4000;
const MAX_GROUP_IDS = 200;
const MAX_DM_LIMIT = 200;
const MAX_CONDUCT_BOTH_LIMIT = 10000;
const MAX_DELAY_SECONDS = 3600;

export function requiresConfirmation(kind, mode) {
  return mode === "LIVE" && LIVE_KINDS.has(kind);
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function validateAccount(account) {
  if (account === undefined || account === null || account === "") return null;
  if (typeof account !== "string" || !ACCOUNT_RE.test(account)) {
    return "некорректное имя аккаунта";
  }
  return null;
}

export function validateCommandPayload(kind, payload) {
  if (!isPlainObject(payload)) return "payload обязателен";
  if (payload.text !== undefined && payload.text !== null) {
    if (typeof payload.text !== "string") return "text должен быть строкой";
    if (payload.text.length > MAX_TEXT_LENGTH) return "текст слишком длинный";
  }
  if (payload.group_ids !== undefined && payload.group_ids !== null) {
    if (!Array.isArray(payload.group_ids)) return "group_ids должен быть массивом";
    if (payload.group_ids.length > MAX_GROUP_IDS) return "слишком много group_ids";
    if (payload.group_ids.some((id) => !Number.isSafeInteger(id) || id === 0)) {
      return "group_ids должен содержать ненулевые целые ID";
    }
  }
  if (payload.group_id !== undefined && payload.group_id !== null) {
    if (!Number.isSafeInteger(payload.group_id) || payload.group_id === 0) {
      return "group_id должен быть ненулевым целым числом";
    }
  }
  if (payload.delay_min !== undefined && payload.delay_min !== null) {
    if (typeof payload.delay_min !== "number" || !Number.isFinite(payload.delay_min) || payload.delay_min < 0 || payload.delay_min > MAX_DELAY_SECONDS) {
      return "некорректный delay_min";
    }
  }
  if (payload.delay_max !== undefined && payload.delay_max !== null) {
    if (typeof payload.delay_max !== "number" || !Number.isFinite(payload.delay_max) || payload.delay_max < 0 || payload.delay_max > MAX_DELAY_SECONDS) {
      return "некорректный delay_max";
    }
  }
  if (
    typeof payload.delay_min === "number" &&
    typeof payload.delay_max === "number" &&
    payload.delay_max > 0 &&
    payload.delay_min > payload.delay_max
  ) {
    return "delay_min не может быть больше delay_max";
  }
  if (kind === "BLACKLIST_ADD") {
    if (typeof payload.value !== "string" || !payload.value.trim() || payload.value.length > 128) {
      return "для BLACKLIST_ADD требуется непустая строка value";
    }
    if (payload.reason !== undefined && (typeof payload.reason !== "string" || payload.reason.length > 256)) {
      return "некорректная причина reason";
    }
  }
  if (kind === "STOP") {
    if (payload.account !== undefined && payload.account !== null) {
      if (typeof payload.account !== "string" || (payload.account !== "all" && !ACCOUNT_RE.test(payload.account))) {
        return "некорректный account для STOP";
      }
    }
    if (payload.command_id !== undefined && payload.command_id !== null && !isUuid(payload.command_id)) {
      return "некорректный command_id для STOP";
    }
  }
  if (kind === "DMSEND" && payload.limit !== undefined && payload.limit !== null) {
    if (!Number.isSafeInteger(payload.limit) || payload.limit < 1 || payload.limit > MAX_DM_LIMIT) {
      return "превышен лимит получателей";
    }
  }
  if (kind === "CONDUCT") {
    if (!["broadcast", "dm", "both"].includes(payload.kind)) {
      return "conduct.kind должен быть broadcast, dm или both";
    }
    if (payload.limit !== undefined && payload.limit !== null) {
      const maxLimit = payload.kind === "both" ? MAX_CONDUCT_BOTH_LIMIT : MAX_DM_LIMIT;
      if (!Number.isSafeInteger(payload.limit) || payload.limit < 0 || payload.limit > maxLimit) {
        return "превышен лимит получателей";
      }
    }
  }
  return null;
}

export function buildCanonicalSummary(kind, payload) {
  const parts = [kind];
  if (payload?.text) parts.push(`text="${String(payload.text).slice(0, 60)}"`);
  if (payload?.group_ids?.length) parts.push(`targets=${payload.group_ids.length}`);
  if (payload?.group_id != null) parts.push(`group=${payload.group_id}`);
  if (payload?.limit) parts.push(`limit=${payload.limit}`);
  if (payload?.delay_min != null) parts.push(`delay=${payload.delay_min}-${payload.delay_max ?? 0}s`);
  return parts.join(" | ");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function digestCommand(kind, mode, account, payload) {
  return crypto.createHash("sha256").update(canonicalJson({ kind, mode, account: account || "main", payload: payload || {} })).digest("hex");
}

/** Дайджест содержимого команды — для подтверждений локальной очереди. */
export function digestCommandPayload(kind, mode, account, payload) {
  return digestCommand(kind, mode, account, payload);
}

function controlFilter(value) {
  return encodeURIComponent(String(value));
}

/**
 * Карта псевдонимов аккаунтов из tb_accounts (для трансляции acc-N -> имя).
 * Использует тот же fetch-клиент, что и очередь, поэтому route-тесты могут
 * стабить его глобальным fetch. В локальном режиме без облака — пустая карта.
 */
export async function loadAccountAliasMap() {
  if (!setting("SUPABASE_URL")) {
    if (isLocalMode()) {
      const { loadLocalAccountNames } = await import("./local-data");
      return accountAliasMap(await loadLocalAccountNames());
    }
    throw new ControlHttpError(503, "Cloud mirror is not configured");
  }
  const res = await fetch(supabaseUrl("tb_accounts?select=name&order=name"), {
    headers: supabaseHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new ControlHttpError(503, "Cloud mirror is unavailable");
  const rows = await res.json();
  const names = (Array.isArray(rows) ? rows : []).map((row) => row?.name);
  return accountAliasMap(names);
}

export async function createConfirmation({ ownerId, kind, mode, account, payload }) {
  const accountErr = validateAccount(account);
  if (accountErr) throw new ControlHttpError(400, accountErr);
  const validationError = validateCommandPayload(kind, payload);
  if (validationError) throw new ControlHttpError(400, validationError);

  const nonce = crypto.randomUUID();
  const expiresAt = new Date(Date.now() + 5 * 60 * 1000).toISOString();
  const requestDigest = digestCommand(kind, mode, account, payload);
  const response = await fetch(supabaseUrl("control_confirmations"), {
    method: "POST",
    headers: supabaseHeaders(),
    body: JSON.stringify([{ nonce, owner_telegram_id: ownerId, request_digest: requestDigest, expires_at: expiresAt }]),
  });
  if (!response.ok) throw new ControlHttpError(503, "Could not store confirmation");
  return { nonce, request_digest: requestDigest, expires_at: expiresAt, canonical_summary: buildCanonicalSummary(kind, payload) };
}

async function lookupIdempotent({ ownerId, idempotencyKey }) {
  const existing = await fetch(
    supabaseUrl(
      `control_commands?owner_telegram_id=eq.${controlFilter(ownerId)}&idempotency_key=eq.${controlFilter(idempotencyKey)}&select=id,status`
    ),
    { headers: supabaseHeaders(), cache: "no-store" }
  );
  if (!existing.ok) return null;
  const rows = await existing.json();
  return rows.length ? { id: rows[0].id, status: rows[0].status, idempotent: true } : null;
}

async function consumeConfirmation({ ownerId, nonce, requestDigest }) {
  const response = await fetch(
    supabaseUrl(
      `control_confirmations?nonce=eq.${controlFilter(nonce)}&owner_telegram_id=eq.${controlFilter(ownerId)}&request_digest=eq.${controlFilter(requestDigest)}&consumed_at=is.null&expires_at=gt.${controlFilter(new Date().toISOString())}`
    ),
    { method: "PATCH", headers: supabaseHeaders(), body: JSON.stringify({ consumed_at: new Date().toISOString() }) }
  );
  if (!response.ok) throw new ControlHttpError(503, "Could not verify confirmation");
  const rows = await response.json();
  return rows.length > 0;
}

async function releaseConfirmation({ ownerId, nonce }) {
  try {
    await fetch(
      supabaseUrl(`control_confirmations?nonce=eq.${controlFilter(nonce)}&owner_telegram_id=eq.${controlFilter(ownerId)}`),
      { method: "PATCH", headers: supabaseHeaders(), body: JSON.stringify({ consumed_at: null }) }
    );
  } catch {
    // best-effort rollback
  }
}

export async function enqueueCommand({ ownerId, kind, mode, account, payload, idempotencyKey, confirmationNonce }) {
  const rejected = commandRejection(kind, mode);
  if (rejected) throw new ControlHttpError(400, rejected);
  const accountErr = validateAccount(account);
  if (accountErr) throw new ControlHttpError(400, accountErr);
  const validationError = validateCommandPayload(kind, payload);
  if (validationError) throw new ControlHttpError(400, validationError);
  if (!isUuid(idempotencyKey)) throw new ControlHttpError(400, "некорректный idempotency_key");
  if (confirmationNonce !== undefined && confirmationNonce !== null && !isUuid(confirmationNonce)) {
    throw new ControlHttpError(400, "некорректный confirmation_nonce");
  }

  // Идемпотентная проверка
  const already = await lookupIdempotent({ ownerId, idempotencyKey });
  if (already) return already;

  const requestDigest = digestCommand(kind, mode, account, payload);
  let consumed = false;
  if (requiresConfirmation(kind, mode)) {
    if (!confirmationNonce) throw new ControlHttpError(400, "LIVE режим требует confirmation_nonce");
    consumed = await consumeConfirmation({ ownerId, nonce: confirmationNonce, requestDigest });
    if (!consumed) {
      // Гонка по idempotency_key: первый запрос уже мог списать nonce и вставить команду.
      const raced = await lookupIdempotent({ ownerId, idempotencyKey });
      if (raced) return raced;
      throw new ControlHttpError(409, "Confirmation is invalid or expired");
    }
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
        request_digest: requestDigest,
        confirmation_nonce: confirmationNonce ?? null,
        status: "PENDING",
        version: 0,
      },
    ]),
  });

  if (!insertRes.ok) {
    if (insertRes.status === 409) {
      const raced = await lookupIdempotent({ ownerId, idempotencyKey });
      if (raced) return raced;
    }
    if (consumed && confirmationNonce) {
      await releaseConfirmation({ ownerId, nonce: confirmationNonce });
    }
    throw new ControlHttpError(503, "Не удалось поставить команду в очередь");
  }

  const rows = await insertRes.json();
  return { id: rows[0]?.id, status: "PENDING", idempotent: false };
}

export async function listCommands({ ownerId, limit = 50, offset = 0 }) {
  const safeLimit = Number.isInteger(limit) ? Math.max(1, Math.min(100, limit)) : 50;
  const safeOffset = Number.isInteger(offset) ? Math.max(0, offset) : 0;
  if (!setting("SUPABASE_URL")) {
    if (isLocalMode()) return [];
    throw new ControlHttpError(503, "Cloud mirror is not configured");
  }
  const res = await fetch(
    supabaseUrl(
      `control_commands?owner_telegram_id=eq.${controlFilter(ownerId)}&order=created_at.desc&limit=${safeLimit}&offset=${safeOffset}&select=${SAFE_COMMAND_COLUMNS}`
    ),
    { headers: supabaseHeaders(), cache: "no-store" }
  );
  if (!res.ok) return [];
  return res.json();
}

export async function getCommand({ ownerId, commandId }) {
  if (!isUuid(commandId)) throw new ControlHttpError(400, "некорректный идентификатор команды");
  if (!setting("SUPABASE_URL")) {
    if (isLocalMode()) return null;
    throw new ControlHttpError(503, "Cloud mirror is not configured");
  }
  const res = await fetch(
    supabaseUrl(
      `control_commands?id=eq.${controlFilter(commandId)}&owner_telegram_id=eq.${controlFilter(ownerId)}&select=${SAFE_COMMAND_COLUMNS}&limit=1`
    ),
    { headers: supabaseHeaders(), cache: "no-store" }
  );
  if (!res.ok) return null;
  const rows = await res.json();
  return rows[0] || null;
}

export async function cancelCommand({ ownerId, commandId }) {
  if (!isUuid(commandId)) throw new ControlHttpError(400, "некорректный идентификатор команды");
  const res = await fetch(
    supabaseUrl(
      `control_commands?id=eq.${controlFilter(commandId)}&owner_telegram_id=eq.${controlFilter(ownerId)}&status=eq.PENDING&select=id,status`
    ),
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
