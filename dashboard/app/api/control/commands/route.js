import { requireOwnerSession } from "../../../../lib/control-auth";
import { enqueueCommand, listCommands, requiresConfirmation } from "../../../../lib/control-queue";

export const dynamic = "force-dynamic";

const VALID_KINDS = new Set([
  "STATUS", "GROUPS", "CAMPAIGNS", "MEDIA_LIST", "BLACKLIST_ADD",
  "BROADCAST", "DMSEND", "CONDUCT", "STOP",
]);

export async function GET(request) {
  try {
    const { id: ownerId } = await requireOwnerSession();
    const { searchParams } = new URL(request.url);
    const limit = Math.min(parseInt(searchParams.get("limit") || "50", 10), 100);
    const offset = parseInt(searchParams.get("offset") || "0", 10);
    const commands = await listCommands({ ownerId, limit, offset });
    return Response.json({ ok: true, commands });
  } catch (error) {
    const unauthenticated = error.message === "Unauthenticated";
    return Response.json(
      { ok: false, error: unauthenticated ? "Необходим вход через Telegram." : "Не удалось загрузить команды." },
      { status: unauthenticated ? 401 : 500 }
    );
  }
}

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

  const { kind, mode, account, payload, idempotency_key: idempotencyKey, confirmation_nonce: confirmationNonce } = body || {};

  if (!kind || !VALID_KINDS.has(kind) || !["READ", "DRY_RUN", "LIVE"].includes(mode) || !idempotencyKey) {
    return Response.json({ ok: false, error: "некорректные поля команды" }, { status: 400 });
  }

  if (requiresConfirmation(kind, mode) && !confirmationNonce) {
    return Response.json({ ok: false, error: "LIVE режим требует confirmation_nonce" }, { status: 400 });
  }

  try {
    const result = await enqueueCommand({ ownerId, kind, mode, account, payload, idempotencyKey, confirmationNonce });
    return Response.json({ ok: true, command_id: result.id, status: result.status, idempotent: result.idempotent });
  } catch (error) {
    return Response.json({ ok: false, error: error.message || "не удалось поставить команду" }, { status: 500 });
  }
}
