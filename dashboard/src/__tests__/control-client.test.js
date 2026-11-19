import { afterEach, describe, expect, it, vi } from "vitest";
import { controlFetch } from "../../lib/control-client";

describe("controlFetch (DASH-601)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("exchanges initData via /api/control/session once on 401 and retries", async () => {
    vi.stubGlobal("window", {
      Telegram: { WebApp: { initData: "auth_date=1&user=%7B%22id%22%3A1%7D&hash=" + "a".repeat(64) } },
      location: { hash: "", search: "" },
      setTimeout,
    });

    const calls = [];
    let bootstrapCount = 0;
    vi.stubGlobal("fetch", async (url, options = {}) => {
      calls.push([String(url), options.method || "GET"]);
      if (url === "/api/control/bootstrap") {
        bootstrapCount += 1;
        if (bootstrapCount === 1) {
          return { ok: false, status: 401, json: async () => ({ ok: false, error: "Unauthenticated" }) };
        }
        return { ok: true, status: 200, json: async () => ({ ok: true, data: { accounts: [] } }) };
      }
      if (url === "/api/control/session") {
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }
      return { ok: false, status: 500, json: async () => ({ ok: false }) };
    });

    const { response, body } = await controlFetch("/api/control/bootstrap");
    expect(response.status).toBe(200);
    expect(body).toEqual({ ok: true, data: { accounts: [] } });
    expect(calls).toEqual([
      ["/api/control/bootstrap", "GET"],
      ["/api/control/session", "POST"],
      ["/api/control/bootstrap", "GET"],
    ]);
  });
});
