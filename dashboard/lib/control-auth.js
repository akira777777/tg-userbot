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

function hmac(key, value) { return crypto.createHmac("sha256", key).update(value).digest(); }
function sign(value) { return crypto.createHmac("sha256", required("CONTROL_SESSION_SECRET")).update(value).digest("base64url"); }

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
  if (!Number.isSafeInteger(user?.id) || user.id <= 0) throw new Error("Access denied");
  return { id: user.id, firstName: typeof user.first_name === "string" ? user.first_name : null };
}

export async function createSession(identity) {
  const expiresAt = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const value = `${identity.id}.${expiresAt}`;
  (await cookies()).set(SESSION_COOKIE, `${value}.${sign(value)}`, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: SESSION_TTL_SECONDS,
    path: "/",
  });
}

export async function requireOwnerSession() {
  const raw = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!raw) throw new Error("Unauthenticated");
  const [idText, expiresText, signature, ...rest] = raw.split(".");
  const id = Number.parseInt(idText, 10); const expiresAt = Number.parseInt(expiresText, 10); const value = `${idText}.${expiresText}`;
  if (rest.length || !Number.isSafeInteger(id) || !Number.isSafeInteger(expiresAt) || expiresAt <= Date.now() / 1000) throw new Error("Unauthenticated");
  const expected = sign(value);
  if (signature.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) throw new Error("Unauthenticated");
  return { id };
}
