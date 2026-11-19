import { describe, expect, it } from "vitest";
import {
  accountAliasMap,
  accountNameOrder,
  displayNameFor,
  toInternalAccount,
} from "../../lib/account-aliases";

describe("accountAliasMap (DASH-603)", () => {
  it("ставит main первым и присваивает детерминированные ключи", () => {
    const map = accountAliasMap(["work2", "main", "work3"]);
    expect(map.aliases.map((alias) => alias.key)).toEqual(["acc-1", "acc-2", "acc-3"]);
    expect(map.aliases[0]).toEqual({ key: "acc-1", display_name: "Аккаунт 1", internal_name: "main" });
    expect(map.aliases[1].internal_name).toBe("work2");
    expect(map.aliases[2].internal_name).toBe("work3");
  });

  it("игнорирует пустые значения и дубликаты", () => {
    expect(accountNameOrder(["", null, undefined, "a", "a"])).toEqual(["a"]);
    expect(accountAliasMap([]).aliases).toEqual([]);
  });

  it("toInternalAccount переводит ключ и принимает внутреннее имя для совместимости", () => {
    const map = accountAliasMap(["main", "work2"]);
    expect(toInternalAccount("acc-1", map)).toBe("main");
    expect(toInternalAccount("acc-2", map)).toBe("work2");
    expect(toInternalAccount("main", map)).toBe("main");
    expect(toInternalAccount("work2", map)).toBe("work2");
    expect(toInternalAccount("all", map)).toBe("all");
    expect(toInternalAccount("acc-9", map)).toBeNull();
    expect(toInternalAccount("", map)).toBeNull();
    expect(toInternalAccount(undefined, map)).toBeNull();
  });

  it("displayNameFor возвращает псевдоним или запасной текст", () => {
    const map = accountAliasMap(["main"]);
    expect(displayNameFor("main", map)).toBe("Аккаунт 1");
    expect(displayNameFor("unknown", map)).toBe("Аккаунт ?");
  });
});
