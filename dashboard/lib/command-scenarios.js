/**
 * Сценарии панели: сформулированы действием пользователя, а не кодом команды.
 * Режимы кампании и вспомогательный текст тоже здесь — чтобы форма и тесты
 * опирались на один источник правды.
 */
import { DANGEROUS_KINDS, READ_KINDS } from "./command-kinds";

export const SCENARIOS = [
  {
    id: "STATUS", icon: "◉", label: "Проверить статус", hint: "Снимок аккаунтов и лимитов без изменений.",
    group: "read", danger: false,
  },
  {
    id: "GROUPS", icon: "▤", label: "Список групп", hint: "Какие чаты видит воркер и где идёт сбор.",
    group: "read", danger: false,
  },
  {
    id: "CAMPAIGNS", icon: "≣", label: "Кампании", hint: "Последние запуски и их результаты.",
    group: "read", danger: false,
  },
  {
    id: "BROADCAST", icon: "✉", label: "Рассылка в группы", hint: "Текст в выбранные чаты. Сначала проверка, потом отправка.",
    group: "send", danger: true,
  },
  {
    id: "DMSEND", icon: "☏", label: "Личные сообщения", hint: "Ограниченная DM-отправка собранным пользователям.",
    group: "send", danger: true,
  },
  {
    id: "CONDUCT", icon: "⚑", label: "Провести кампанию", hint: "Группы, личные сообщения или оба канала сразу.",
    group: "send", danger: true,
  },
  {
    id: "BLACKLIST_ADD", icon: "⊘", label: "В чёрный список", hint: "Исключить человека из всех будущих рассылок.",
    group: "safety", danger: false,
  },
  {
    id: "STOP", icon: "■", label: "Остановить", hint: "Стоп-кран: очередь и запуски воркера прекратят работу.",
    group: "safety", danger: false,
  },
];

export const SCENARIO_GROUPS = [
  { id: "read", title: "Посмотреть" },
  { id: "send", title: "Отправить" },
  { id: "safety", title: "Сначала проверь" },
];

export const CONDUCT_MODES = [
  { id: "broadcast", short: "Группы", label: "Группы", hint: "Сообщение в каждый чат из списка рассылки." },
  { id: "channel", short: "Каналы", label: "Каналы", hint: "То же самое, но для каналов." },
  { id: "dm", short: "Личные", label: "Личные сообщения", hint: "Отправка собранным пользователям." },
  { id: "both", short: "Группы+ЛС", label: "Группы + личные", hint: "Сначала чаты, затем личные. Долго." },
];

export function scenarioFor(kind) {
  return SCENARIOS.find((item) => item.id === kind);
}

export function conductModeFor(id) {
  return CONDUCT_MODES.find((item) => item.id === id);
}

/** Какие поля показывать для выбранного сценария. */
export function formFields(kind, conductKind) {
  const isRead = READ_KINDS.has(kind);
  const showConduct = kind === "CONDUCT";
  return {
    isRead,
    isBlacklist: kind === "BLACKLIST_ADD",
    isDangerous: DANGEROUS_KINDS.has(kind),
    showConduct,
    needsText: !isRead && kind !== "STOP",
    showGroups: kind === "BROADCAST" || kind === "DMSEND" || kind === "CONDUCT",
    showLimit: kind === "DMSEND" || (showConduct && (conductKind === "dm" || conductKind === "both")),
    showDelay: kind === "BROADCAST" || kind === "DMSEND",
    showAccount: kind !== "BLACKLIST_ADD",
  };
}

/** Подпись основного действия. Пользователь видит ровно то, что произойдёт. */
export function primaryLabel(kind) {
  if (READ_KINDS.has(kind)) return "Запросить";
  if (kind === "STOP") return "Остановить";
  if (kind === "BLACKLIST_ADD") return "Исключить";
  return "Проверить без отправки";
}

/** Ориентир по объёму: сколько сообщений может уйти при реальном запуске. */
export function dangerCountFor(kind, conductKind) {
  if (kind === "CONDUCT" && conductKind === "dm") return "до 200 сообщений";
  if (kind === "CONDUCT" && conductKind === "both") return "до 10 000 сообщений";
  if (kind === "DMSEND") return "до 50 сообщений";
  return "во все выбранные чаты";
}

/** Короткая выжимка текста для предпросмотра. */
export function previewText(text, limit = 90) {
  const clean = String(text || "").trim();
  if (!clean) return "—";
  return clean.length > limit ? `${clean.slice(0, limit)}…` : clean;
}

export function delayPairLabel(min, max) {
  const lo = Number(min) || 0;
  const hi = Number(max) || 0;
  if (!lo && !hi) return "без паузы";
  return `${Math.min(lo, hi)}–${Math.max(lo, hi)} сек`;
}