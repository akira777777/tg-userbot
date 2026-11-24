import { beforeEach, describe, expect, it, vi } from "vitest";

const opens = vi.hoisted(() => ({ n: 0 }));

vi.mock("node:sqlite", () => ({
  DatabaseSync: class {
    constructor() {
      opens.n += 1;
    }

    prepare() {
      return { all: () => [] };
    }

    close() {}
  },
}));

import { buildLocalSnapshot, clearLocalSnapshotCache, loadLocalSnapshot, snapshotCacheFresh } from "../../lib/local-data";

const SAMPLE = {
  accounts: [
    { name: "work2", enabled: 1, daily_budget: 7, flood_until: null, note: "sync" },
    { name: "main", enabled: 0, daily_budget: 30, flood_until: "2026-11-20T03:32:41+00:00", note: "imported" },
  ],
  dmToday: [{ account: "main", total: 5 }],
  covered: [{ account: "main", total: 40 }],
  writable: [{ account: "main", total: 12 }],
  groups: [{ id: -100, title: "Прага", in_broadcast: 1 }],
  membersByGroup: [{ group_id: -100, total: 320 }],
  campaigns: [{ id: 29, kind: "dm", account: "main", created_at: "2026-11-19T03:32:37+00:00", ok: 4, fail: 1 }],
  stats: [
    { key: "groups_total", value: 1 },
    { key: "members_total", value: 320 },
  ],
};

describe("buildLocalSnapshot (локальный режим)", () => {
  it("не отдаёт внутренние имена аккаунтов — только key/display_name", () => {
    const snapshot = buildLocalSnapshot(SAMPLE);
    const names = JSON.stringify(snapshot);
    expect(names).not.toContain('"name":"main"');
    expect(names).not.toContain('"name":"work2"');
    expect(names).not.toContain("userbot.session");
    expect(names).not.toContain("api_hash");

    expect(snapshot.accounts.map((row) => row.key)).toEqual(["acc-1", "acc-2"]);
    // main всегда первый («Аккаунт 1»)
    expect(snapshot.accounts[0]).toMatchObject({ display_name: "Аккаунт 1", enabled: 0, dm_today: 5 });
    expect(snapshot.accounts[1]).toMatchObject({ display_name: "Аккаунт 2", enabled: 1 });
  });

  it("собирает группы, кампании и статистику в форму зеркала", () => {
    const snapshot = buildLocalSnapshot(SAMPLE);
    expect(snapshot.localMode).toBe(true);
    expect(snapshot.groups[0]).toMatchObject({ id: -100, title: "Прага", in_broadcast: true, members_collected: 320 });
    expect(snapshot.campaigns[0]).toMatchObject({ id: 29, kind: "dm", account_display: "Аккаунт 1", ok: 4, fail: 1 });
    expect(snapshot.campaigns[0].account).toBeUndefined();
    expect(snapshot.stats.map((row) => row.key)).toEqual(["groups_total", "members_total"]);
    expect(snapshot.worker).toEqual({ last_heartbeat_at: null, online: null, stale_seconds: 600 });
  });

  it("пустые входные данные дают пустые списки, а не ошибку", () => {
    const snapshot = buildLocalSnapshot({
      accounts: [], dmToday: [], covered: [], writable: [], groups: [], membersByGroup: [], campaigns: [], stats: [],
    });
    expect(snapshot.accounts).toEqual([]);
    expect(snapshot.account_aliases).toEqual([]);
    expect(snapshot.groups).toEqual([]);
  });
});

describe("loadLocalSnapshot cache", () => {
  beforeEach(() => {
    clearLocalSnapshotCache();
    opens.n = 0;
  });

  it("считает кэш свежим только внутри 10 секунд", () => {
    expect(snapshotCacheFresh({ at: 1_000 }, 10_999)).toBe(true);
    expect(snapshotCacheFresh({ at: 1_000 }, 11_000)).toBe(false);
    expect(snapshotCacheFresh(null, 11_000)).toBe(false);
  });

  it("не перечитывает members, пока снапшот моложе 10 секунд", async () => {
    const first = await loadLocalSnapshot(1_000);
    const second = await loadLocalSnapshot(1_000 + 9_999);
    expect(second).toBe(first);
    expect(opens.n).toBe(1);

    await loadLocalSnapshot(1_000 + 10_000);
    expect(opens.n).toBe(2);
  });
});
