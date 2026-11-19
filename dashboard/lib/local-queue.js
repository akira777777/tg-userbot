import "server-only";

import path from "node:path";
import crypto from "node:crypto";
import { DatabaseSync } from "node:sqlite";

import { isLocalMode } from "./control-auth";
import { ControlHttpError } from "./control-queue";

/**
 * Локальная очередь команд для режима «панель на своём компьютере».
 * Хранит команды и события в собственном SQLite-файле рядом с userbot.db
 * и исполняет их через bot.py напрямую — без Supabase.
 *
 * Требует Node >= 22.5 (встроенный node:sqlite).
 */

function localQueueDbPath() {
  const configured = process.env.LOCAL_QUEUE_DB?.trim();
  if (configured) return configured;
  return path.resolve(process.cwd(), "..", "local_control.db");
}

const SCHEMA = `
create table if not exists local_commands (
  id text primary key,
  idempotency_key text not null,
  kind text not null,
  mode text not null,
  account text,
  payload text,
  status text not null,
  created_at text not null,
  claimed_at text,
  finished_at text,
  result_summary text,
  error_code text
);
create unique index if not exists local_commands_idempotency on local_commands (idempotency_key);
create index if not exists local_commands_created on local_commands (created_at desc);

create table if not exists local_events (
  command_id text not null,
  sequence integer not null,
  occurred_at text not null,
  level text not null,
  event_type text not null,
  safe_message text,
  primary key (command_id, sequence)
);

create table if not exists local_confirmations (
  nonce text primary key,
  request_digest text not null,
  expires_at text not null,
  consumed_at text
);
`;

function openDb() {
  return new DatabaseSync(localQueueDbPath());
}

function run(db, sql, params = []) {
  db.prepare(sql).run(...params);
}

function get(db, sql, params = []) {
  return db.prepare(sql).get(...params) ?? null;
}

function all(db, sql, params = []) {
  return db.prepare(sql).all(...params);
}

export function localQueueOpen() {
  const db = openDb();
  db.exec(SCHEMA);
  return db;
}

export function isLocalQueueMode() {
  return isLocalMode() && !process.env.SUPABASE_URL?.trim();
}

const SAFE_COMMAND_FIELDS = ["id", "kind", "mode", "account", "status", "created_at", "claimed_at", "finished_at", "error_code", "result_summary"];

function rowToCommand(row) {
  const command = {};
  for (const field of SAFE_COMMAND_FIELDS) command[field] = row?.[field] ?? null;
  if (typeof command.result_summary === "string" && command.result_summary) {
    try {
      command.result_summary = JSON.parse(command.result_summary);
    } catch {
      command.result_summary = null;
    }
  }
  return command;
}

/** Вставка PENDING-команды; при повторном idempotency_key возвращает существующую. */
export function localEnqueueCommand({ id, idempotencyKey, kind, mode, account, payload }) {
  const db = localQueueOpen();
  try {
    const existing = get(db, "select * from local_commands where idempotency_key = ?", [idempotencyKey]);
    if (existing) {
      return { ...rowToCommand(existing), idempotent: true };
    }
    const now = new Date().toISOString();
    run(
      db,
      "insert into local_commands (id, idempotency_key, kind, mode, account, payload, status, created_at) values (?, ?, ?, ?, ?, ?, 'PENDING', ?)",
      [id, idempotencyKey, kind, mode, account, JSON.stringify(payload ?? {}), now],
    );
    appendLocalEvent(db, id, 1, "info", "queued", `команда поставлена в очередь: ${kind} (${mode})`);
    return { ...rowToCommand(get(db, "select * from local_commands where id = ?", [id])), idempotent: false };
  } finally {
    db.close();
  }
}

export function localListCommands({ limit = 50, offset = 0 }) {
  const db = localQueueOpen();
  try {
    const safeLimit = Math.max(1, Math.min(100, Number.isInteger(limit) ? limit : 50));
    const safeOffset = Math.max(0, Number.isInteger(offset) ? offset : 0);
    const rows = all(db, "select * from local_commands order by created_at desc limit ? offset ?", [safeLimit, safeOffset]);
    return rows.map(rowToCommand);
  } finally {
    db.close();
  }
}

export function localGetCommand(id) {
  const db = localQueueOpen();
  try {
    const row = get(db, "select * from local_commands where id = ?", [id]);
    return row ? rowToCommand(row) : null;
  } finally {
    db.close();
  }
}

