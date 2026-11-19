import { createSession, isLocalMode, verifyTelegramInitData } from "../../../../lib/control-auth";

export const dynamic = "force-dynamic";

export async function POST(request) {
  if (isLocalMode()) {
    // Локальная веб-страница без Telegram-входа.
    return Response.json({ ok: true, owner: { firstName: "local" } });
  }
  try {
    const identity = verifyTelegramInitData((await request.json())?.initData);
    await createSession(identity);
    return Response.json({ ok: true, owner: { firstName: identity.firstName } });
  } catch (error) {
    return Response.json({ ok: false, error: "Не удалось подтвердить доступ Telegram." }, { status: error.message === "Access denied" ? 403 : 401 });
  }
}
