/**
 * Серверные псевдонимы аккаунтов: внутренние имена (main, work2, ...)
 * не уходят в браузер. UI оперирует ключами acc-1, acc-2, ..., сервер
 * транслирует ключ обратно во внутреннее имя перед постановкой в очередь.
 *
 * Чистые функции без React/server-only, чтобы покрывать их vitest-тестами.
 */

/** Детерминированный порядок: main первым, дальше по ASCII (как sortAccounts). */
export function accountNameOrder(names = []) {
  return [...new Set(names.filter((name) => typeof name === "string" && name))].sort(
    (left, right) => {
      if (left === "main" && right !== "main") return -1;
      if (right === "main" && left !== "main") return 1;
      return left < right ? -1 : left > right ? 1 : 0;
    },
  );
}

/**
 * Карта имён -> псевдонимы.
 * Возвращает aliases [{key, display_name, internal_name}], byKey, byName.
 * display_name: «Аккаунт N» с N от 1 в каноническом порядке.
 */
export function accountAliasMap(names = []) {
  const ordered = accountNameOrder(names);
  const aliases = ordered.map((name, index) => ({
    key: `acc-${index + 1}`,
    display_name: `Аккаунт ${index + 1}`,
    internal_name: name,
  }));
  const byKey = new Map(aliases.map((alias) => [alias.key, alias]));
  const byName = new Map(aliases.map((alias) => [alias.internal_name, alias]));
  return { aliases, byKey, byName };
}

/**
 * Перевод значения поля account из запроса во внутреннее имя.
 * Принимает ключ-псевдоним (acc-N), а для обратной совместимости —
 * и внутреннее имя. "all" остаётся "all". Неизвестное значение -> null.
 */
export function toInternalAccount(account, map) {
  if (account === undefined || account === null || account === "") return null;
  if (account === "all") return "all";
  if (map.byKey.has(account)) return map.byKey.get(account).internal_name;
  if (map.byName.has(account)) return account;
  return null;
}

/** Отображаемое имя для внутреннего имени в ответах API. */
export function displayNameFor(internalName, map, fallback = "Аккаунт ?") {
  return map.byName.get(internalName)?.display_name ?? fallback;
}
