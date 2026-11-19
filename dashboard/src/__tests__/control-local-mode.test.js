import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/control-auth", () => ({
  isLocalMode: vi.fn(() => process.env.CONTROL_LOCAL_MODE === "1"),
  requireOwnerSession: vi.fn(async () => ({ id: 1 })),
  createSession: vi.fn(async () => {}),
  verifyTelegramInitData: vi.fn(() => {
    throw new Error("Missing Telegram authentication data");
  }),
}));

import { POST as postSession } from "../../app/api/control/session/route";
import { POST as postCommand } from "../../app/api/control/commands/route";
import { POST as postConfirmation } from "../../app/api/control/confirmations/route";

const IDEMP_UUID = "44444444-4444-4444-8444-444444444444";

describe("local mode routes (CONTROL_LOCAL_MODE=1)", () => {
  afterEach(() => {
    delete process.env.CONTROL_LOCAL_MODE;
    delete process.env.SUPABASE_URL;
    vi.clearAllMocks();
  });

  it("сессия открывается без initData в локальном режиме", async () => {
    process.env.CONTROL_LOCAL_MODE = "1";
    const res = await postSession(new Request("http://localhost/api/control/session", { method: "POST" }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, owner: { firstName: "local" } });
  });

  it("без облака постановка команды отвечает 503 с понятным текстом", async () => {
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
    expect(res.status).toBe(503);
    expect((await res.json()).error).toContain("режиме просмотра");
  });

  it("без облака подтверждение LIVE отвечает 503", async () => {
    process.env.CONTROL_LOCAL_MODE = "1";
    const res = await postConfirmation(new Request("http://localhost/api/control/confirmations", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind: "BROADCAST", mode: "LIVE", account: "main", payload: { text: "hi" } }),
    }));
    expect(res.status).toBe(503);
  });
});
