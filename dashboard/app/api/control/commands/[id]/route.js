import { requireOwnerSession } from "../../../../../lib/control-auth";
import { getCommand, cancelCommand } from "../../../../../lib/control-queue";

export const dynamic = "force-dynamic";

export async function GET(request, { params }) {
  try {
    const { id: ownerId } = await requireOwnerSession();
    const command = await getCommand({ ownerId, commandId: params.id });
    if (!command) return Response.json({ ok: false, error: "команда не найдена" }, { status: 404 });
    return Response.json({ ok: true, command });
  } catch (error) {
    const unauthenticated = error.message === "Unauthenticated";
    return Response.json(
      { ok: false, error: unauthenticated ? "Необходим вход через Telegram." : "ошибка загрузки" },
      { status: unauthenticated ? 401 : 500 }
    );
  }
}

export async function POST(request, { params }) {
  // POST сюда используется для /cancel действия (упрощённый маршрут без вложенного /cancel)
  try {
    const { id: ownerId } = await requireOwnerSession();
    const cancelled = await cancelCommand({ ownerId, commandId: params.id });
    if (!cancelled) {
      return Response.json({ ok: false, error: "команду нельзя отменить (уже выполняется или завершена)" }, { status: 409 });
    }
    return Response.json({ ok: true, command_id: params.id, status: "CANCELLED" });
  } catch (error) {
    const unauthenticated = error.message === "Unauthenticated";
    return Response.json(
      { ok: false, error: unauthenticated ? "Необходим вход через Telegram." : "ошибка отмены" },
      { status: unauthenticated ? 401 : 500 }
    );
  }
}
