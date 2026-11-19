import { describe, expect, it } from "vitest";
import { workerStatus } from "../../lib/worker-status";

const NOW = Date.parse("2026-11-19T05:00:00Z");

describe("workerStatus (DASH-607)", () => {
  it("свежий heartbeat — онлайн", () => {
    const worker = {
      last_heartbeat_at: "2026-11-19T04:59:00Z",
      online: true,
      stale_seconds: 600,
    };
    expect(workerStatus(worker, NOW)).toMatchObject({ tone: "ok" });
    expect(workerStatus(worker, NOW).label).toContain("воркер онлайн");
  });

  it("просроченный heartbeat — офлайн", () => {
    const worker = {
      last_heartbeat_at: "2026-11-19T04:30:00Z",
      online: false,
      stale_seconds: 600,
    };
    expect(workerStatus(worker, NOW)).toMatchObject({ tone: "warn" });
    expect(workerStatus(worker, NOW).label).toContain("воркер офлайн");
  });

  it("нет данных — неизвестно", () => {
    expect(workerStatus(null, NOW)).toMatchObject({ tone: "dim" });
    expect(workerStatus({ last_heartbeat_at: null, online: null }, NOW).label).toContain("неизвестно");
  });

  it("недавний сигнал показывает «только что»", () => {
    const worker = { last_heartbeat_at: new Date(NOW - 10_000).toISOString(), online: true };
    expect(workerStatus(worker, NOW).label).toContain("только что");
  });
});
