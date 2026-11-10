import "server-only";

import crypto from "node:crypto";
import { cookies } from "next/headers";

const SESSION_COOKIE = "tg_control_session";
const MAX_INIT_DATA_AGE_SECONDS = 300;
const SESSION_TTL_SECONDS = 30 * 60;

function required(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is not configured`);
  return value;
}

function ownerIds() {
  const values = required("OWNER_TELEGRAM_IDS").split(",").map((value) => Number.parseInt(value.trim(), 10));
  if (!values.length || values.some((value) => !Number.isSafeInteger(value) || value <= 0)) throw new Error("OWNER_TELEGRAM_IDS is invalid");
  return new Set(values);
}

function hmac(key, value) { return crypto.createHmac("sha256", key).update(value).digest(); }
export function sessionSecret() {
  const value = required("CONTROL_SESSION_SECRET");
  if (value.length < 32 || value === "change-me-in-production") throw new Error("CONTROL_SESSION_SECRET is unsafe");
  return value;
}
function sign(value) { return crypto.createHmac("sha256", sessionSecret()).update(value).digest("base64url"); }

export function verifyTelegramInitData(initData, nowSeconds = Math.floor(Date.now() / 1000)) {
  if (typeof initData !== "string" || !initData) throw new Error("Missing Telegram authentication data");
  const parameters = new URLSearchParams(initData);
  const hash = parameters.get("hash");
  if (!hash || parameters.getAll("hash").length !== 1) throw new Error("Invalid Telegram authentication data");
  const fields = [];
  for (const [key, value] of parameters.entries()) if (key !== "hash") fields.push([key, value]);
  if (new Set(fields.map(([key]) => key)).size !== fields.length) throw new Error("Invalid Telegram authentication data");
  const check = fields.sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => `${key}=${value}`).join("\n");
  const expected = hmac(hmac("WebAppData", required("BOT_TOKEN")), check).toString("hex");
  const actualBuffer = Buffer.from(hash, "hex");
  const expectedBuffer = Buffer.from(expected, "hex");
  if (actualBuffer.length !== expectedBuffer.length || !crypto.timingSafeEqual(actualBuffer, expectedBuffer)) throw new Error("Invalid Telegram authentication data");
  const authDate = Number.parseInt(parameters.get("auth_date") ?? "", 10);
  if (!Number.isSafeInteger(authDate) || authDate > nowSeconds + 30 || nowSeconds - authDate > MAX_INIT_DATA_AGE_SECONDS) throw new Error("Telegram authentication has expired");
  let user;
  try { user = JSON.parse(parameters.get("user") ?? ""); } catch { throw new Error("Invalid Telegram user"); }
  if (!Number.isSafeInteger(user?.id) || user.id <= 0 || !ownerIds().has(user.id)) throw new Error("Access denied");
  return { id: user.id, firstName: typeof user.first_name === "string" ? user.first_name : null };
}

export async function createSession(identity) {
  const expiresAt = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const value = `${identity.id}.${expiresAt}`;
  (await cookies()).set(SESSION_COOKIE, `${value}.${sign(value)}`, { httpOnly: true, secure: true, sameSite: "lax", maxAge: SESSION_TTL_SECONDS, path: "/" });
}

function publicActorId() {
  const raw = process.env.OWNER_TELEGRAM_IDS?.split(",")[0]?.trim();
  const id = Number.parseInt(raw ?? "", 10);
  return Number.isSafeInteger(id) && id > 0 ? id : 1;
}

export async function requireOwnerSession() {
  return { id: publicActorId() };
}
