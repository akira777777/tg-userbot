import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionSecret } from "../../lib/control-auth";
import { commandRejection, enqueueCommand, listCommands } from "../../lib/control-queue";

describe("command contract", () => {
  it("rejects unsupported commands before enqueue", () => {
    expect(commandRejection("DELETE_ALL", "LIVE")).toBe("неподдерживаемая команда");
    expect(commandRejection("BROADCAST", "READ")).toMatch(/READ/);
    expect(commandRejection("BROADCAST", "DRY_RUN")).toBeNull();
    expect(commandRejection("STATUS", "READ")).toBeNull();
  });

  it("rejects a weak control session secret", () => {
    process.env.CONTROL_SESSION_SECRET = "change-me-in-production";
    expect(() => sessionSecret()).toThrow(/unsafe/);
  });
});

describe("command queue persistence", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.SUPABASE_URL;
    delete process.env.SUPABASE_SECRET_KEY;
  });

  it("stores the confirmation nonce on the queued command", async () => {
    process.env.SUPABASE_URL = "https://example.supabase.co";
    process.env.SUPABASE_SECRET_KEY = "test-key";
    const bodies = [];
    vi.stubGlobal("fetch", async (url, options = {}) => {
      const href = String(url);
      if (href.includes("idempotency_key")) return { ok: true, json: async () => [] };
      if (options.method === "PATCH") return { ok: true, json: async () => [{ nonce: "n" }] };
      if (options.method === "POST" && href.includes("control_commands")) {
        bodies.push(JSON.parse(options.body));
        return { ok: true, json: async () => [{ id: "cmd-1", status: "PENDING" }] };
      }
      return { ok: false, json: async () => ({}) };
    });

    const nonce = "11111111-1111-4111-8111-111111111111";
    const result = await enqueueCommand({
      ownerId: 1,
      kind: "BROADCAST",
      mode: "LIVE",
      account: "main",
      payload: { text: "hi" },
      idempotencyKey: "22222222-2222-4222-8222-222222222222",
      confirmationNonce: nonce,
    });

    expect(result).toMatchObject({ id: "cmd-1", status: "PENDING" });
    expect(bodies[0][0].confirmation_nonce).toBe(nonce);
  });

  it("lists owner-safe command columns", async () => {
    process.env.SUPABASE_URL = "https://example.supabase.co";
    process.env.SUPABASE_SECRET_KEY = "test-key";
    let seen = "";
    vi.stubGlobal("fetch", async (url) => {
      seen = String(url);
      return { ok: true, json: async () => [] };
    });
    await listCommands({ ownerId: 7, limit: 20, offset: 0 });
    expect(seen).toContain("select=id,kind,mode,account,status,created_at,claimed_at,finished_at,error_code,result_summary");
    expect(seen).not.toContain("confirmation_nonce");
    expect(seen).not.toContain("payload");
  });
});
