import { describe, expect, it } from "vitest";

import { sessionSecret } from "../../lib/control-auth";
import { commandRejection } from "../../lib/control-queue";

describe("command contract", () => {
  it("rejects unsupported commands before enqueue", () => {
    expect(commandRejection("DELETE_ALL", "LIVE")).toBe("неподдерживаемая команда");
    expect(commandRejection("BROADCAST", "READ")).toMatch(/READ/);
    expect(commandRejection("BROADCAST", "DRY_RUN")).toBeNull();
    expect(commandRejection("STATUS", "READ")).toBeNull();
  });

  it("rejects a weak control session secret", () => {
    process.env.CONTROL_SESSION_SECRET = "change-me-in-production";
    expect(() => sessionSecret()).toThrow(/unsafe/);
  });
});
