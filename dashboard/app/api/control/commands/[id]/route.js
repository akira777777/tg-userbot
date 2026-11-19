import { requireOwnerSession } from "../../../../../lib/control-auth";
import { displayNameFor } from "../../../../../lib/account-aliases";
import { cancelCommand, getCommand, isUuid, loadAccountAliasMap } from "../../../../../lib/control-queue";
import { isLocalQueueMode, localCancelCommand, localGetCommand, localListEvents } from "../../../../../lib/local-queue";
import { localCancelRun } from "../../../../../lib/local-executor";

export const dynamic = "force-dynamic";

export async function GET(request, { params }) {
  try {
    await requireOwnerSession();
    const { id } = await params;
    if (!isUuid(id)) {
      return Response.json({ ok: false, error: "некорректный идентификатор команды" }, { status: 400 });
    }
    if (isLocalQueueMode()) {
      const command = localGetCommand(id);
      if (!command) return Response.json({ ok: false, error: "команда не найдена" }, { status: 404 });
      let aliases = null;
      try {
        aliases = await loadAccountAliasMap();
      } catch {
        aliases = null;
      }
      const { account: _account, ...rest } = command;
      return Response.json({
        ok: true,
        command: { ...rest, account_display: aliases ? displayNameFor(command.account, aliases) : null },
        events: localListEvents(id),
      });
    }
    const command = await getCommand({ ownerId: (await requireOwnerSession()).id, commandId: id });
    if (!command) return Response.json({ ok: false, error: "команда не найдена" }, { status: 404 });
    let aliases = null;
    try {
      aliases = await loadAccountAliasMap();
    } catch {
      aliases = null;
    }
    const { account: _account, ...rest } = command;
    const safe = { ...rest, account_display: aliases ? displayNameFor(command.account, aliases) : null };
    return Response.json({ ok: true, command: safe });
  } catch (error) {
    const unauthenticated = error.message === "Unauthenticated";
    const status = unauthenticated ? 401 : (Number.isInteger(error?.status) ? error.status : 500);
    return Response.json(
      { ok: false, error: unauthenticated ? "Необходим вход через Telegram." : (error.message || "ошибка загрузки") },
      { status }
    );
  }
}

export async function POST(request, { params }) {
  // POST сюда используется для /cancel действия (упрощённый маршрут без вложенного /cancel)
  try {
    await requireOwnerSession();
    const { id } = await params;
    if (!isUuid(id)) {
      return Response.json({ ok: false, error: "некорректный идентификатор команды" }, { status: 400 });
    }
    if (isLocalQueueMode()) {
      localCancelRun(id); // если процесс ещё бежит — убиваем
      const cancelled = localCancelCommand(id);
      if (!cancelled) {
        return Response.json({ ok: false, error: "команда не найдена" }, { status: 404 });
      }
      if (cancelled.status !== "CANCELLED") {
        return Response.json({ ok: false, error: "команду нельзя отменить (уже завершена)" }, { status: 409 });
      }
      return Response.json({ ok: true, command_id: id, status: "CANCELLED" });
    }
    const { id: ownerId } = await requireOwnerSession();
    const cancelled = await cancelCommand({ ownerId, commandId: id });
    if (!cancelled) {
      return Response.json({ ok: false, error: "команду нельзя отменить (уже выполняется или завершена)" }, { status: 409 });
    }
    return Response.json({ ok: true, command_id: id, status: "CANCELLED" });
  } catch (error) {
    const unauthenticated = error.message === "Unauthenticated";
    const status = unauthenticated ? 401 : (Number.isInteger(error?.status) ? error.status : 500);
    return Response.json(
      { ok: false, error: unauthenticated ? "Необходим вход через Telegram." : (error.message || "ошибка отмены") },
      { status }
    );
  }
}
