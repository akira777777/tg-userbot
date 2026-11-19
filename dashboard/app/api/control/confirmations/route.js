import { requireOwnerSession } from "../../../../lib/control-auth";
import { toInternalAccount } from "../../../../lib/account-aliases";
import {
  buildCanonicalSummary,
  createConfirmation,
  digestCommandPayload,
  loadAccountAliasMap,
  requiresConfirmation,
  validateAccount,
  validateCommandPayload,
} from "../../../../lib/control-queue";
import { isLocalQueueMode, localCreateConfirmation } from "../../../../lib/local-queue";

export const dynamic = "force-dynamic";

export async function POST(request) {
  let ownerId;
  try {
    ({ id: ownerId } = await requireOwnerSession());
  } catch {
    return Response.json({ ok: false, error: "Необходим вход через Telegram." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: "неверный JSON" }, { status: 400 });
  }

  const { kind, mode, account, payload } = body || {};

  if (!kind || !mode || !requiresConfirmation(kind, mode)) {
    return Response.json({ ok: false, error: "подтверждение требуется только для LIVE-команд" }, { status: 400 });
  }

  const localMode = isLocalQueueMode();

  let internalAccount;
  try {
    internalAccount = toInternalAccount(account, await loadAccountAliasMap());
  } catch {
    if (localMode && typeof account === "string" && /^[a-z0-9_\-]{1,32}$/i.test(account)) {
      internalAccount = account === "all" ? "all" : account;
    } else {
      return Response.json({ ok: false, error: "Зеркало данных недоступно." }, { status: 503 });
    }
  }
  if (internalAccount === null) {
    return Response.json({ ok: false, error: "некорректное имя аккаунта" }, { status: 400 });
  }

  const accountError = validateAccount(internalAccount);
  if (accountError) {
    return Response.json({ ok: false, error: accountError }, { status: 400 });
  }

  const validationError = validateCommandPayload(kind, payload);
  if (validationError) {
    return Response.json({ ok: false, error: validationError }, { status: 400 });
  }

  if (localMode) {
    const requestDigest = digestCommandPayload(kind, mode, internalAccount, payload);
    const { nonce, expires_at } = localCreateConfirmation({ requestDigest });
    return Response.json({
      ok: true,
      nonce,
      request_digest: requestDigest,
      expires_at,
      canonical_summary: buildCanonicalSummary(kind, payload),
    });
  }

  try {
    return Response.json({ ok: true, ...(await createConfirmation({ ownerId, kind, mode, account: internalAccount, payload })) });
  } catch (error) {
    const status = Number.isInteger(error?.status) ? error.status : 503;
    return Response.json({ ok: false, error: status < 500 ? error.message : "Не удалось подготовить подтверждение." }, { status });
  }
}
