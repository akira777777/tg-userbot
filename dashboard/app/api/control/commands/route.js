import crypto from "node:crypto";

import { requireOwnerSession, isLocalMode } from "../../../../lib/control-auth";
import { displayNameFor, toInternalAccount } from "../../../../lib/account-aliases";
import {
  commandRejection,
  digestCommandPayload,
  enqueueCommand,
  isUuid,
  listCommands,
  loadAccountAliasMap,
  requiresConfirmation,
  validateAccount,
  validateCommandPayload,
} from "../../../../lib/control-queue";
import {
  isLocalQueueMode,
  localConsumeConfirmation,
  localEnqueueCommand,
  localFinishCommand,
  localListCommands,
  localMarkRunning,
} from "../../../../lib/local-queue";
import { localRunCommand as spawnLocalCommand, localCancelRun } from "../../../../lib/local-executor";

export const dynamic = "force-dynamic";

const VALID_KINDS = new Set([
  "STATUS", "GROUPS", "CAMPAIGNS", "MEDIA_LIST", "BLACKLIST_ADD",
  "BROADCAST", "DMSEND", "CONDUCT", "STOP",
]);

function parseBoundedInt(raw, fallback, min, max) {
  if (raw === null || raw === "") return fallback;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isSafeInteger(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

export async function GET(request) {
  try {
    const { id: ownerId } = await requireOwnerSession();
    const { searchParams } = new URL(request.url);
    const limit = parseBoundedInt(searchParams.get("limit"), 50, 1, 100);
    const offset = parseBoundedInt(searchParams.get("offset"), 0, 0, 10_000);
    if (isLocalQueueMode()) {
      const commands = localListCommands({ limit, offset });
      const aliases = await loadAccountAliasMap().catch(() => null);
      const safe = commands.map((command) => {
        const { account: _account, ...rest } = command;
        return { ...rest, account_display: aliases ? displayNameFor(command.account, aliases) : null };
      });
      return Response.json({ ok: true, commands: safe });
    }
    const commands = await listCommands({ ownerId, limit, offset });
    // Внутренние имена аккаунтов не уходят в браузер: только псевдоним.
    let aliases = null;
    try {
      aliases = await loadAccountAliasMap();
    } catch {
      aliases = null;
    }
    const safe = commands.map((command) => {
      const { account: _account, ...rest } = command;
      return {
        ...rest,
        account_display: aliases ? displayNameFor(command.account, aliases) : null,
      };
    });
    return Response.json({ ok: true, commands: safe });
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
  } catch {
    return Response.json({ ok: false, error: "Необходим вход через Telegram." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: "неверный JSON" }, { status: 400 });
  }

  const { kind, mode, account, payload, idempotency_key: idempotencyKey, confirmation_nonce: confirmationNonce } = body || {};

  if (!kind || !VALID_KINDS.has(kind) || !["READ", "DRY_RUN", "LIVE"].includes(mode) || !isUuid(idempotencyKey)) {
    return Response.json({ ok: false, error: "некорректные поля команды" }, { status: 400 });
  }

  const localMode = isLocalQueueMode();

  // Псевдоним аккаунта (acc-N) -> внутреннее имя; неизвестное значение отклоняем.
  let internalAccount = account || "main";
  let internalPayload = payload;
  try {
    const aliases = await loadAccountAliasMap();
    internalAccount = toInternalAccount(account, aliases);
    if (internalAccount === null) {
      return Response.json({ ok: false, error: "некорректное имя аккаунта" }, { status: 400 });
    }
    if (kind === "STOP" && isPlainObject(payload) && payload.account != null) {
      const stopAccount = toInternalAccount(payload.account, aliases);
      if (stopAccount === null) {
        return Response.json({ ok: false, error: "некорректное имя аккаунта" }, { status: 400 });
      }
      internalPayload = { ...payload, account: stopAccount };
    }
  } catch {
    if (localMode) {
      // Локальный режим без зеркала: пустая карта — принимаем внутреннее имя как есть.
      if (typeof internalAccount !== "string" || !/^[a-z0-9_\-]{1,32}$/i.test(internalAccount)) {
        return Response.json({ ok: false, error: "некорректное имя аккаунта" }, { status: 400 });
      }
    } else {
      return Response.json({ ok: false, error: "Зеркало данных недоступно." }, { status: 503 });
    }
  }

  const rejected = commandRejection(kind, mode);
  if (rejected) return Response.json({ ok: false, error: rejected }, { status: 400 });

  const accountError = validateAccount(internalAccount);
  if (accountError) return Response.json({ ok: false, error: accountError }, { status: 400 });

  const validationError = validateCommandPayload(kind, internalPayload);
  if (validationError) return Response.json({ ok: false, error: validationError }, { status: 400 });

  if (localMode) {
    return handleLocalCommand({ kind, mode, account: internalAccount, payload: internalPayload, idempotencyKey, confirmationNonce });
  }

  if (requiresConfirmation(kind, mode) && !isUuid(confirmationNonce)) {
    return Response.json({ ok: false, error: "LIVE режим требует confirmation_nonce" }, { status: 400 });
  }

  try {
    const result = await enqueueCommand({ ownerId, kind, mode, account: internalAccount, payload: internalPayload, idempotencyKey, confirmationNonce });
    return Response.json({ ok: true, command_id: result.id, status: result.status, idempotent: result.idempotent });
  } catch (error) {
    const status = Number.isInteger(error?.status) ? error.status : 500;
    return Response.json({ ok: false, error: error.message || "не удалось поставить команду" }, { status });
  }
}

async function handleLocalCommand({ kind, mode, account, payload, idempotencyKey, confirmationNonce }) {
  if (requiresConfirmation(kind, mode)) {
    if (!isUuid(confirmationNonce)) {
      return Response.json({ ok: false, error: "LIVE режим требует confirmation_nonce" }, { status: 400 });
    }
    const digest = digestCommandPayload(kind, mode, account, payload);
    if (!localConsumeConfirmation({ nonce: confirmationNonce, requestDigest: digest })) {
      return Response.json({ ok: false, error: "Подтверждение устарело (даётся 5 минут). Нажмите «Запустить» ещё раз." }, { status: 409 });
    }
  }

  const id = crypto.randomUUID();
  const enqueued = localEnqueueCommand({ id, idempotencyKey, kind, mode, account, payload });
  if (enqueued.idempotent) {
    return Response.json({ ok: true, command_id: enqueued.id, status: enqueued.status, idempotent: true });
  }

  localMarkRunning(id);
  // Выполнение асинхронное: ответ отдаём сразу, результат появится в таймлайне.
  spawnLocalCommand({ id, kind, mode, account, payload })
    .then((result) => {
      localFinishCommand(id, { ok: result.ok, output: result.output, errorCode: result.errorCode });
    })
    .catch((error) => {
      localFinishCommand(id, { ok: false, output: String(error?.message || error), errorCode: "failed" });
    });

  return Response.json({ ok: true, command_id: id, status: "RUNNING", idempotent: false });
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
