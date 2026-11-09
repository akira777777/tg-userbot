import crypto from "node:crypto";
import { describe, expect, it, beforeEach } from "vitest";

import { verifyTelegramInitData } from "../../lib/control-auth";

const NOW = 1_700_000_000;

function signedInitData(fields = {}) {
  const values = new URLSearchParams({
    auth_date: String(NOW),
    query_id: "test-query",
    user: JSON.stringify({ id: 123, first_name: "Owner" }),
    ...fields,
  });
  const check = [...values.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => `${key}=${value}`).join("\n");
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

  it("accepts a valid signed Telegram identity", () => {
    expect(verifyTelegramInitData(signedInitData(), NOW)).toEqual({ id: 123, firstName: "Owner" });
  });

  it("accepts a signed identity that is not in OWNER_TELEGRAM_IDS", () => {
    expect(verifyTelegramInitData(signedInitData({ user: JSON.stringify({ id: 999, first_name: "Guest" }) }), NOW)).toEqual({
      id: 999,
      firstName: "Guest",
    });
  });

  it("rejects an altered payload", () => {
    expect(() => verifyTelegramInitData(signedInitData().replace("Owner", "Intruder"), NOW)).toThrow("Invalid Telegram authentication data");
  });

  it("rejects expired authentication", () => {
    expect(() => verifyTelegramInitData(signedInitData({ auth_date: String(NOW - 301) }), NOW)).toThrow("Telegram authentication has expired");
  });
});
