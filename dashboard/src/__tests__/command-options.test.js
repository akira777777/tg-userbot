import { describe, expect, it } from "vitest";

import { accountOptions, clampLimit, delayPair, filterGroups, resolveRequestAccount } from "../../lib/command-options";
import { errorTextFor, payloadFor } from "../../lib/command-kinds";
import { friendlyError } from "../../lib/format";

describe("payloadFor: режимы кампании", () => {
  it("уводит каналы в обычную рассылку по группам", () => {
    expect(payloadFor("CONDUCT", { text: "hi", conductKind: "channel", groupId: "-1009" })).toEqual({
      text: "hi",
      kind: "broadcast",
      group_ids: [-1009],
    });
  });

  it("не считает «all» реальным аккаунтом для STOP", () => {
    expect(payloadFor("STOP", { account: "all" })).toEqual({ account: "all" });
  });
});

describe("accountOptions", () => {
  const accounts = [
    { key: "acc-1", display_name: "Аккаунт 1", enabled: true, flood_until: null },
    { key: "acc-2", display_name: "Аккаунт 2", enabled: false, flood_until: null },
  ];

  it("показывает только псевдонимы, без внутренних имён", () => {
    expect(accountOptions(accounts).map((item) => item.label)).toEqual(["Аккаунт 1", "Аккаунт 2"]);
  });

  it("добавляет «Все аккаунты» только когда разрешено", () => {
    expect(accountOptions(accounts, { allowAll: true })[0]).toMatchObject({ value: "all" });
    expect(accountOptions(accounts, { allowAll: false }).some((item) => item.value === "all")).toBe(false);
  });

  it("помечает аккаунт с активным flood как недоступный, а с истёкшим — как доступный", () => {
    const future = new Date(Date.now() + 3600_000).toISOString();
    const past = "2026-01-01T00:00:00Z";
    const list = accountOptions([
      { key: "acc-1", display_name: "Аккаунт 1", enabled: true, flood_until: past },
      { key: "acc-2", display_name: "Аккаунт 2", enabled: true, flood_until: future },
    ]);
    expect(list.map((item) => [item.value, item.label, item.enabled])).toEqual([
      ["acc-1", "Аккаунт 1", true],
      ["acc-2", "Аккаунт 2", false],
    ]);
    expect(list[0].note).toBe("");
    expect(list[1].note).toContain("пауза");
  });

  it("отличает выключенный аккаунт от аккаунта на паузе", () => {
    const list = accountOptions([
      { key: "acc-1", display_name: "Аккаунт 1", enabled: false, flood_until: null },
      { key: "acc-2", display_name: "Аккаунт 2", enabled: true, flood_until: null },
    ]);
    expect(list[0]).toMatchObject({ enabled: false, note: "выключен" });
    expect(list[1]).toMatchObject({ enabled: true, note: "" });
  });

  it("возвращает заглушку, если аккаунтов нет", () => {
    expect(accountOptions([])).toEqual([{ value: "main", label: "Аккаунт 1", enabled: true, note: "" }]);
  });
});

describe("resolveRequestAccount", () => {
  const accounts = [{ key: "acc-1" }, { key: "acc-2" }];

  it("«all» допустим только для CONDUCT", () => {
    expect(resolveRequestAccount("CONDUCT", "all", accounts)).toBe("all");
    expect(resolveRequestAccount("BROADCAST", "all", accounts)).toBe("acc-1");
  });

  it("падает на первый реальный аккаунт", () => {
    expect(resolveRequestAccount("DMSEND", "", accounts)).toBe("acc-1");
    expect(resolveRequestAccount("DMSEND", "", [])).toBe("main");
  });
});

describe("clampLimit & delayPair", () => {
  it("зажимает лимит в допустимый диапазон", () => {
    expect(clampLimit(0, 1, 50, 20)).toBe(1);
    expect(clampLimit(999, 1, 50, 20)).toBe(50);
    expect(clampLimit("abc", 1, 50, 20)).toBe(20);
  });

  it("меняет местами паузы, если ввели задом наперёд", () => {
    expect(delayPair(45, 20)).toEqual({ delay_min: 20, delay_max: 45 });
  });
});

describe("filterGroups", () => {
  const groups = [{ id: -1, title: "Прага аренда" }, { id: -2, title: "Кофе" }];

  it("находит по названию без учёта регистра", () => {
    expect(filterGroups(groups, "праг").map((g) => g.id)).toEqual([-1]);
  });

  it("без запроса возвращает всё, но отбрасывает строки без id", () => {
    expect(filterGroups([...groups, { title: "без id" }])).toHaveLength(2);
  });
});

describe("ошибки", () => {
  it("переводит истёкшую сессию и устаревшее подтверждение", () => {
    expect(errorTextFor(new Error("Unauthenticated"))).toContain("Сессия истекла");
    expect(errorTextFor(new Error("Confirmation is invalid or expired"))).toContain("Подтверждение устарело");
  });

  it("friendlyError прячет сетевую ошибку", () => {
    expect(friendlyError(new Error("Failed to fetch"))).toContain("Нет связи");
    expect(friendlyError("не удалось")).toBe("не удалось");
  });
});
