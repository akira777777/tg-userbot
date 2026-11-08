import crypto from "node:crypto";
import { requireOwnerSession } from "../../../../lib/control-auth";
import { requiresConfirmation, validateCommandPayload, buildCanonicalSummary } from "../../../../lib/control-queue";

export const dynamic = "force-dynamic";

const CONFIRMATION_TTL_MS = 5 * 60 * 1000;

export async function POST(request) {
  let ownerId;
  try {
    ({ id: ownerId } = await requireOwnerSession());
  } catch (error) {
    return Response.json({ ok: false, error: "Необходим вход через Telegram." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch (error) {
    return Response.json({ ok: false, error: "неверный JSON" }, { status: 400 });
  }

  const { kind, mode, account, payload } = body || {};

  if (!kind || !mode || !requiresConfirmation(kind, mode)) {
    return Response.json({ ok: false, error: "подтверждение требуется только для LIVE-команд" }, { status: 400 });
  }

  const validationError = validateCommandPayload(kind, payload);
  if (validationError) {
    return Response.json({ ok: false, error: validationError }, { status: 400 });
  }

  const canonicalSummary = buildCanonicalSummary(kind, payload);
  const requestDigest = crypto
    .createHash("sha256")
    .update(JSON.stringify({ kind, mode, account, payload }))
    .digest("hex");
  const nonce = crypto.randomUUID();
  const expiresAt = new Date(Date.now() + CONFIRMATION_TTL_MS).toISOString();

  return Response.json({
    ok: true,
    nonce,
    request_digest: requestDigest,
    expires_at: expiresAt,
    canonical_summary: canonicalSummary,
    owner_telegram_id: ownerId,
  });
}
