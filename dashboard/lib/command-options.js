/**
 * Чистые хелперы панели: варианты выбора и нормализация payload.
 * Живут отдельно от UI, чтобы покрывать их тестами без рендера React.
 */

/** Пауза между сообщениями: значения меняются местами, если ввели задом наперёд. */
export function delayPair(delayMin, delayMax) {
  const min = Number(delayMin);
  const max = Number(delayMax);
  const safeMin = Number.isFinite(min) && min > 0 ? min : 20;
  const safeMax = Number.isFinite(max) && max > 0 ? max : 45;
  return { delay_min: Math.min(safeMin, safeMax), delay_max: Math.max(safeMin, safeMax) };
}

/** Активна ли пауза FloodWait в указанный момент времени. Просроченные метки не считаются паузой. */
export function isFloodActive(floodUntil, now = Date.now()) {
  if (!floodUntil) return false;
  const ts = floodUntil instanceof Date ? floodUntil.getTime() : Date.parse(String(floodUntil));
  if (!Number.isFinite(ts)) return false;
  return ts > now;
}

/** Детерминированный порядок аккаунтов: сервер уже шлёт отсортированный список. */
export function sortAccounts(accounts = []) {
  return [...(accounts || [])].filter((item) => item?.key);
}

/**
 * Варианты выбора аккаунта.
 * Сервер отдаёт key (acc-N) и display_name («Аккаунт N»); внутренние имена
 * (main, work2) в UI не попадают.
 * allowAll — добавить пункт «Все аккаунты» (STOP/CONDUCT).
 */
export function accountOptions(accounts = [], { allowAll = false, now = Date.now() } = {}) {
  const real = sortAccounts(accounts);
  const options = real.map((item) => {
    const floodActive = isFloodActive(item.flood_until, now);
    return {
      value: item.key,
      label: item.display_name || "Аккаунт ?",
      enabled: item.enabled !== false && !floodActive,
      note: item.enabled === false ? "выключен" : floodActive ? "пауза до снятия ограничения" : "",
    };
  });
  if (allowAll) options.unshift({ value: "all", label: "Все аккаунты", enabled: true, note: "" });
  if (!options.length) options.push({ value: "main", label: "Аккаунт 1", enabled: true, note: "" });
  return options;
}

/**
 * Фактический ключ аккаунта для запроса: «all» допустим только для CONDUCT,
 * для остальных команд берём первый реальный аккаунт.
 */
export function resolveRequestAccount(kind, selectedAccount, accounts = []) {
  if (kind === "CONDUCT" && selectedAccount === "all") return "all";
  if (selectedAccount && selectedAccount !== "all") return selectedAccount;
  return sortAccounts(accounts)[0]?.key || "main";
}

/** Лимит получателей из поля ввода, зажатый в допустимый диапазон. */
export function clampLimit(value, min, max, fallback = min) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

export const LIMITS = {
  dm: { min: 1, max: 50, fallback: 20 },
  conductDm: { min: 1, max: 200, fallback: 20 },
  conductBoth: { min: 1, max: 10000, fallback: 100 },
  delayMin: { min: 1, max: 3600, fallback: 20 },
  delayMax: { min: 1, max: 3600, fallback: 45 },
};

/** Группы с поиском по названию — для фильтра в выпадающем списке. */
export function filterGroups(groups = [], query = "") {
  const needle = query.trim().toLowerCase();
  const list = (groups || []).filter((group) => group?.id != null);
  if (!needle) return list;
  return list.filter((group) => {
    const title = String(group.title || group.id).toLowerCase();
    return title.includes(needle) || String(group.id).includes(needle);
  });
}
