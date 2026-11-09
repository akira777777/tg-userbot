import { createSession, verifyTelegramInitData } from "../../../../lib/control-auth";

export const dynamic = "force-dynamic";

export async function POST(request) {
  try {
    const body = await request.json();
    if (process.env.NODE_ENV !== "production" && body?.local) {
      const identity = { id: 1, firstName: "Local" };
      await createSession(identity);
      return Response.json({ ok: true, owner: { firstName: identity.firstName } });
    }
    const identity = verifyTelegramInitData(body?.initData);
    await createSession(identity);
    return Response.json({ ok: true, owner: { firstName: identity.firstName } });
  } catch (error) {
    return Response.json({ ok: false, error: "Не удалось подтвердить доступ Telegram." }, { status: error.message === "Access denied" ? 403 : 401 });
  }
}
