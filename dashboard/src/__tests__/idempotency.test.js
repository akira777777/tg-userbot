import { describe, expect, it, vi } from "vitest";
import { newIdempotencyKey } from "../../lib/idempotency";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("newIdempotencyKey", () => {
  it("использует crypto.randomUUID, когда он есть", () => {
    const spy = vi.spyOn(crypto, "randomUUID").mockReturnValue("11111111-1111-4111-8111-111111111111");
    expect(newIdempotencyKey()).toBe("11111111-1111-4111-8111-111111111111");
    spy.mockRestore();
  });

  it("работает без randomUUID (http на LAN — не secure context)", () => {
    const original = crypto.randomUUID;
    // @ts-expect-error — имитируем старый/небезопасный контекст
    delete crypto.randomUUID;
    try {
      const key = newIdempotencyKey();
      expect(key).toMatch(UUID_V4);
      expect(newIdempotencyKey()).not.toBe(key);
    } finally {
      crypto.randomUUID = original;
    }
  });

  it("работает даже без crypto.getRandomValues", () => {
    const originalUUID = crypto.randomUUID;
    const originalValues = crypto.getRandomValues;
    // @ts-expect-error — крайний случай
    delete crypto.randomUUID;
    // @ts-expect-error — крайний случай
    delete crypto.getRandomValues;
    try {
      expect(newIdempotencyKey()).toMatch(UUID_V4);
    } finally {
      crypto.randomUUID = originalUUID;
      crypto.getRandomValues = originalValues;
    }
  });
});
