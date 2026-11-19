import crypto from "node:crypto";
import { describe, expect, it, beforeEach, afterEach } from "vitest";

import { isLocalMode, requireOwnerSession, sessionCookieOptions, verifyTelegramInitData } from "../../lib/control-auth";

const NOW = 1_700_000_000;

function signedInitData(fields = {}) {
  const values = new URLSearchParams({
    auth_date: String(NOW),
    query_id: "test-query",
    user: JSON.stringify({ id: 123, first_name: "Owner" }),
    ...fields,
  });
  const check = [...values.entries()]
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  const secret = crypto.createHmac("sha256", "WebAppData").update(process.env.BOT_TOKEN).digest();
  values.set("hash", crypto.createHmac("sha256", secret).update(check).digest("hex"));
  return values.toString();
}

describe("verifyTelegramInitData", () => {
  beforeEach(() => {
    process.env.BOT_TOKEN = "test-token";
    process.env.OWNER_TELEGRAM_IDS = "123";
    process.env.CONTROL_SESSION_SECRET = "test-session-secret";
  });

  it("accepts a valid signed owner identity", () => {
    expect(verifyTelegramInitData(signedInitData(), NOW)).toEqual({ id: 123, firstName: "Owner" });
  });

  it("sorts keys using ASCII code-point order (Z before a)", () => {
    expect(verifyTelegramInitData(signedInitData({ Z_field: "1", a_field: "2" }), NOW)).toEqual({
      id: 123,
      firstName: "Owner",
    });
  });

  it("rejects non-64-char hex hashes strictly", () => {
    const valid = new URLSearchParams(signedInitData());
    valid.set("hash", "deadbeef");
    expect(() => verifyTelegramInitData(valid.toString(), NOW)).toThrow("Invalid Telegram authentication data");
    valid.set("hash", "G".repeat(64));
    expect(() => verifyTelegramInitData(valid.toString(), NOW)).toThrow("Invalid Telegram authentication data");
  });

  it("configures iframe-safe SameSite=None; Secure cookies in production and HTTP-safe cookies in dev", () => {
    expect(sessionCookieOptions(true)).toMatchObject({ httpOnly: true, secure: true, sameSite: "none" });
    expect(sessionCookieOptions(false)).toMatchObject({ httpOnly: true, secure: false, sameSite: "lax" });
  });

  it("rejects an altered payload", () => {
    expect(() => verifyTelegramInitData(signedInitData().replace("Owner", "Intruder"), NOW)).toThrow("Invalid Telegram authentication data");
  });

  it("rejects expired authentication", () => {
    expect(() => verifyTelegramInitData(signedInitData({ auth_date: String(NOW - 301) }), NOW)).toThrow("Telegram authentication has expired");
  });
});

describe("local mode (CONTROL_LOCAL_MODE=1)", () => {
  afterEach(() => {
    delete process.env.CONTROL_LOCAL_MODE;
    delete process.env.LOCAL_OWNER_ID;
  });

  it("isLocalMode читает флаг", () => {
    expect(isLocalMode()).toBe(false);
    process.env.CONTROL_LOCAL_MODE = "1";
    expect(isLocalMode()).toBe(true);
  });

  it("requireOwnerSession выдаёт локального владельца без cookie", async () => {
    process.env.CONTROL_LOCAL_MODE = "1";
    expect(await requireOwnerSession()).toEqual({ id: 1 });
    process.env.LOCAL_OWNER_ID = "42";
    expect(await requireOwnerSession()).toEqual({ id: 42 });
  });

  it("в production локальный режим не выдаёт сессию", async () => {
    process.env.CONTROL_LOCAL_MODE = "1";
    const previous = process.env.NODE_ENV;
    process.env.NODE_ENV = "production";
    try {
      await expect(requireOwnerSession()).rejects.toThrow("Unauthenticated");
    } finally {
      process.env.NODE_ENV = previous;
    }
  });
});
