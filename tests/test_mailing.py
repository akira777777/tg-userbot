"""Эффективность рассылки: персонализация, варианты, опт-аут и порядок целей."""

from __future__ import annotations

import argparse
import asyncio
import random

import pytest

import bot


def test_render_message_fills_placeholders_and_drops_empty_ones():
    ctx = {"name": "Анна", "username": "anna", "chat": "Promo"}
    assert bot.render_message("Привет, {name}! Ваш ник {username}.", ctx) == (
        "Привет, Анна! Ваш ник @anna.")
    assert bot.render_message("Привет, {name}!", {"name": ""}) == "Привет!"
    assert bot.render_message("{name}, привет!", {"name": ""}) == "привет!"
    assert bot.render_message("Группа {chat}: {name}", {"chat": "Promo", "name": ""}) == (
        "Группа Promo")
    # Неизвестный плейсхолдер остаётся текстом: шаблон не ломается молча.
    assert bot.render_message("{unknown} и {name}", {"name": "Илья"}) == "{unknown} и Илья"
    # «@{username}» и «{username}» дают один и тот же адрес без «@@».
    assert bot.render_message("Пиши @{username}", {"username": "anna"}) == "Пиши @anna"


def test_render_message_chooses_spintax_variant_per_target():
    rng = random.Random(7)
    seen = {
        bot.render_message("[[Привет|Здравствуй|Добрый день]], {name}!",
                           {"name": "Ян"}, rng)
        for _ in range(40)
    }
    assert len(seen) > 1
    assert all(text.endswith(", Ян!") for text in seen)
    # Без «|» двойные скобки не трогаем: это может быть разметка, а не варианты.
    assert bot.render_message("[[ссылка]]", {}) == "[[ссылка]]"


def test_opt_out_footer_is_added_once_and_respects_existing_stop():
    assert bot.with_opt_out("Текст") == "Текст\n\n" + bot.OPT_OUT_FOOTER
    assert bot.with_opt_out("Ответьте стоп, и я не напишу") == "Ответьте стоп, и я не напишу"
    assert bot.with_opt_out("Текст", enabled=False) == "Текст"


def test_caption_text_keeps_telegram_limit():
    assert bot.caption_text("коротко") == "коротко"
    long = "x" * 2000
    cut = bot.caption_text(long)
    assert len(cut) == bot.CAPTION_LIMIT
    assert cut.endswith("…")


def test_dm_peer_prefers_username_and_never_uses_foreign_access_hash():
    # @username разрешается из любого аккаунта; чужой access_hash — нет.
    assert bot.dm_peer({"user_id": 5, "username": "anna", "access_hash": 99}) == "@anna"
    assert bot.dm_peer({"user_id": 5, "username": "", "access_hash": 99}) == 5
    assert bot.dm_peer({"user_id": 5, "username": "", "access_hash": None}) == 5
    assert bot.dm_peer({"user_id": None, "username": ""}) is None


def test_interleave_targets_round_robins_source_groups():
    rows = [{"user_id": uid, "source_group": gid}
            for gid, uid in [(1, 1), (1, 2), (1, 3), (2, 4), (3, 5)]]
    assert [row["user_id"] for row in bot.interleave_targets(rows)] == [1, 4, 5, 2, 3]
    assert [row["user_id"] for row in bot.interleave_targets(rows, 4)] == [1, 4, 5, 2]
    assert bot.interleave_targets([]) == []


def test_campaign_stats_counts_ok_fail_and_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "legacy.session"))
    conn = bot.connect()
    try:
        conn.execute("INSERT INTO campaigns(id,kind,text,created_at) VALUES(1,'dm','x',?)",
                     (bot.now_iso(),))
        conn.execute("INSERT INTO campaigns(id,kind,text,created_at) VALUES(2,'broadcast','y',?)",
                     (bot.now_iso(),))
        for campaign, target, status in [
            (1, "10", "ok"), (1, "11", "ok"), (1, "12", "fail:privacy"),
            (1, "13", "skip:slowmode"), (2, "100", "ok"),
        ]:
            conn.execute("INSERT INTO sent(campaign,target,status,ts) VALUES(?,?,?,?)",
                         (campaign, target, status, bot.now_iso()))
        conn.commit()
        stats = {row["kind"]: row for row in bot.campaign_stats(conn, hours=24)}
        assert stats["dm"]["ok"] == 2
        assert stats["dm"]["fail"] == 1
        assert stats["dm"]["skip"] == 1
        assert stats["dm"]["total"] == 4
        assert stats["broadcast"]["ok"] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("cmd", ["dmsend", "conduct"])
