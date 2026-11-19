import { randomUUID } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/control-auth", () => ({
  isLocalMode: vi.fn(() => process.env.CONTROL_LOCAL_MODE === "1"),
  requireOwnerSession: vi.fn(async () => ({ id: 1 })),
  createSession: vi.fn(async () => {}),
  verifyTelegramInitData: vi.fn(() => {
    throw new Error("Missing Telegram authentication data");
  }),
}));

// Юнит-тест не должен поднимать реальный bot.py (сеть/сессии): исполнитель мокается,
// сама локальная очередь (SQLite) работает по-настоящему.
vi.mock("../../lib/local-executor", () => ({
  localRunCommand: vi.fn(async () => ({ ok: true, output: "ok", errorCode: null })),
  localCancelRun: vi.fn(() => false),
}));

import { POST as postSession } from "../../app/api/control/session/route";
import { POST as postCommand } from "../../app/api/control/commands/route";
import { POST as postConfirmation } from "../../app/api/control/confirmations/route";

const IDEMP_UUID = "44444444-4444-4444-8444-444444444444";

describe("local mode routes (CONTROL_LOCAL_MODE=1)", () => {
  // Изолированный SQLite для каждого теста: не трогаем ../local_control.db.
  let queueDbPath;

  beforeEach(() => {
    queueDbPath = path.join(os.tmpdir(), `ctl-local-test-${process.pid}-${randomUUID()}.db`);
    process.env.LOCAL_QUEUE_DB = queueDbPath;
  });

  afterEach(() => {
    delete process.env.CONTROL_LOCAL_MODE;
    delete process.env.SUPABASE_URL;
    delete process.env.LOCAL_QUEUE_DB;
    try {
      fs.unlinkSync(queueDbPath);
    } catch {
      // файл мог не создаться — не важно
    }
    vi.clearAllMocks();
  });

  it("сессия открывается без initData в локальном режиме", async () => {
    process.env.CONTROL_LOCAL_MODE = "1";
    const res = await postSession(new Request("http://localhost/api/control/session", { method: "POST" }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, owner: { firstName: "local" } });
  });

  // Контракт WIP: локальный режим (CONTROL_LOCAL_MODE=1 + отсутствие SUPABASE_URL)
  // — это полноценная локальная очередь (lib/local-queue.js): команда уходит в
  // локальный SQLite и исполняется через bot.py напрямую, без облака. Поэтому
  // ответ 200 с command_id, а не 503 «режим просмотра» (такого режима в коде нет).
  it("без облака команда уходит в локальную очередь: 200 + command_id", async () => {
    process.env.CONTROL_LOCAL_MODE = "1";
    const res = await postCommand(new Request("http://localhost/api/control/commands", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        kind: "STATUS",
        mode: "READ",
        account: "main",
        payload: {},
        idempotency_key: IDEMP_UUID,
      }),
    }));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.command_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(body.status).toBe("RUNNING");
    expect(body.idempotent).toBe(false);
  });

  it("без облака подтверждение LIVE создаётся локально: 200 + nonce", async () => {
    process.env.CONTROL_LOCAL_MODE = "1";
    const res = await postConfirmation(new Request("http://localhost/api/control/confirmations", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind: "BROADCAST", mode: "LIVE", account: "main", payload: { text: "hi" } }),
    }));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.nonce).toMatch(/^[0-9a-f-]{36}$/);
    expect(body.expires_at).toBeTruthy();
    expect(body.request_digest).toMatch(/^[0-9a-f]{64}$/);
    expect(body.canonical_summary).toContain("BROADCAST");
  });
});
