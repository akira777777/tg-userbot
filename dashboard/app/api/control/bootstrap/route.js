import { requireOwnerSession } from "../../../../lib/control-auth";
import { loadControlSnapshot } from "../../../../lib/control-data";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    await requireOwnerSession();
    return Response.json({ ok: true, data: await loadControlSnapshot() }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const unauthenticated = error.message === "Unauthenticated";
    return Response.json({ ok: false, error: unauthenticated ? "Необходим вход через Telegram." : "Зеркало данных недоступно." }, { status: unauthenticated ? 401 : 503 });
  }
}
