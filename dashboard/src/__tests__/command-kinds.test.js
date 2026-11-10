import { describe, expect, it } from "vitest";

import { commandLabel, payloadFor } from "../../lib/command-kinds";
import { formatNumber } from "../../lib/format";

describe("command payloads", () => {
  it("keeps read commands empty", () => {
    expect(payloadFor("STATUS")).toEqual({});
  });

  it("builds a dry-run friendly broadcast payload", () => {
    expect(payloadFor("BROADCAST", { text: "  hello  ", delayMin: 10, delayMax: 30 })).toEqual({
      text: "hello",
      delay_min: 10,
      delay_max: 30,
    });
  });

  it("caps DM limit and labels kinds in Russian", () => {
    expect(payloadFor("DMSEND", { text: "hi", limit: 0 }).limit).toBe(1);
    expect(commandLabel("BROADCAST")).toBe("Рассылка в группы");
  });
});

describe("formatNumber", () => {
  it("renders empty values as an em dash", () => {
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(1200)).toMatch(/1[\s\u00a0]200/);
  });
});