export function localMarkRunning(id) {
  const db = localQueueOpen();
  try {
    run(db, "update local_commands set status = 'RUNNING', claimed_at = ? where id = ? and status = 'PENDING'", [new Date().toISOString(), id]);
    const row = get(db, "select * from local_commands where id = ?", [id]);
    appendLocalEvent(db, id, nextSequence(db, id), "info", "started", "выполнение началось");
    return row ? rowToCommand(row) : null;
  } finally {
    db.close();
  }
}

export function localFinishCommand(id, { ok, output, errorCode }) {
  const db = localQueueOpen();
  try {
    // Отменённую команду не перезаписываем: kill процесса может прийти позже отмены.
    const current = get(db, "select status from local_commands where id = ?", [id]);
    if (!current || (current.status !== "RUNNING" && current.status !== "PENDING")) {
      return null;
    }
    const status = ok ? "SUCCEEDED" : "FAILED";
    run(
      db,
      "update local_commands set status = ?, finished_at = ?, result_summary = ?, error_code = ? where id = ?",
      [status, new Date().toISOString(), JSON.stringify({ ok, output }), errorCode ?? null, id],
    );
    appendLocalEvent(
      db,
      id,
      nextSequence(db, id),
      ok ? "info" : "error",
      ok ? "completed" : "failed",
      ok ? "команда выполнена" : "ошибка выполнения",
    );
    const row = get(db, "select * from local_commands where id = ?", [id]);
    return row ? rowToCommand(row) : null;
  } finally {
    db.close();
  }
}

export function localCancelCommand(id) {
  const db = localQueueOpen();
  try {
    const row = get(db, "select * from local_commands where id = ?", [id]);
    if (!row) return null;
    if (row.status !== "PENDING" && row.status !== "RUNNING") return rowToCommand(row);
    run(db, "update local_commands set status = 'CANCELLED', finished_at = ? where id = ?", [new Date().toISOString(), id]);
    appendLocalEvent(db, id, nextSequence(db, id), "warn", "cancelled", "команда отменена");
    const updated = get(db, "select * from local_commands where id = ?", [id]);
    return rowToCommand(updated);
  } finally {
    db.close();
  }
}

export function localListEvents(id) {
  const db = localQueueOpen();
  try {
    return all(db, "select * from local_events where command_id = ? order by sequence", [id]);
  } finally {
    db.close();
  }
}

function nextSequence(db, commandId) {
  const row = get(db, "select max(sequence) as seq from local_events where command_id = ?", [commandId]);
  return Number(row?.seq ?? 0) + 1;
}

function appendLocalEvent(db, commandId, sequence, level, eventType, safeMessage) {
  run(db, "insert into local_events (command_id, sequence, occurred_at, level, event_type, safe_message) values (?, ?, ?, ?, ?, ?)", [
    commandId,
    sequence,
    new Date().toISOString(),
    level,
    eventType,
    safeMessage,
  ]);
}

export function localCreateConfirmation({ requestDigest }) {
  const db = localQueueOpen();
  try {
    const nonce = crypto.randomUUID();
    const expiresAt = new Date(Date.now() + 5 * 60 * 1000).toISOString();
    run(db, "insert into local_confirmations (nonce, request_digest, expires_at) values (?, ?, ?)", [nonce, requestDigest, expiresAt]);
    return { nonce, expires_at: expiresAt };
  } finally {
    db.close();
  }
}

export function localConsumeConfirmation({ nonce, requestDigest }) {
  const db = localQueueOpen();
  try {
    const row = get(db, "select * from local_confirmations where nonce = ? and consumed_at is null and expires_at > ?", [
      nonce,
      new Date().toISOString(),
    ]);
    if (!row || row.request_digest !== requestDigest) return false;
    run(db, "update local_confirmations set consumed_at = ? where nonce = ?", [new Date().toISOString(), nonce]);
    return true;
  } finally {
    db.close();
  }
}

export function localValidateNonce(nonce) {
  if (nonce === undefined || nonce === null) throw new ControlHttpError(400, "LIVE режим требует confirmation_nonce");
  if (typeof nonce !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(nonce)) {
    throw new ControlHttpError(400, "некорректный confirmation_nonce");
  }
}
