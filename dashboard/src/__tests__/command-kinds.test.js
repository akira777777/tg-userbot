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

  it("targets one group and a named account stop", () => {
    expect(payloadFor("BROADCAST", { text: "hi", groupId: "-1005" }).group_ids).toEqual([-1005]);
    expect(payloadFor("STOP", { account: "work2" })).toEqual({ account: "work2" });
    expect(payloadFor("STOP")).toEqual({ account: "all" });
  });

  it("builds a conduct DM payload the worker can validate", () => {
    expect(payloadFor("CONDUCT", { text: " wave ", conductKind: "dm", groupId: "9", limit: 4 })).toEqual({
      text: "wave",
      kind: "dm",
      group_id: 9,
      limit: 4,
    });
    expect(payloadFor("CONDUCT", { text: "groups", conductKind: "broadcast" })).toEqual({
      text: "groups",
      kind: "broadcast",
    });
    expect(payloadFor("CONDUCT", { text: " both ", conductKind: "both", limit: 4 })).toEqual({
      text: "both",
      kind: "both",
      limit: 4,
    });
    expect(payloadFor("CONDUCT", { text: " both ", conductKind: "both", groupId: "-10042", limit: 10 })).toEqual({
      text: "both",
      kind: "both",
      group_id: -10042,
      group_ids: [-10042],
      limit: 10,
    });
  });

  it("normalizes reversed delay bounds consistently with delayPair", () => {
    expect(payloadFor("BROADCAST", { text: "hi", delayMin: 45, delayMax: 20 })).toEqual({
      text: "hi",
      delay_min: 20,
      delay_max: 45,
    });
  });
});

describe("formatNumber", () => {
  it("renders empty values as an em dash", () => {
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(1200)).toMatch(/1[\s\u00a0]200/);
  });
});
