import { describe, expect, it } from "vitest";

import {
  CONDUCT_MODES,
  SCENARIOS,
  conductModeFor,
  dangerCountFor,
  delayPairLabel,
  formFields,
  previewText,
  primaryLabel,
  scenarioFor,
} from "../../lib/command-scenarios";
import { validateCommand } from "../../lib/command-validation";

describe("сценарии", () => {
  it("каждый сценарий — валидная команда с уникальным id", () => {
    const ids = SCENARIOS.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(SCENARIOS.every((item) => item.label && item.hint && item.icon)).toBe(true);
  });

  it("чёрный список — отдельный сценарий без рассылки", () => {
    const item = scenarioFor("BLACKLIST_ADD");
    expect(item.group).toBe("safety");
    expect(item.danger).toBe(false);
    expect(primaryLabel("BLACKLIST_ADD")).toBe("Исключить");
  });

  it("виды опасной отправки помечены danger", () => {
    expect(SCENARIOS.filter((item) => item.danger).map((item) => item.id)).toEqual([
      "BROADCAST", "DMSEND", "CONDUCT",
    ]);
  });

  it("режимы кампании доступны по id", () => {
    expect(CONDUCT_MODES).toHaveLength(4);
    expect(conductModeFor("both").short).toBe("Группы+ЛС");
    expect(conductModeFor("unknown")).toBeUndefined();
  });
});

describe("formFields", () => {
  it("у чёрного списка нет аккаунта и групп", () => {
    const fields = formFields("BLACKLIST_ADD", "broadcast");
    expect(fields.showAccount).toBe(false);
    expect(fields.showGroups).toBe(false);
    expect(fields.needsText).toBe(true);
    expect(fields.isDangerous).toBe(false);
  });

  it("лимит появляется только у DM-режимов кампании", () => {
    expect(formFields("CONDUCT", "broadcast").showLimit).toBe(false);
    expect(formFields("CONDUCT", "dm").showLimit).toBe(true);
    expect(formFields("DMSEND", "broadcast").showLimit).toBe(true);
  });
});

describe("подписи", () => {
  it("основная кнопка говорит правду о действии", () => {
    expect(primaryLabel("STATUS")).toBe("Запросить");
    expect(primaryLabel("STOP")).toBe("Остановить");
    expect(primaryLabel("BROADCAST")).toBe("Проверить без отправки");
  });

  it("предупреждение о объёме зависит от режима", () => {
    expect(dangerCountFor("CONDUCT", "both")).toContain("10 000");
    expect(dangerCountFor("BROADCAST", "broadcast")).toContain("все");
  });

  it("пустой текст и короткая пауза", () => {
    expect(previewText("   ")).toBe("—");
    expect(previewText("a".repeat(200)).endsWith("…")).toBe(true);
    expect(delayPairLabel(0, 0)).toBe("без паузы");
    expect(delayPairLabel(45, 20)).toBe("20–45 сек");
  });
});

describe("validateCommand", () => {
  const sendFields = formFields("BROADCAST", "broadcast");

  it("требует текст для рассылки", () => {
    expect(validateCommand({ kind: "BROADCAST", text: "  ", fields: sendFields })).toContain("текст");
  });

  it("пропускает корректную рассылку", () => {
    expect(validateCommand({
      kind: "BROADCAST", text: "привет", delayMin: 20, delayMax: 45, fields: sendFields,
    })).toBe("");
  });

  it("ловит перевёрнутую паузу", () => {
    expect(validateCommand({
      kind: "BROADCAST", text: "привет", delayMin: 60, delayMax: 10, fields: sendFields,
    })).toContain("Пауза");
  });

  it("проверяет формат записи в чёрный список", () => {
    const fields = formFields("BLACKLIST_ADD", "broadcast");
    expect(validateCommand({ kind: "BLACKLIST_ADD", text: "", fields })).toContain("@username");
    expect(validateCommand({ kind: "BLACKLIST_ADD", text: "два слова", fields })).toContain("пробелов");
    expect(validateCommand({ kind: "BLACKLIST_ADD", text: "not-a-user", fields })).toContain("Не похоже");
    expect(validateCommand({ kind: "BLACKLIST_ADD", text: "@ivan_petrov", fields })).toBe("");
    expect(validateCommand({ kind: "BLACKLIST_ADD", text: "123456789", fields })).toBe("");
    expect(validateCommand({ kind: "BLACKLIST_ADD", text: "-1001234567", fields })).toBe("");
  });

  it("ограничивает лимит DM и режимов CONDUCT", () => {
    const dmFields = formFields("DMSEND", "broadcast");
    expect(validateCommand({ kind: "DMSEND", text: "x", limit: 0, fields: dmFields })).toContain("от 1");
    expect(validateCommand({ kind: "DMSEND", text: "x", limit: 999, fields: dmFields })).toContain("от 1");
    expect(validateCommand({ kind: "DMSEND", text: "x", limit: 20, fields: dmFields })).toBe("");

    const conductDmFields = formFields("CONDUCT", "dm");
    expect(validateCommand({ kind: "CONDUCT", conductKind: "dm", text: "x", limit: 0, fields: conductDmFields })).toContain("от 1");
    expect(validateCommand({ kind: "CONDUCT", conductKind: "dm", text: "x", limit: 201, fields: conductDmFields })).toContain("до 200");
    expect(validateCommand({ kind: "CONDUCT", conductKind: "dm", text: "x", limit: 100, fields: conductDmFields })).toBe("");

    const conductBothFields = formFields("CONDUCT", "both");
    expect(validateCommand({ kind: "CONDUCT", conductKind: "both", text: "x", limit: 0, fields: conductBothFields })).toContain("от 1");
    expect(validateCommand({ kind: "CONDUCT", conductKind: "both", text: "x", limit: 10001, fields: conductBothFields })).toContain("до 10000");
    expect(validateCommand({ kind: "CONDUCT", conductKind: "both", text: "x", limit: 500, fields: conductBothFields })).toBe("");
  });

  it("не содержит устаревших упоминаний Claude в подписях сценариев", () => {
    for (const item of SCENARIOS) {
      expect(item.label).not.toMatch(/claude/i);
      expect(item.hint).not.toMatch(/claude/i);
    }
  });
});
