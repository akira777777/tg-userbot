import { describe, expect, it } from "vitest";
import { resultLine, resultOutput } from "../../lib/command-results";

describe("resultLine", () => {
  it("не превращает признак выполнения в текст", () => {
    expect(resultLine({ ok: true, account: "Аккаунт 1", output: "" })).toBe("команда выполнена");
    expect(resultLine({ ok: false })).toBe("");
  });

  it("показывает числовые метрики кампании", () => {
    expect(resultLine({ ok: 12, fail: 1 })).toBe("успешно: 12 · ошибок: 1");
    expect(resultLine({ sent: 5, ok: true })).toBe("отправлено: 5");
    expect(resultLine({ total: 40, ok: 38, fail: 2 })).toBe("успешно: 38 · ошибок: 2 · всего: 40");
  });

  it("пустая или чужая сводка не ломает строку", () => {
    expect(resultLine(null)).toBe("");
    expect(resultLine(undefined)).toBe("");
    expect(resultLine("text")).toBe("");
    expect(resultLine({ ok: "true" })).toBe("");
  });
});

describe("resultOutput", () => {
  it("возвращает очищенный текст вывода выполненной команды", () => {
    expect(resultOutput({ ok: true, output: "  аккаунты: 3\n  группы: 12  " })).toBe("аккаунты: 3\n  группы: 12");
    expect(resultOutput({ ok: true, output: "   " })).toBe("");
    expect(resultOutput({ ok: true })).toBe("");
    expect(resultOutput(null)).toBe("");
  });
});
