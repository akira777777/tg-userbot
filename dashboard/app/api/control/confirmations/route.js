import { requireOwnerSession } from "../../../../lib/control-auth";
import { createConfirmation, requiresConfirmation, validateCommandPayload } from "../../../../lib/control-queue";

export const dynamic = "force-dynamic";

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

  try {
    return Response.json({ ok: true, ...(await createConfirmation({ ownerId, kind, mode, account, payload })) });
  } catch (error) {
    return Response.json({ ok: false, error: "Не удалось подготовить подтверждение." }, { status: 503 });
  }
}