def test_opt_out_flag_defaults_on_and_can_be_disabled(cmd):
    parser = bot.build_parser()
    base = ["dmsend", "--text", "hi"] if cmd == "dmsend" else ["conduct", "--what", "dm",
                                                               "--text", "hi"]
    assert parser.parse_args(base).opt_out is True
    assert parser.parse_args(base + ["--no-opt-out"]).opt_out is False


class _StubClient:
    """Клиент без сети: записывает вызовы, умеет уронить первый send."""

    def __init__(self, fail_first: bool = False) -> None:
        self.calls: list[tuple[object, object]] = []
        self.fail_first = fail_first
        self.reconnects = 0

    async def connect(self) -> None:
        self.reconnects += 1

    async def send_message(self, peer, text) -> None:
        self.calls.append((peer, text))
        if self.fail_first:
            self.fail_first = False
            raise ConnectionError("обрыв")


def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(bot, "LEGACY_SESSION", str(tmp_path / "legacy.session"))
    return bot.connect()


def _args(**over):
    base = {"dry_run": False, "delay_min": 0, "delay_max": 0, "max_runs": 0, "opt_out": True}
    base.update(over)
    return argparse.Namespace(**base)


@pytest.mark.asyncio
async def test_send_run_personalizes_dm_and_appends_opt_out(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    client = _StubClient()
    row = {"user_id": 1, "username": "anna", "first_name": "Анна"}
    try:
        await bot.send_run(
            client, conn, "dm", [row], "Привет, {name}!", _args(),
            resolve=lambda r: ("@anna", bot.dm_peer(r), str(r["user_id"]), bot.dm_context(r)),
        )
        assert client.calls == [("@anna", "Привет, Анна!\n\n" + bot.OPT_OUT_FOOTER)]
        assert conn.execute("SELECT status FROM sent").fetchone()["status"] == "ok"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_send_run_broadcast_uses_chat_placeholder_without_opt_out(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    client = _StubClient()
    row = {"id": 5, "title": "Promo"}
    try:
        await bot.send_run(
            client, conn, "broadcast", [row], "[[Анонс|Новость]] для {chat}", _args(),
            resolve=lambda r: ("группа 5", "peer-5", "5", bot.chat_context(r)),
        )
        assert len(client.calls) == 1
        peer, text = client.calls[0]
        assert peer == "peer-5"
        assert text.endswith(" для Promo")
        assert bot.OPT_OUT_FOOTER not in text
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_send_run_reconnects_once_on_link_error(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    client = _StubClient(fail_first=True)
    row = {"user_id": 2, "username": "bob", "first_name": "Боб"}
    try:
        await bot.send_run(
            client, conn, "dm", [row], "Привет, {name}!", _args(opt_out=False),
            resolve=lambda r: ("@bob", bot.dm_peer(r), str(r["user_id"]), bot.dm_context(r)),
        )
        assert len(client.calls) == 2
        assert client.reconnects == 1
        assert client.calls[-1] == ("@bob", "Привет, Боб!")
        assert conn.execute("SELECT status FROM sent").fetchone()["status"] == "ok"
    finally:
        conn.close()


def test_opt_out_footer_ignores_words_that_only_start_with_stop():
    # «стопка» и «автостоп» — не просьба отписаться: футер всё равно нужен.
    assert bot.with_opt_out("Тема: стопка ягод") == (
        "Тема: стопка ягод\n\n" + bot.OPT_OUT_FOOTER)
    assert bot.with_opt_out("автостоп на трассе") != "автостоп на трассе"
    # Явная просьба на русском и английском футер не дублирует.
    assert bot.with_opt_out("Если передумали — стоп") == "Если передумали — стоп"
    assert bot.with_opt_out("Reply STOP to opt out") == "Reply STOP to opt out"


def test_render_message_removes_dangling_at_sign_when_username_is_empty():
    assert bot.render_message("Пиши @{username}", {"username": ""}) == "Пиши"
    assert bot.render_message("@{username}, привет", {"username": ""}) == "привет"
    assert bot.render_message("Пиши @{username}", {"username": "anna"}) == "Пиши @anna"


def test_caption_text_cuts_on_word_boundary():
    cut = bot.caption_text(" ".join(["слово"] * 400))
    assert len(cut) <= bot.CAPTION_LIMIT
    assert cut.endswith("слово…")  # слово не разорвано посередине
    # Строка без пробелов режется жёстко, но по лимиту.
    assert len(bot.caption_text("x" * 2000)) == bot.CAPTION_LIMIT


def test_prepare_message_keeps_opt_out_inside_caption_limit():
    body = bot.prepare_message("Д" * 2000, {}, opt_out=True, media=True)
    assert len(body) <= bot.CAPTION_LIMIT
    assert body.endswith(bot.OPT_OUT_FOOTER)  # футер не съеден лимитом подписи
    # Без медиа лимит подписи не применяется.
    assert len(bot.prepare_message("Д" * 2000, {}, opt_out=False, media=False)) == 2000
    # Медиа без подписи: остаётся только просьба ответить «стоп».
    assert bot.prepare_message(None, {}, opt_out=True, media=True) == bot.OPT_OUT_FOOTER


@pytest.mark.asyncio
async def test_send_run_dry_run_previews_are_limited_and_show_footer(
        tmp_path, monkeypatch, capsys):
    conn = _db(tmp_path, monkeypatch)

    class NoNetwork:
        async def connect(self):
            raise AssertionError("dry-run не должен трогать Telegram")

        async def send_message(self, *args, **kwargs):
            raise AssertionError("dry-run не должен отправлять")

    rows = [{"user_id": i, "username": f"u{i}", "first_name": "Анна"} for i in range(10)]
    try:
        await bot.send_run(
            NoNetwork(), conn, "dm", rows, "Привет, {name}!", _args(dry_run=True),
            resolve=lambda r: (f"@{r['username']}", bot.dm_peer(r), str(r["user_id"]),
                               bot.dm_context(r)),
        )
        out = capsys.readouterr().out
        # Каждая цель в списке, но отрендеренных превью — только первые DRY_PREVIEWS.
        assert out.count("[dry] ->") == 10
        assert out.count("Привет, Анна!") == bot.DRY_PREVIEWS
        assert bot.OPT_OUT_FOOTER.split("«")[1].split("»")[0] in out  # «стоп» виден в превью
        assert f"превью {bot.DRY_PREVIEWS}" in out
    finally:
        conn.close()


def test_dmsend_dry_run_works_without_telegram(tmp_path, monkeypatch, capsys):
    conn = _db(tmp_path, monkeypatch)
    opened = []
    real_connect = bot.connect

    def tracked_connect():
        fresh = real_connect()
        opened.append(fresh)
        return fresh

    monkeypatch.setattr(bot, "get_client",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("dmsend --dry-run не должен звать Telegram")))
    monkeypatch.setattr(bot, "connect", tracked_connect)
    try:
        conn.execute(
            "INSERT INTO members(user_id,username,first_name,is_bot,collected_at,deleted) "
            "VALUES(1,'anna','Анна',0,?,0)", (bot.now_iso(),))
        conn.commit()
        args = bot.build_parser().parse_args(
            ["dmsend", "--text", "Привет, {name}!", "--dry-run", "--limit", "1"])
        asyncio.run(bot.cmd_dmsend(args))
        out = capsys.readouterr().out
        assert "[dry] -> @anna" in out
        assert "Привет, Анна!" in out
    finally:
        for extra in opened:
            extra.close()
        conn.close()
