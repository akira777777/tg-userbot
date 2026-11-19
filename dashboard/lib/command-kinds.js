import { delayPair } from "./command-options";

export const READ_KINDS = new Set(["STATUS", "GROUPS", "CAMPAIGNS", "MEDIA_LIST"]);
/** Команды, которые физически что-то отправляют в Telegram. */
export const DANGEROUS_KINDS = new Set(["BROADCAST", "DMSEND", "CONDUCT"]);
export const DELIVERY_KINDS = new Set(["BROADCAST", "DMSEND", "CONDUCT"]);

export const COMMANDS = [
  { kind: "STATUS", label: "Статус", hint: "Снимок аккаунтов и лимитов без изменений." },
  { kind: "GROUPS", label: "Группы", hint: "Список групп, которые видит воркер." },
  { kind: "CAMPAIGNS", label: "Кампании", hint: "Последние запуски рассылок." },
  { kind: "MEDIA_LIST", label: "Медиа", hint: "Доступные вложения для рассылки." },
  { kind: "BROADCAST", label: "Рассылка в группы", hint: "Текст в выбранные чаты. По умолчанию dry-run." },
  { kind: "DMSEND", label: "Личные сообщения", hint: "Ограниченная DM-отправка. По умолчанию dry-run." },
  { kind: "CONDUCT", label: "Провести кампанию", hint: "Группы, личные или оба канала сразу. Режим «оба» идёт до остановки." },
  { kind: "BLACKLIST_ADD", label: "В чёрный список", hint: "Исключить ID или ник из рассылок." },
  { kind: "STOP", label: "Остановить", hint: "Остановить отправку выбранного аккаунта или всех." },
];

export const STATUS_LABELS = {
  PENDING: "В очереди",
  QUEUED: "В очереди",
  CLAIMED: "Выполняется",
  RUNNING: "Выполняется",
  SUCCEEDED: "Готово",
  FAILED: "Ошибка",
  CANCELLED: "Отменена",
  EXPIRED: "Истекла",
};

export const MODE_LABELS = {
  READ: "чтение",
  DRY_RUN: "dry-run",
  LIVE: "live",
};

function groupIdValue(value) {
  if (value === "" || value == null) return null;
  const id = Number(value);
  if (!Number.isInteger(id) || id === 0) return null;
  return id;
}

export function payloadFor(kind, {
  text = "",
  delayMin = 20,
  delayMax = 45,
  limit = 20,
  account = "all",
  conductKind = "broadcast",
  groupId = "",
} = {}) {
  if (READ_KINDS.has(kind)) return {};
  if (kind === "BLACKLIST_ADD") return { value: text.trim(), reason: "mini-app" };
  if (kind === "STOP") return { account: account || "all" };
  const trimmed = text.trim();
  const target = groupIdValue(groupId);
  if (kind === "CONDUCT") {
    const requested = conductKind === "dm" || conductKind === "both" ? conductKind : "broadcast";
    // Режим "channel" (каналы) уходит воркеру как обычная рассылка по группам.
    const mode = requested === "channel" ? "broadcast" : requested;
    const payload = { text: trimmed, kind: mode };
    if (target && (mode === "dm" || mode === "both")) payload.group_id = target;
    if (target && (mode === "broadcast" || mode === "both")) payload.group_ids = [target];
    if (mode === "dm" || mode === "both") {
      const parsed = Number(limit);
      if (mode === "dm") {
        payload.limit = Number.isFinite(parsed) ? Math.max(1, Math.min(200, parsed)) : 20;
      } else if (Number.isFinite(parsed) && parsed > 0) {
        payload.limit = Math.max(1, Math.min(10000, parsed));
      }
    }
    return payload;
  }
  const payload = { text: trimmed, ...delayPair(delayMin, delayMax) };
  if (kind === "DMSEND") {
    const parsed = Number(limit);
    payload.limit = Number.isFinite(parsed) ? Math.max(1, Math.min(50, parsed)) : 20;
    if (target) payload.group_id = target;
  }
  if (kind === "BROADCAST" && target) payload.group_ids = [target];
  return payload;
}

/** Совместимость с HTTP-ошибками: текст ошибки очереди бывает по-английски. */
export function errorTextFor(reason) {
  if (!reason) return "";
  const message = reason instanceof Error ? reason.message : String(reason);
  if (/^Unauthenticated$/.test(message) || /Необходим вход/.test(message)) {
    return "Сессия истекла — переоткройте панель из Telegram.";
  }
  if (/Confirmation is invalid or expired/i.test(message)) {
    return "Подтверждение устарело (даётся 5 минут). Нажмите «Запустить» ещё раз.";
  }
  if (/Could not verify confirmation/i.test(message)) {
    return "Не удалось проверить подтверждение. Повторите запуск.";
  }
  return message;
}

export function commandLabel(kind) {
  return COMMANDS.find((item) => item.kind === kind)?.label ?? kind;
}
