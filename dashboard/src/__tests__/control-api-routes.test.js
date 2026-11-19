import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/control-auth", () => ({
  requireOwnerSession: vi.fn(),
  isLocalMode: () => false,
}));

import { requireOwnerSession } from "../../lib/control-auth";
import { GET as getCommandById, POST as cancelCommandById } from "../../app/api/control/commands/[id]/route";
import { GET as listCommandsRoute, POST as createCommandRoute } from "../../app/api/control/commands/route";
import { enqueueCommand, getCommand } from "../../lib/control-queue";

const CMD_UUID = "33333333-3333-4333-8333-333333333333";
const IDEMP_UUID = "44444444-4444-4444-8444-444444444444";
const NONCE_UUID = "55555555-5555-4555-8555-555555555555";

describe("Next.js 16 control API routes (DASH-602)", () => {
  beforeEach(() => {
    process.env.SUPABASE_URL = "https://example.supabase.co";
    process.env.SUPABASE_SECRET_KEY = "test-key";
    vi.mocked(requireOwnerSession).mockResolvedValue({ id: 7 });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    delete process.env.SUPABASE_URL;
    delete process.env.SUPABASE_SECRET_KEY;
  });

  it("awaits async params.id and validates UUID on GET /api/control/commands/[id]", async () => {
    const badRes = await getCommandById(new Request("http://localhost/api/control/commands/not-a-uuid"), {
      params: Promise.resolve({ id: "not-a-uuid" }),
    });
    expect(badRes.status).toBe(400);

    let seenCommandUrl = "";
    vi.stubGlobal("fetch", async (url) => {
      const href = String(url);
      if (href.includes("tb_accounts")) return { ok: true, json: async () => [{ name: "main" }] };
      seenCommandUrl = href;
      return {
        ok: true,
        json: async () => [{ id: CMD_UUID, kind: "STATUS", mode: "READ", status: "SUCCEEDED", account: "main" }],
      };
    });

    const okRes = await getCommandById(new Request(`http://localhost/api/control/commands/${CMD_UUID}`), {
      params: Promise.resolve({ id: CMD_UUID }),
    });
    expect(okRes.status).toBe(200);
    const body = await okRes.json();
    expect(body).toMatchObject({ ok: true, command: { id: CMD_UUID, status: "SUCCEEDED" } });
    expect(body.command.account).toBeUndefined();
    expect(body.command.account_display).toBe("Аккаунт 1");
    expect(seenCommandUrl).toContain("select=id,kind,mode,account,status,created_at,claimed_at,finished_at,error_code,result_summary");
    expect(seenCommandUrl).not.toContain("confirmation_nonce");
  });

  it("awaits async params.id and cancels pending command on POST /api/control/commands/[id]", async () => {
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      json: async () => [{ id: CMD_UUID, status: "CANCELLED" }],
    }));

    const res = await cancelCommandById(new Request(`http://localhost/api/control/commands/${CMD_UUID}`, { method: "POST" }), {
      params: Promise.resolve({ id: CMD_UUID }),
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, command_id: CMD_UUID, status: "CANCELLED" });
  });

  it("returns 400 on invalid payload types and 409 on expired confirmation", async () => {
    vi.stubGlobal("fetch", async (url) => {
      const href = String(url);
      if (href.includes("tb_accounts")) return { ok: true, json: async () => [{ name: "main" }] };
      return { ok: false, status: 500, json: async () => ({}) };
    });
    const badPayloadReq = new Request("http://localhost/api/control/commands", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        kind: "BROADCAST",
        mode: "DRY_RUN",
        account: "main",
        payload: { text: 12345, group_ids: "not-an-array" },
        idempotency_key: IDEMP_UUID,
      }),
    });
    const badPayloadRes = await createCommandRoute(badPayloadReq);
    expect(badPayloadRes.status).toBe(400);

    vi.stubGlobal("fetch", async (url, options = {}) => {
      const href = String(url);
      if (href.includes("tb_accounts")) return { ok: true, json: async () => [{ name: "main" }] };
      if (href.includes("idempotency_key")) return { ok: true, json: async () => [] };
      if (options.method === "PATCH") return { ok: true, json: async () => [] };
      return { ok: false, status: 500, json: async () => ({}) };
    });

    const expiredReq = new Request("http://localhost/api/control/commands", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        kind: "BROADCAST",
        mode: "LIVE",
        account: "main",
        payload: { text: "hello" },
        idempotency_key: IDEMP_UUID,
        confirmation_nonce: NONCE_UUID,
      }),
    });
    const expiredRes = await createCommandRoute(expiredReq);
    expect(expiredRes.status).toBe(409);
  });

  it("translates account alias keys to internal names before enqueue and rejects unknown keys", async () => {
    let storedAccount = "";
    vi.stubGlobal("fetch", async (url, options = {}) => {
      const href = String(url);
      if (href.includes("tb_accounts")) {
        return { ok: true, json: async () => [{ name: "main" }, { name: "work2" }] };
      }
      if (href.includes("idempotency_key")) return { ok: true, json: async () => [] };
      if (options.method === "POST" && href.includes("control_commands")) {
        const rows = JSON.parse(options.body);
        storedAccount = rows[0].account;
        return { ok: true, json: async () => [{ id: CMD_UUID, status: "PENDING" }] };
      }
      return { ok: false, status: 500, json: async () => ({}) };
    });

    const okReq = new Request("http://localhost/api/control/commands", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        kind: "STATUS",
        mode: "READ",
        account: "acc-2",
        payload: {},
        idempotency_key: IDEMP_UUID,
      }),
    });
    const okRes = await createCommandRoute(okReq);
    expect(okRes.status).toBe(200);
    expect(storedAccount).toBe("work2");

    const unknownReq = new Request("http://localhost/api/control/commands", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        kind: "STATUS",
        mode: "READ",
        account: "acc-9",
        payload: {},
        idempotency_key: IDEMP_UUID,
      }),
    });
    const unknownRes = await createCommandRoute(unknownReq);
    expect(unknownRes.status).toBe(400);
  });

  it("handles idempotency_key race on 409 insert and rolls back confirmation on insert failure", async () => {
    let lookupCalls = 0;
    vi.stubGlobal("fetch", async (url, options = {}) => {
      const href = String(url);
      if (href.includes("idempotency_key")) {
        lookupCalls += 1;
        return {
          ok: true,
          json: async () => (lookupCalls === 1 ? [] : [{ id: CMD_UUID, status: "PENDING" }]),
        };
      }
      if (options.method === "POST" && href.includes("control_commands")) {
        return { ok: false, status: 409, json: async () => ({ code: "23505" }) };
      }
      return { ok: false, status: 500, json: async () => ({}) };
    });

    const raced = await enqueueCommand({
      ownerId: 7,
      kind: "STATUS",
      mode: "READ",
      account: "main",
      payload: {},
      idempotencyKey: IDEMP_UUID,
    });
    expect(raced).toEqual({ id: CMD_UUID, status: "PENDING", idempotent: true });

    let rolledBack = false;
    vi.stubGlobal("fetch", async (url, options = {}) => {
      const href = String(url);
      if (href.includes("idempotency_key")) return { ok: true, json: async () => [] };
      if (options.method === "PATCH" && href.includes("control_confirmations")) {
        const body = JSON.parse(options.body);
        if (body.consumed_at === null) rolledBack = true;
        return { ok: true, json: async () => [{ nonce: NONCE_UUID }] };
      }
      if (options.method === "POST" && href.includes("control_commands")) {
        return { ok: false, status: 503, json: async () => ({}) };
      }
      return { ok: false, status: 500, json: async () => ({}) };
    });

    await expect(
      enqueueCommand({
        ownerId: 7,
        kind: "BROADCAST",
        mode: "LIVE",
        account: "main",
        payload: { text: "hi" },
        idempotencyKey: IDEMP_UUID,
        confirmationNonce: NONCE_UUID,
      }),
    ).rejects.toThrow();
    expect(rolledBack).toBe(true);
  });

  it("clamps invalid limit/offset on GET /api/control/commands and validates UUID in getCommand", async () => {
    let seenCommandsUrl = "";
    vi.stubGlobal("fetch", async (url) => {
      const href = String(url);
      if (href.includes("tb_accounts")) return { ok: true, json: async () => [{ name: "main" }] };
      if (href.includes("control_commands")) seenCommandsUrl = href;
      return { ok: true, json: async () => [] };
    });
    const res = await listCommandsRoute(new Request("http://localhost/api/control/commands?limit=abc&offset=-5"));
    expect(res.status).toBe(200);
    expect(seenCommandsUrl).toContain("limit=50");
    expect(seenCommandsUrl).toContain("offset=0");
    const body = await res.json();
    expect(body.commands).toEqual([]);

    await expect(getCommand({ ownerId: 7, commandId: "bad&or=1" })).rejects.toMatchObject({ status: 400 });
  });
});
