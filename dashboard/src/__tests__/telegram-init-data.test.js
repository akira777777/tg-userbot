import { describe, expect, it } from "vitest";

import { extractTelegramInitData } from "../../lib/telegram-init-data";

describe("extractTelegramInitData", () => {
  it("prefers WebApp initData", () => {
    expect(extractTelegramInitData({ initData: "user=%7B%7D&hash=abc", hash: "#tgWebAppData=from-hash" })).toBe(
      "user=%7B%7D&hash=abc",
    );
  });

  it("reads Telegram Desktop launch hash", () => {
    const initData = "auth_date=1&user=%7B%22id%22%3A1%7D&hash=deadbeef";
    expect(extractTelegramInitData({ hash: `#tgWebAppData=${encodeURIComponent(initData)}&tgWebAppVersion=8.0` })).toBe(
      initData,
    );
  });

  it("returns empty when Telegram did not launch the page", () => {
    expect(extractTelegramInitData({ hash: "", search: "" })).toBe("");
  });
});
