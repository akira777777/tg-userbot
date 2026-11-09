#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tg-userbot — Telegram userbot-инструмент:

  * сбор никнеймов участников групп  -> SQLite (members)
  * авто-добавление групп в список рассылки (groups --sync / watch)
  * рассылка сообщений в сами группы        (broadcast)
  * личные DM собранным участникам          (dmsend) — с троттлингом,
    дедупликацией по прошлым кампаниям и чёрным списком.

Полная инструкция — README.md. Скрипт можно запускать без .env
для команд status / export / blacklist (они работают только с базой).
"""
import argparse
import asyncio
import collections
import csv
import mimetypes
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("USERBOT_DB", ROOT / "userbot.db"))
SESSION_DIR = Path(os.environ.get("USERBOT_SESSIONS", ROOT / "sessions"))
MEDIA_DIR = Path(os.environ.get("USERBOT_MEDIA", ROOT / "media_cache"))
ACCOUNTS_RE = re.compile(r"^[a-z0-9_\-]{1,32}$")
LEGACY_SESSION = os.environ.get("USERBOT_SESSION", str(ROOT / "userbot.session"))
STOP_RE = re.compile(r"^(stop|opt.?out|unsubscribe|отпиш|отпис|стоп)", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups(
  id INTEGER PRIMARY KEY,
  title TEXT, username TEXT, gtype TEXT,
  added_at TEXT, active INTEGER DEFAULT 1, in_broadcast INTEGER DEFAULT 1,
  skip_reason TEXT
);
CREATE TABLE IF NOT EXISTS members(
  user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
  is_bot INTEGER DEFAULT 0, collected_at TEXT, source_group INTEGER,
  deleted INTEGER DEFAULT 0, access_hash INTEGER, dm_block TEXT
);
CREATE TABLE IF NOT EXISTS member_sources(
  user_id INTEGER, group_id INTEGER, PRIMARY KEY(user_id, group_id)
);
CREATE TABLE IF NOT EXISTS campaigns(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, text TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sent(
  campaign INTEGER, target TEXT, status TEXT, ts TEXT,
  PRIMARY KEY(campaign, target)
);
CREATE TABLE IF NOT EXISTS blacklist(
  user_id INTEGER PRIMARY KEY, username TEXT, reason TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS accounts(
  name TEXT PRIMARY KEY, session TEXT, user_id INTEGER, api_id TEXT, api_hash TEXT,
  enabled INTEGER DEFAULT 1, daily_budget INTEGER DEFAULT 30,
  broadcast_budget INTEGER DEFAULT 15,
  flood_until TEXT, note TEXT, added_at TEXT
);
CREATE TABLE IF NOT EXISTS account_groups(
  account TEXT, group_id INTEGER, can_write INTEGER DEFAULT 1, seen_at TEXT,
  PRIMARY KEY(account, group_id)
);
CREATE TABLE IF NOT EXISTS media(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT, msg_id INTEGER, kind TEXT, title TEXT, mime TEXT,
  size INTEGER, path TEXT, added_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ag_account ON account_groups(account);
CREATE INDEX IF NOT EXISTS idx_sources_group ON member_sources(group_id);
CREATE INDEX IF NOT EXISTS idx_sources_user ON member_sources(user_id);
CREATE INDEX IF NOT EXISTS idx_sent_campaign ON sent(campaign);
CREATE INDEX IF NOT EXISTS idx_members_bot ON members(is_bot);
"""

ENV = {}


def load_env():
    """Читает .env рядом со скриптом; переменные окружения имеют приоритет."""
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                ENV[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("API_ID", "API_HASH"):
        if os.environ.get(k):
            ENV[k] = os.environ[k]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # читаем из web.py параллельно с записью
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def _add_column(conn, table, column, ddl):
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def migrate(conn):
    """Идемпотентно: колонка account у кампаний/отправок + сид главного аккаунта."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(campaigns)")}
    if "account" not in cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN account TEXT DEFAULT 'main'")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sent)")}
    if "account" not in cols:
        conn.execute("ALTER TABLE sent ADD COLUMN account TEXT DEFAULT 'main'")
    _add_column(conn, "groups", "skip_reason", "TEXT")
    _add_column(conn, "members", "deleted", "INTEGER DEFAULT 0")
    _add_column(conn, "members", "access_hash", "INTEGER")
    _add_column(conn, "members", "dm_block", "TEXT")
    _add_column(conn, "accounts", "broadcast_budget", "INTEGER DEFAULT 15")
    conn.execute("INSERT OR IGNORE INTO accounts(name,session,added_at) VALUES('main',?,?)",
                 (LEGACY_SESSION, now_iso()))
    conn.commit()


def record_edge(conn, account, gid, can_write=None):
    """Покрытие: аккаунт состоит в группе.

    can_write=None не затирает права (их выставляет probe или явная классификация).
    """
    if can_write is None:
        conn.execute("INSERT INTO account_groups(account,group_id,can_write,seen_at) "
                     "VALUES(?,?,1,?) ON CONFLICT(account,group_id) DO UPDATE SET seen_at=excluded.seen_at",
                     (account, gid, now_iso()))
        return
    conn.execute(
        "INSERT INTO account_groups(account,group_id,can_write,seen_at) "
        "VALUES(?,?,?,?) ON CONFLICT(account,group_id) DO UPDATE SET "
        "seen_at=excluded.seen_at, can_write=excluded.can_write",
        (account, gid, int(can_write), now_iso()))


def refresh_group_flags(conn, gid, reason):
    """Группа в рассылке, если хоть один аккаунт может в неё писать."""
    row = conn.execute(
        "SELECT MAX(can_write) c FROM account_groups WHERE group_id=?", (gid,)).fetchone()
    if row and row["c"]:
        conn.execute("UPDATE groups SET in_broadcast=1, skip_reason='' WHERE id=?", (gid,))
        return
    conn.execute(
        "UPDATE groups SET in_broadcast=0, skip_reason=? WHERE id=?",
        (reason or "no_send", gid))


def flood_guard(conn, account):
    row = conn.execute("SELECT flood_until FROM accounts WHERE name=?", (account,)).fetchone()
    if row and row["flood_until"] and row["flood_until"] > now_iso():
        sys.exit(f"Аккаунт «{account}» в cooldown до {row['flood_until']} — "
                 f"подожди окончания паузы; ту же очередь другому аккаунту не передавай")


def today_sent(conn, account, kind):
    return conn.execute(
        "SELECT COUNT(*) c FROM sent s JOIN campaigns c ON c.id=s.campaign "
        "WHERE s.account=? AND s.status='ok' AND c.kind=? AND s.ts>=?",
        (account, kind, now_iso()[:10] + "T00:00:00")).fetchone()["c"]


def today_dm_sent(conn, account):
    return today_sent(conn, account, "dm")


def today_broadcast_sent(conn, account):
    return today_sent(conn, account, "broadcast")


def trim_to_cap(rows, cap, used):
    """Обрезать выборку дневным потолком. cap=0 — без лимита, left=None."""
    if not cap:
        return list(rows), None
    left = max(0, int(cap) - int(used))
    return list(rows)[:left], left


def daily_cap(conn, account, kind):
    col = "daily_budget" if kind == "dm" else "broadcast_budget"
    bud = conn.execute(f"SELECT {col} AS c FROM accounts WHERE name=?", (account,)).fetchone()
    if not bud or bud["c"] is None:
        return 30 if kind == "dm" else 15
    return bud["c"]


def get_client(account="main", allow_flood=False):
    load_env()
    if not ACCOUNTS_RE.fullmatch(account):
        sys.exit("Некорректное имя аккаунта: a-z, 0-9, _ , - до 32 символов")
    conn = connect()
    row = conn.execute("SELECT * FROM accounts WHERE name=?", (account,)).fetchone()
    if row and not row["enabled"]:
        conn.close()
        sys.exit(f"Аккаунт «{account}» отключён — bot.py accounts enable {account}")
    if row and not allow_flood:
        flood_guard(conn, account)
    session = (row["session"] if row and row["session"]
               else str(SESSION_DIR / f"{account}.session"))
    # приоритет секретов: колонки реестра > env API_ID_<NAME> / API_ID_<число из имени>
    # (work2 -> API_ID_WORK2, затем API_ID_2) > общий API_ID из .env
    tag = re.sub(r"[^A-Z0-9_]", "_", account.upper())
    m = re.search(r"(\d+)$", account)
    digit = m.group(1) if m else None

    def _env_secret(base):
        keys = [f"{base}_{tag}"] + ([f"{base}_{digit}"] if digit else [])
        for k in keys:
            v = os.environ.get(k) or ENV.get(k)
            if v:
                return v
        return None

    api_id = (row["api_id"] if row and row["api_id"]
              else _env_secret("API_ID") or ENV.get("API_ID"))
    api_hash = (row["api_hash"] if row and row["api_hash"]
                else _env_secret("API_HASH") or ENV.get("API_HASH"))
    conn.close()
    if not api_id or not api_hash:
        sys.exit("Нет API_ID/API_HASH. Заведи приложение на my.telegram.org и "
                 "заполни .env (см. README.md).")
    from telethon import TelegramClient
    p = Path(session)
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(p), int(api_id), api_hash)


async def require_auth(client):
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("Сессия не авторизована — сначала выполните: python bot.py login [--account NAME]")


def read_message(args):
    if getattr(args, "text", None):
        return args.text
    if getattr(args, "file", None):
        return Path(args.file).read_text(encoding="utf-8")
    if getattr(args, "media", None):
        return ""  # медиа без подписи — тоже валидная кампания
    sys.exit("Укажите сообщение: --text \"...\", --file msg.txt или --media <id из библиотеки>")


# ---------------------------------------------------------------- commands

async def cmd_login(args):
    client = get_client(args.account, allow_flood=True)
    phone = args.phone or (lambda: input(f"Номер телефона для «{args.account}»: "))
    await client.start(phone=phone)
    me = await client.get_me()
    path = getattr(client.session, "filename", None) or str(SESSION_DIR / f"{args.account}.session")
    conn = connect()
    clash = conn.execute("SELECT name FROM accounts WHERE session=? AND name<>?",
                         (path, args.account)).fetchone()
    if clash:
        conn.close()
        await client.disconnect()
        sys.exit(f"session-файл уже привязан к аккаунту «{clash['name']}» — "
                 f"Telethon запрещает два клиента на одном session (one session = one client)")
    conn.execute(
        "INSERT INTO accounts(name,session,user_id,note,added_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET session=excluded.session, user_id=excluded.user_id, "
        "note=excluded.note",
        (args.account, path, me.id, f"login {now_iso()}", now_iso()))
    conn.commit()
    conn.close()
    name = " ".join(filter(None, [me.first_name, me.last_name]))
    print(f"ОК, аккаунт «{args.account}»: {name} @{me.username or '-'} (id={me.id}), session={path}")


def upsert_mailing_group(conn, account, ent):
    """Запомнить чат. Недостижимый не остаётся в рассылке, если писать некому."""
    can, why = classify_broadcast(ent)
    gtype = ("channel" if getattr(ent, "broadcast", False) else
             "megagroup" if getattr(ent, "megagroup", False) else "group")
    gid = ent.id
    title = getattr(ent, "title", "") or ""
    username = getattr(ent, "username", None) or ""
    cur = conn.execute(
        "INSERT OR IGNORE INTO groups(id,title,username,gtype,added_at,in_broadcast,skip_reason) "
        "VALUES(?,?,?,?,?,?,?)",
        (gid, title, username, gtype, now_iso(), 1 if can else 0, "" if can else why))
    added = cur.rowcount
    conn.execute(
        "UPDATE groups SET title=?, username=?, gtype=? WHERE id=?",
        (title, username, gtype, gid))
    record_edge(conn, account, gid, can_write=1 if can else 0)
    refresh_group_flags(conn, gid, why)
    return added, can, why


async def cmd_groups(args):
    conn = connect()
    if args.list:
        rows = conn.execute("SELECT * FROM groups ORDER BY added_at").fetchall()
        if not rows:
            print("Список пуст. Добавьте группы: python bot.py groups --sync")
        cov = {r["group_id"]: r["cw"] for r in conn.execute(
            "SELECT group_id, MAX(can_write) cw FROM account_groups GROUP BY group_id")}
        for r in rows:
            flag = "в рассылке" if r["in_broadcast"] else "пауза"
            if r["skip_reason"]:
                flag += " · " + REASON_RU.get(r["skip_reason"], r["skip_reason"])
            cw = cov.get(r["id"])
            if cw is not None:
                flag += " · писать: " + ("✓" if cw else "✗ нет прав ни у кого")
            print(f"  {r['id']:<15} [{r['gtype']:<10}] {r['title'] or ''} "
                  f"(@{r['username'] or '-'}) {flag}")
        return
    if args.probe:
        client = get_client(args.account)
        await require_auth(client)
        ok_cnt, bad_cnt = await probe_apply(client, args.account, conn, keep=args.keep)
        mode = "только показ (keep)" if args.keep else "в рассылке оставлены только проходимые"
        print(f"Probe «{args.account}»: можно писать {ok_cnt}, закрыто {bad_cnt} — {mode}.")
        print("  Группа, закрытая этим аккаунтом, но открытая другому, останется в рассылке "
              "после probe других аккаунтов (conduct выбирает того, у кого есть право).")
        await client.disconnect()
        return
    if not args.sync:
        sys.exit("Выберите действие: groups --sync / --list / --probe [--keep]")
    client = get_client(args.account)
    await require_auth(client)
    from telethon.tl.types import Chat, Channel
    me = await client.get_me()
    conn.execute("UPDATE accounts SET user_id=?, note=? WHERE name=?",
                 (me.id, f"sync {now_iso()}", args.account))
    conn.commit()
    added = 0
    async for d in client.iter_dialogs():
        ent = d.entity
        if isinstance(ent, (Chat, Channel)):
            n, can, why = upsert_mailing_group(conn, args.account, ent)
            if n:
                added += 1
                mark = "" if can else f" (не цель: {REASON_RU.get(why, why)})"
                print(f"  + {ent.id} {ent.title}{mark}")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) c FROM groups").fetchone()["c"]
    print(f"Добавлено новых групп: {added} (всего в списке: {total})")


async def cmd_watch(args):
    """Авто-добавление: группа регистрируется в списке рассылки при первом
    полученном из неё сообщении; "стоп"-ответ в DM заносит в blacklist."""
    conn = connect()
    client = get_client(args.account)
    await require_auth(client)
    from telethon import events
    from telethon.tl.types import Chat, Channel

    async def on_msg(e):
        if e.is_private:
            txt = (e.raw_text or "").strip()
            if STOP_RE.match(txt):
                who = await e.get_sender()
                if not who or not getattr(who, "id", None):
                    return  # отправитель недоступен (удалённый аккаунт и т.п.)
                conn.execute("INSERT OR IGNORE INTO blacklist(user_id,username,reason,ts) "
                             "VALUES(?,?,?,?)",
                             (who.id, who.username or "", "самоотписка в DM", now_iso()))
                conn.commit()
                print(f"[blacklist] {who.id} @{who.username or '-'}: «{txt[:40]}»")
                if not args.noreply:
                    await e.reply("Добавил вас в чёрный список, больше не пишу.")
            return
        chat = await e.get_chat()
        if isinstance(chat, (Chat, Channel)):
            fresh = not conn.execute("SELECT 1 FROM groups WHERE id=?", (chat.id,)).fetchone()
            added, can, why = upsert_mailing_group(conn, args.account, chat)
            conn.commit()
            if fresh:
                note = "" if can else f", не цель: {REASON_RU.get(why, why)}"
                print(f"[авто+] группа {chat.id} «{chat.title}» "
                      f"(аккаунт «{args.account}»){note}")
            elif added:
                print(f"[авто+] группа {chat.id} «{chat.title}» добавлена "
                      f"(аккаунт «{args.account}»)")

    client.add_event_handler(on_msg, events.NewMessage())
    print("watch запущен: новые группы добавляются автоматически, «стоп» в DM -> blacklist. Ctrl+C — выход.")
    await client.run_until_disconnected()


def username_is_bot(username):
    """Ник Telegram-бота обязан заканчиваться на bot. Имена вроде Subbotina и Rabota — нет."""
    return bool(username) and str(username).lower().endswith("bot")


def member_is_bot(u):
    return int(bool(getattr(u, "bot", False)) or username_is_bot(getattr(u, "username", None)))


def _field(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        if key not in row or row[key] is None:
            return default
        return row[key]
    try:
        keys = row.keys()
    except AttributeError:
        keys = None
    if keys is not None and key not in keys:
        return default
    try:
        val = row[key]
    except (KeyError, IndexError):
        return default
    if val is None:
        return default
    return val


def _admin_can_post(entity):
    if getattr(entity, "creator", False):
        return True
    rights = getattr(entity, "admin_rights", None)
    if not rights:
        return False
    return bool(getattr(rights, "post_messages", False))


def classify_broadcast(entity):
    """Можно ли этому аккаунту писать в чат. (ok, reason). reason пустой, если можно.

    Канал — цель только у создателя или админа с post_messages.
    Обычный чат закрыт, если аккаунт вышел, заглушён лично или сообщения запрещены всем.
    """
    if entity is None:
        return False, "not_member"
    if getattr(entity, "left", False):
        return False, "left"
    if getattr(entity, "kicked", False):
        return False, "kicked"
    banned = getattr(entity, "banned_rights", None)
    if banned and getattr(banned, "send_messages", False):
        return False, "muted"
    if getattr(entity, "broadcast", False):
        if _admin_can_post(entity):
            return True, ""
        return False, "channel_subscriber"
    default_rights = getattr(entity, "default_banned_rights", None)
    if (default_rights and getattr(default_rights, "send_messages", False)
            and not _admin_can_post(entity)):
        return False, "no_send"
    return True, ""


def classify_dm(row, self_id=None, blacklisted=False, already_sent=False):
    """Можно ли ставить строку members в очередь DM. (ok, reason)."""
    if row is None:
        return False, "no_address"
    username = _field(row, "username", "") or ""
    if int(_field(row, "is_bot", 0) or 0) or username_is_bot(username):
        return False, "bot"
    if blacklisted:
        return False, "blacklist"
    uid = _field(row, "user_id", None)
    if self_id is not None and uid == self_id:
        return False, "self"
    if int(_field(row, "deleted", 0) or 0):
        return False, "deleted"
    if already_sent:
        return False, "already_sent"
    block = str(_field(row, "dm_block", "") or "").strip()
    if block:
        return False, block
    if not str(username).strip() and _field(row, "access_hash", None) is None:
        return False, "no_address"
    return True, ""


def slowmode_exceeds(entity, remaining_seconds):
    """True, если slowmode длиннее оставшегося окна кампании. Без окна не отсекаем."""
    if entity is None or remaining_seconds is None:
        return False
    seconds = int(getattr(entity, "slowmode_seconds", 0) or 0)
    return seconds > max(0, remaining_seconds)


SPAM_ERRORS = {"PeerFloodError", "UserRestrictedError"}
# Пауза аккаунта на время сервера. SlowModeWaitError сюда не входит: это лимит чата.
ACCOUNT_FLOOD_ERRORS = {"FloodWaitError", "FloodPremiumWaitError"}

PERMANENT_DM = {
    "UserPrivacyRestrictedError": "privacy",
    "UserIsBlockedError": "user_blocked",
    "YouBlockedUserError": "you_blocked",
    "InputUserDeactivatedError": "deleted",
    "UserDeactivatedError": "deleted",
    "UserDeactivatedBanError": "deleted",
    "PeerIdInvalidError": "no_address",
    "UsernameNotOccupiedError": "no_address",
    "UsernameInvalidError": "no_address",
    "UserIdInvalidError": "no_address",
}

PERMANENT_BROADCAST = {
    "ChatWriteForbiddenError": "no_send",
    "ChatAdminRequiredError": "no_send",
    "UserBannedInChannelError": "muted",
    "ChannelPrivateError": "not_member",
    "ChatRestrictedError": "restricted",
    "TopicClosedError": "topic_closed",
    "UserNotParticipantError": "not_member",
    "ChatGuestSendForbiddenError": "no_send",
    "ChatSendPlainForbiddenError": "no_send",
}

REASON_RU = {
    "channel_subscriber": "канал: подписчик, постить нельзя",
    "left": "выход из чата",
    "kicked": "исключён из чата",
    "muted": "личный запрет писать",
    "no_send": "чат закрыл сообщения",
    "not_member": "аккаунт не состоит",
    "restricted": "чат ограничен",
    "topic_closed": "тема закрыта",
    "bot": "бот",
    "deleted": "удалённый аккаунт",
    "no_address": "нет ника и access_hash",
    "privacy": "приватность: личка закрыта",
    "user_blocked": "получатель заблокировал",
    "you_blocked": "мы заблокировали получателя",
    "blacklist": "чёрный список",
    "already_sent": "уже писали",
    "self": "свой аккаунт",
    "slowmode": "slowmode длиннее окна кампании",
}


def _error_name(exc):
    return exc if isinstance(exc, str) else type(exc).__name__


def send_error_class(exc):
    """flood | slowmode | spam | other. PeerFlood — spam, не обычный fail цели."""
    name = _error_name(exc)
    if name in ACCOUNT_FLOOD_ERRORS:
        return "flood"
    if name == "SlowModeWaitError":
        return "slowmode"
    if name in SPAM_ERRORS:
        return "spam"
    return "other"


def permanent_reason(exc, kind):
    name = _error_name(exc)
    table = PERMANENT_DM if kind == "dm" else PERMANENT_BROADCAST
    return table.get(name)


def target_disposition(exc, kind):
    """Что делать с целью после ошибки отправки.

    defer_same_account — FloodWait, цель остаётся этому аккаунту.
    halt_dm_queue — PeerFlood на DM: очередь другим аккаунтам не отдаётся.
    halt_account — спам-сигнал на broadcast: молчит только этот аккаунт.
    remember — постоянный отказ цели.
    skip — slowmode, без пометки цели мёртвой.
    fail — прочая ошибка, попытка записана, очередь идёт дальше.
    """
    name = _error_name(exc)
    klass = send_error_class(name)
    if klass == "flood":
        return "defer_same_account"
    if klass == "spam":
        return "halt_dm_queue" if kind == "dm" else "halt_account"
    if klass == "slowmode":
        return "skip"
    if permanent_reason(name, kind):
        return "remember"
    return "fail"


def spam_pause_until(now=None):
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(hours=24)).isoformat(timespec="seconds")


def flood_pause_until(exc, now=None):
    """Пауза на число секунд от сервера. Минимум 1 с, чтобы не крутить пустой цикл."""
    now = now or datetime.now(timezone.utc)
    seconds = int(getattr(exc, "seconds", 0) or 0)
    if seconds < 1:
        seconds = 1
    return (now + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def ordered_pause(delay_min, delay_max):
    """Пара паузы для random.uniform. Перепутанные границы меняются местами."""
    lo = float(delay_min or 0)
    hi = float(delay_max or 0)
    if lo < 0:
        lo = 0.0
    if hi < 0:
        hi = 0.0
    if hi > 0 and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def apply_write_edge(conn, account, gid, can, why):
    """Записать право писать и заново решить, остаётся ли группа в рассылке."""
    record_edge(conn, account, gid, can_write=1 if can else 0)
    refresh_group_flags(conn, gid, "" if can else (why or "no_send"))


def handoff_broadcast(row, name, players, edges):
    """Кому отдать группу, если name уже не может писать. None — отдать некому."""
    gid = row["id"]
    for other in sorted(players):
        if other == name:
            continue
        if gid not in edges.get(other, ()):
            continue
        if remaining_cap(players[other]) <= 0:
            continue
        return other
    return None


def remember_permanent(conn, kind, key, reason, account=None):
    """Постоянный отказ больше не попадает в очередь."""
    if kind == "dm":
        conn.execute(
            "UPDATE members SET dm_block=? WHERE user_id=? AND COALESCE(dm_block, '')=''",
            (reason, int(key)))
        if reason == "deleted":
            conn.execute("UPDATE members SET deleted=1 WHERE user_id=?", (int(key),))
        return
    gid = int(key)
    if account:
        conn.execute(
            "UPDATE account_groups SET can_write=0, seen_at=? WHERE account=? AND group_id=?",
            (now_iso(), account, gid))
    refresh_group_flags(conn, gid, reason)


def dm_eligible_where(repeat_ok=False):
    """Предикат очереди новых DM. repeat_ok снимает только дедуп прошлых успешных DM."""
    parts = [
        "m.is_bot=0",
        "LOWER(COALESCE(m.username, '')) NOT LIKE '%bot'",
        "COALESCE(m.deleted, 0)=0",
        "(COALESCE(m.username, '') != '' OR m.access_hash IS NOT NULL)",
        "COALESCE(m.dm_block, '')=''",
        "m.user_id NOT IN (SELECT user_id FROM blacklist)",
        "m.user_id NOT IN (SELECT user_id FROM accounts WHERE user_id IS NOT NULL)",
    ]
    if not repeat_ok:
        parts.append(
            "m.user_id NOT IN (SELECT CAST(s.target AS INTEGER) FROM sent s "
            "JOIN campaigns c ON c.id=s.campaign WHERE c.kind='dm' AND s.status='ok')"
        )
    return " AND ".join(parts)


# Тот же предикат, что у dmsend по умолчанию: status и панель считают одну очередь.
DM_ELIGIBLE_WHERE = dm_eligible_where(False)


def save_member(conn, u, gid):
    uname = u.username or ""
    is_bot = member_is_bot(u)
    deleted = int(bool(getattr(u, "deleted", False)))
    access_hash = getattr(u, "access_hash", None)
    cur = conn.execute(
        "INSERT OR IGNORE INTO members(user_id,username,first_name,last_name,is_bot,collected_at,"
        "source_group,deleted,access_hash) VALUES(?,?,?,?,?,?,?,?,?)",
        (u.id, uname, u.first_name or "", u.last_name or "", is_bot, now_iso(), gid,
         deleted, access_hash))
    # INSERT OR IGNORE не обновляет уже существующую строку — иначе ник *bot
    # навсегда остаётся is_bot=0, если первый scrape записал его человеком.
    sets, params = [], []
    if is_bot:
        sets.append("is_bot=1")
    if deleted:
        sets.append("deleted=1")
    if access_hash is not None:
        sets.append("access_hash=?")
        params.append(int(access_hash))
    if sets:
        params.append(u.id)
        conn.execute(f"UPDATE members SET {', '.join(sets)} WHERE user_id=?", params)
    conn.execute("INSERT OR IGNORE INTO member_sources(user_id,group_id) VALUES(?,?)", (u.id, gid))
    return cur.rowcount


async def cmd_scrape(args):
    conn = connect()
    client = get_client(args.account)
    await require_auth(client)
    from telethon.tl.types import Chat, Channel
    from telethon.errors import FloodWaitError

    dialogs = await client.get_dialogs()
    wanted = {d.entity.id: d.entity for d in dialogs if isinstance(d.entity, (Chat, Channel))}
    ids = args.groups if args.groups else [r["id"] for r in
           conn.execute("SELECT id FROM groups WHERE active=1").fetchall()]
    if not ids:
        ids = list(wanted)
    total = 0
    for gid in ids:
        ent = wanted.get(gid)
        if ent is None:
            print(f"  пропуск: группа {gid} не найдена среди ваших диалогов")
            continue
        n = 0
        while True:
            try:
                async for u in client.iter_participants(ent):
                    n += save_member(conn, u, gid)
                break
            except FloodWaitError as e:
                conn.commit()
                if e.seconds > 600:  # часовые/суточные паузы — смысл ждать теряет
                    conn.commit()
                    sys.exit(f"FloodWait {e.seconds} c (>10 мин) — прерываю scrape; "
                             f"сохранённое не потеряно, смотри status и повтори scrape")
                print(f"  FloodWait {e.seconds} c в группе {gid} — жду и повторяю", flush=True)
                await asyncio.sleep(e.seconds + 1)
                continue
            except Exception as e:
                name = type(e).__name__
                if "AdminRequired" in name or "Forbidden" in name or "ChatWriteForbidden" in name:
                    print(f"  группа {gid} «{ent.title}»: список участников закрыт "
                          f"для не-админов ({name}) — пропущена")
                else:
                    print(f"  группа {gid} «{ent.title}»: ошибка {name}: {str(e)[:100]}")
                break
        conn.commit()
        print(f"  группа {gid} «{ent.title}»: новых участников {n}")
        total += n
        await asyncio.sleep(2)  # мягкая пауза между группами
    all_m = conn.execute("SELECT COUNT(*) c FROM members").fetchone()["c"]
    print(f"Готово: собрано {total} новых (всего в базе: {all_m})", flush=True)


def dm_targets(conn, group_id=None, repeat_ok=False, limit=None):
    """Очередь DM: боты, удалённые, без адреса, dm_block, blacklist и прошлые DM исключены.

    Пустой профиль с username или access_hash остаётся: его можно адресовать.
    Имена людей, внутри которых есть буквы bot (Subbotina, Rabota), не отсекаются.
    """
    q = f"SELECT m.* FROM members m WHERE {dm_eligible_where(repeat_ok)}"
    p = []
    if group_id:
        q += " AND m.user_id IN (SELECT user_id FROM member_sources WHERE group_id=?)"
        p.append(group_id)
    q += " ORDER BY m.collected_at"
    if limit:
        q += " LIMIT ?"
        p.append(limit)
    return conn.execute(q, p).fetchall()


def recent_ok_targets(conn, kind, hours=24):
    """Цели, которым успешно писали этого вида за последние N часов (все аккаунты)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return {str(r["target"]) for r in conn.execute(
        "SELECT DISTINCT s.target FROM sent s JOIN campaigns c ON c.id=s.campaign "
        "WHERE c.kind=? AND s.status='ok' AND s.ts>=?", (kind, cutoff))}


async def send_run(client, conn, kind, targets, text, args, resolve, account="main", media=None):
    """Общий цикл отправки: журнал sent с авторством аккаунта, FloodWait ->
    cooldown в реестре аккаунтов, рандом-паузы. media: путь к файлу (фото/видео) —
    тогда текст уходит подписью (caption)."""
    if args.dry_run:
        camp = None
    else:
        cur = conn.execute(
            "INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
            (kind, text, now_iso(), account))
        camp = cur.lastrowid
        conn.commit()
    done = ok = 0
    halt = False
    halt_reason = ""
    pause_lo, pause_hi = ordered_pause(
        getattr(args, "delay_min", 0), getattr(args, "delay_max", 0))
    for target in targets:
        label, peer, key = resolve(target)
        if args.dry_run:
            print(f"  [dry] -> {label}")
            continue
        if args.max_runs and done >= args.max_runs:
            print("  достигнут лимит --max-runs, стоп.")
            break
        if halt:
            break
        for attempt in (1, 2):
            try:
                if media:
                    await client.send_file(peer, media, caption=text or None,
                                           supports_streaming=True)
                else:
                    await client.send_message(peer, text)
                conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                             "VALUES(?,?,?,?,?)", (camp, key, "ok", now_iso(), account))
                ok += 1
                print(f"  ok  {label}")
                break
            except Exception as e:
                disp = target_disposition(e, kind)
                if disp == "defer_same_account":
                    fu = flood_pause_until(e)
                    conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, account))
                    conn.commit()
                    print(f"  {type(e).__name__} {int(getattr(e, 'seconds', 0) or 0)} c "
                          f"на аккаунте «{account}» (cooldown до {fu}). "
                          f"Цель не помечена мёртвой.")
                    halt = True
                    halt_reason = type(e).__name__
                    break
                if disp in ("halt_dm_queue", "halt_account"):
                    fu = spam_pause_until()
                    conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, account))
                    conn.commit()
                    print(f"  {type(e).__name__}: аккаунт «{account}» на паузе до {fu}. "
                          f"Цель не помечена мёртвой и другому аккаунту не отдаётся.")
                    halt = True
                    halt_reason = type(e).__name__
                    break
                status = f"fail:{type(e).__name__}"
                if disp == "skip":
                    status = "skip:slowmode"
                elif disp == "remember":
                    remember_permanent(conn, kind, key, permanent_reason(e, kind), account)
                conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                             "VALUES(?,?,?,?,?)", (camp, key, status, now_iso(), account))
                print(f"  fail {label}: {type(e).__name__}: {str(e)[:120]}")
                break
        conn.commit()
        done += 1
        if pause_hi > 0 and not halt and done < len(targets):
            await asyncio.sleep(random.uniform(pause_lo, pause_hi))
    suffix = " [dry-run, ничего не отправлено]" if args.dry_run else ""
    if halt:
        suffix += f" [остановлено: {halt_reason} — аккаунт в cooldown]"
    if args.dry_run:
        print(f"Сухой прогон ({kind}, «{account}»): целей {len(targets)}, отправлено 0{suffix}")
    else:
        print(f"Кампания #{camp} ({kind}, «{account}»): отправлено {ok}, попыток {done}{suffix}")
    return camp


def pick_target(emap, row):
    """Цель broadcast: объект из диалогов либо явный peer по типу чата.
    ВАЖНО: голый int Telethon трактует как PeerUser — канал/группу так слать нельзя."""
    from telethon.tl.types import PeerChannel, PeerChat
    ent = emap.get(row["id"])
    if ent is None:
        ent = (PeerChat(row["id"]) if row["gtype"] == "group"
               else PeerChannel(row["id"]))
    return ent


async def cmd_broadcast(args):
    account = args.account
    conn = connect()
    media = resolve_media(conn, getattr(args, "media", None))
    text = read_message(args)
    client = get_client(account)
    await require_auth(client)
    from telethon.tl.types import Chat, Channel
    dialogs = await client.get_dialogs()
    emap = {d.entity.id: d.entity for d in dialogs if isinstance(d.entity, (Chat, Channel))}

    def resolve(r):
        return f"группа {r['id']} «{r['title']}»", pick_target(emap, r), str(r["id"])

    q = "SELECT * FROM groups WHERE active=1 AND in_broadcast=1"
    p = []
    if args.ids:
        q += " AND id IN (%s)" % ",".join("?" * len(args.ids))
        p = args.ids
    rows = conn.execute(q, p).fetchall()
    # оркестр: аккаунт пишет только туда, где он реально состоит
    foreign = [r for r in rows if r["id"] not in emap]
    rows = [r for r in rows if r["id"] in emap]
    if foreign:
        print(f"  пропущено {len(foreign)} групп: аккаунт «{account}» в них не состоит")
    if not args.repeat_ok:
        recent = recent_ok_targets(conn, "broadcast", hours=24)
        dup = [r for r in rows if str(r["id"]) in recent]
        rows = [r for r in rows if str(r["id"]) not in recent]
        if dup:
            print(f"  пропущено {len(dup)} групп: туда уже писали <24 ч назад "
                  f"(любым аккаунтом; --repeat-ok отключает)")
    open_rows = []
    closed = 0
    for r in rows:
        ent = emap.get(r["id"])
        can, why = classify_broadcast(ent)
        if not can:
            closed += 1
            if not args.dry_run:
                apply_write_edge(conn, account, r["id"], False, why)
            print(f"  пропуск {r['id']} «{r['title']}»: {REASON_RU.get(why, why)}")
            continue
        if not args.dry_run:
            apply_write_edge(conn, account, r["id"], True, "")
        open_rows.append(r)
    if not args.dry_run:
        conn.commit()
    if closed:
        print(f"  не цель для «{account}»: {closed}")
    rows = open_rows
    if not args.dry_run:
        cap = daily_cap(conn, account, "broadcast")
        used = today_broadcast_sent(conn, account)
        trimmed, left = trim_to_cap(rows, cap, used)
        if left == 0:
            sys.exit(f"Дневной потолок групп «{account}» исчерпан ({used}/{cap}). "
                     f"Завтра продолжится сам; объём = accounts bbudget {account} N.")
        if left is not None and len(trimmed) < len(rows):
            print(f"потолок групп «{account}»: обрезаю выборку {len(rows)} -> {left} "
                  f"(сегодня ok {used}/{cap})")
        rows = trimmed
    if not rows:
        sys.exit("Нечего отправлять: список пуст или всё уже покрыто")
    print(f"Рассылка {len(rows)} групп(ы) аккаунтом «{account}»"
          + (f" + медиа #{args.media}" if getattr(args, "media", None) else "") + ":")
    await send_run(client, conn, "broadcast", rows, text, args, resolve=resolve,
                   account=account, media=media)


async def cmd_dmsend(args):
    account = args.account
    conn = connect()
    media = resolve_media(conn, getattr(args, "media", None))
    text = read_message(args)
    if args.delay_max < 10 and not args.dry_run:
        print("! Пауза <10 c между DM — высокий риск бана аккаунта.")
    client = get_client(account)
    await require_auth(client)
    rows = dm_targets(conn, group_id=args.group, repeat_ok=args.repeat_ok, limit=args.limit)
    me = await client.get_me()
    rows = [r for r in rows if r["user_id"] != me.id]  # себе не пишем
    if not args.dry_run:
        bud = conn.execute("SELECT daily_budget FROM accounts WHERE name=?", (account,)).fetchone()
        raw = bud["daily_budget"] if bud else None
        cap = 30 if raw is None else raw  # 0 = без лимита (не рекомендую); NULL = потолок по умолчанию
        if cap:
            used = today_dm_sent(conn, account)
            left = max(0, cap - used)
            if left == 0:
                sys.exit(f"Дневной DM-бюджет «{account}» исчерпан ({used}/{cap}). "
                         f"Завтра продолжится сам; объём = accounts budget {account} N.")
            if len(rows) > left:
                print(f"бюджет «{account}»: обрезаю выборку {len(rows)} -> {left} "
                      f"(сегодня ok {used}/{cap})")
                rows = rows[:left]
    if not rows:
        sys.exit("Нет доступных получателей (отправлены, в blacklist, боты, "
                 "удалённые или без адреса).")
    print(f"DM {len(rows)} получателям аккаунтом «{account}» (лимит за-run: {args.limit}):")
    await send_run(client, conn, "dm", rows, text, args,
                   resolve=lambda r: (f"@{r['username'] or '-'} id={r['user_id']}",
                                      r["user_id"], str(r["user_id"])),
                   account=account, media=media)


def cmd_accounts(args):
    conn = connect()
    op = args.op
    if op == "list":
        for a in conn.execute("SELECT * FROM accounts ORDER BY name"):
            used = today_dm_sent(conn, a["name"])
            bused = today_broadcast_sent(conn, a["name"])
            bcap = a["broadcast_budget"]
            flags = []
            if not a["enabled"]:
                flags.append("ВЫКЛ")
            if a["flood_until"] and a["flood_until"] > now_iso():
                flags.append(f"cooldown до {a['flood_until'][11:16]} UTC")
            print(f"  {a['name']:<14} id={a['user_id'] or '-':<12} "
                  f"DM сегодня {used:>3}/{a['daily_budget'] or '∞'}  "
                  f"группы {bused:>3}/{bcap if bcap else '∞'}  "
                  f"session={a['session']}  {' '.join(flags)}")
        return
    if op == "import":
        if not ACCOUNTS_RE.fullmatch(args.name or ""):
            sys.exit("некорректное имя аккаунта")
        if args.name == "main":
            sys.exit("main — зарезервирован")
        if conn.execute("SELECT 1 FROM accounts WHERE name=?", (args.name,)).fetchone():
            sys.exit(f"аккаунт «{args.name}» уже есть")
        src = Path(args.value or "")
        if not src.is_absolute():
            src = (Path.cwd() / src).resolve()
        if not src.exists() or src.suffix != ".session":
            sys.exit("нужен существующый файл *.session (Telethon)")
        if conn.execute("SELECT 1 FROM accounts WHERE session=?", (str(src),)).fetchone():
            sys.exit("этот session-файл уже привязан к другому аккаунту — "
                     "копия обязательна (один файл = один клиент)")
        SESSION_DIR.mkdir(exist_ok=True)
        dest = SESSION_DIR / f"{args.name}.session"
        shutil.copyfile(src, dest)
        conn.execute("INSERT INTO accounts(name,session,note,added_at) VALUES(?,?,?,?)",
                     (args.name, str(dest), f"import {now_iso()}", now_iso()))
        conn.commit()
        print(f"импортирован «{args.name}» (session={dest}). "
              f"Заполни user_id: bot.py groups --sync --account {args.name}")
        return
    name = args.name
    if not name or not ACCOUNTS_RE.fullmatch(name):
        sys.exit("нужно корректное имя аккаунта")
    if op == "enable":
        conn.execute("UPDATE accounts SET enabled=1 WHERE name=?", (name,))
    elif op == "disable":
        conn.execute("UPDATE accounts SET enabled=0 WHERE name=?", (name,))
    elif op == "secret":
        import getpass
        api_id = getpass.getpass(f"api_id для «{name}» (пусто = общий .env): ").strip()
        api_hash = getpass.getpass(f"api_hash для «{name}»: ").strip()
        conn.execute("UPDATE accounts SET api_id=?, api_hash=? WHERE name=?",
                     (api_id or None, api_hash or None, name))
    elif op == "budget":
        n = int(args.value)
        if not (0 <= n <= 500):
            sys.exit("budget: 0 (без лимита, не рекомендую) .. 500")
        conn.execute("UPDATE accounts SET daily_budget=? WHERE name=?", (n, name))
    elif op == "bbudget":
        n = int(args.value)
        if not (0 <= n <= 500):
            sys.exit("bbudget: 0 (без лимита, не рекомендую) .. 500")
        conn.execute("UPDATE accounts SET broadcast_budget=? WHERE name=?", (n, name))
    elif op == "remove":
        if name == "main":
            sys.exit("main не удаляю — отключи: accounts disable main")
        row = conn.execute("SELECT session FROM accounts WHERE name=?", (name,)).fetchone()
        conn.execute("DELETE FROM accounts WHERE name=?", (name,))
        if args.purge and row and row["session"]:
            p = Path(row["session"])
            for ext in ("", "-wal", "-shm"):
                f = Path(str(p) + ext)
                if f.exists():
                    f.unlink()
    else:
        sys.exit("unknown op")
    conn.commit()
    conn.close()
    print(f"{op}: ок ({name})")


PRIVACY_MAX = [
    ("InputPrivacyKeyPhoneNumber",       "номер телефона"),
    ("InputPrivacyKeyStatusTimestamp",   "время захода (last seen)"),
    ("InputPrivacyKeyProfilePhoto",      "фото профиля"),
    ("InputPrivacyKeyForwards",          "ссылка на аккаунт при пересылке"),
    ("InputPrivacyKeyChatInvite",        "добавление в чаты/каналы"),
    ("InputPrivacyKeyPhoneCall",         "звонки"),
    ("InputPrivacyKeyPhoneP2P",          "P2P-звонки (защита IP)"),
    ("InputPrivacyKeyAddedByPhone",      "добавление в контакты по номеру"),
    ("InputPrivacyKeyVoiceMessages",     "голосовые сообщения"),
]


async def privacy_apply(client, account, check=True):
    """Применить Nobody-набор к подключённому клиенту; одинаково для всех аккаунтов."""
    from telethon.tl import types, functions
    keycls = {getattr(types, k): label for k, label in PRIVACY_MAX}
    skip = []
    for key, label in keycls.items():
        try:
            await client(functions.account.SetPrivacyRequest(
                key=key(), rules=[types.InputPrivacyValueDisallowAll()]))
            print(f"    ok   {label}: Nobody")
        except Exception:
            applied = False
            for alt in (types.InputPrivacyValueDisallowContacts,
                        types.InputPrivacyValueAllowContacts):
                try:
                    await client(functions.account.SetPrivacyRequest(
                        key=key(), rules=[alt()]))
                    print(f"    ok   {label}: {alt.__name__.replace('InputPrivacyValue', '')} "
                          f"(DisallowAll не поддерживается этим ключом)")
                    applied = True
                    break
                except Exception:
                    continue
            if not applied:
                print(f"    fail {label}")
                skip.append(key)
    if check:
        for key, label in keycls.items():
            if key in skip:
                continue
            try:
                got = await client(functions.account.GetPrivacyRequest(key=key()))
                nobody = any(isinstance(r, types.PrivacyValueDisallowAll) or
                             isinstance(r, types.PrivacyValueDisallowContacts)
                             for r in got.rules)
                print(f"    check {label}: " + ("✓ скрыто" if nobody else "! не применилось"))
            except Exception:
                print(f"    check {label}: не прочитать")


async def privacy_snapshot(client):
    """Снимок приватности (label -> набор правил) для сверки аккаунтов."""
    from telethon.tl import types, functions
    snap = {}
    for k, label in PRIVACY_MAX:
        key = getattr(types, k)
        try:
            got = await client(functions.account.GetPrivacyRequest(key=key()))
            snap[label] = ",".join(sorted(type(r).__name__.replace('PrivacyValue', '')
                                          for r in got.rules))
        except Exception as e:
            snap[label] = f"err:{type(e).__name__}"
    return snap


async def probe_apply(client, account, conn, keep=True, verbose=True):
    """Проверить права записи во всех группах базы этим аккаунтом.
    Возвращает (ok, blocked). keep=True — не менять флаги groups."""
    from telethon.tl.types import Chat, Channel
    dialogs = await client.get_dialogs()
    emap = {d.entity.id: d.entity for d in dialogs
            if isinstance(d.entity, (Chat, Channel))}
    rows = conn.execute("SELECT * FROM groups ORDER BY title").fetchall()
    ok_cnt = bad_cnt = 0
    for r in rows:
        ent = emap.get(r["id"])
        can, why = classify_broadcast(ent)
        if can:
            ok_cnt += 1
        else:
            bad_cnt += 1
        if ent is None:
            conn.execute(
                "UPDATE account_groups SET can_write=0, seen_at=? WHERE account=? AND group_id=?",
                (now_iso(), account, r["id"]))
        else:
            record_edge(conn, account, r["id"], can_write=1 if can else 0)
        if not keep:
            if can:
                conn.execute("UPDATE groups SET active=1 WHERE id=?", (r["id"],))
            refresh_group_flags(conn, r["id"], why)
        if verbose:
            label = "можно писать" if can else REASON_RU.get(why, why)
            print(f"  {'✓' if can else '✗'} {r['id']:<15} {(r['title'] or '')[:46]:<48} {label}")
    conn.commit()
    return ok_cnt, bad_cnt


def fmt_size(n):
    n = n or 0
    for unit in ("B", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} ТБ"


def media_kind(mime):
    if not mime:
        return "file"
    if mime.startswith("image/"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    return "file"


def register_media(conn, account, msg_id, path_str, kind, title, mime, size):
    cur = conn.execute(
        "INSERT INTO media(account,msg_id,kind,title,mime,size,path,added_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (account, msg_id, kind, title, mime or "", size, path_str, now_iso()))
    conn.commit()
    return cur.lastrowid


def resolve_media(conn, value):
    """--media <id> -> локальный путь для отправки; None если не задано."""
    if value is None or value == "":
        return None
    row = conn.execute("SELECT * FROM media WHERE id=?", (int(value),)).fetchone()
    if not row:
        sys.exit(f"медиа #{value} нет в библиотеке (bot.py media list)")
    p = Path(row["path"]) if row["path"] else None
    if not p or not p.exists():
        sys.exit(f"файл медиа #{value} не на диске — восстанови: "
                 f"bot.py media sync {row['id']} --account {row['account']}")
    return str(p)


def cmd_cloud(args):
    """Облачное зеркало Supabase (push|status|test) — делегирует cloud.py."""
    code = subprocess.call([sys.executable, str(ROOT / "cloud.py"), args.op])
    sys.exit(code)


# команды, после которых стоит обновить зеркало при AUTO_CLOUD=1
CLOUD_HOOK = {"cmd_broadcast", "cmd_dmsend", "cmd_conduct", "cmd_scrape",
              "cmd_groups", "cmd_privacy", "cmd_align", "cmd_accounts",
              "cmd_media", "cmd_blacklist", "cmd_export"}


def maybe_cloud_push(args):
    if os.environ.get("AUTO_CLOUD") != "1":
        return
    if getattr(args.func, "__name__", "") not in CLOUD_HOOK:
        return
    try:
        subprocess.Popen([sys.executable, str(ROOT / "cloud.py"), "push"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


async def cmd_media(args):
    """Библиотека медиа поверх «Избранного» Telegram: add/pull/sync/del/list."""
    from telethon.tl import types as tl_types
    op = args.op
    conn = connect()
    if op == "list":
        rows = conn.execute("SELECT * FROM media ORDER BY id DESC").fetchall()
        if not rows:
            print("библиотека пуста: media add <файл> или media pull")
        for r in rows:
            ok = "✓" if r["path"] and Path(r["path"]).exists() else "✗нет файла→sync"
            print(f"  #{r['id']:<3} [{r['kind']:<5}] {(r['title'] or '-')[:44]:<46} "
                  f"{fmt_size(r['size']):>9}  ({r['account']}/{r['msg_id']})  {ok}")
        return
    if op == "del":
        if not args.value.isdigit():
            sys.exit("del: нужен id из media list")
        conn.execute("DELETE FROM media WHERE id=?", (int(args.value),))
        conn.commit()
        print(f"запись #{args.value} удалена (файл в media_cache не тронут)")
        return
    account = args.account
    if op in ("sync",):
        row = conn.execute("SELECT * FROM media WHERE id=?",
                           (int(args.value or 0) or None,) if args.value.isdigit() else (0,)).fetchone() \
            if args.value.isdigit() else None
        if not row:
            sys.exit("sync: нужен id из media list")
        if row["account"] != account:
            sys.exit(f"медиа #{row['id']} лежит в «Избранном» аккаунта «{row['account']}» — "
                     f"синхронизируй им: media sync {row['id']} --account {row['account']}")
    client = get_client(account)
    await require_auth(client)
    MEDIA_DIR.mkdir(exist_ok=True)
    if op == "add":
        src = Path(args.value)
        if not src.is_absolute():
            src = (Path.cwd() / src).resolve()
        if not src.exists():
            sys.exit("файл не найден: " + str(src))
        mime = mimetypes.guess_type(str(src))[0] or ""
        msg = await client.send_file("me", str(src),
                                     caption=f"tg-lib: {src.name}",
                                     supports_streaming=True)
        dest = MEDIA_DIR / f"lib_{msg.id}_{src.name}"
        shutil.copyfile(src, dest)
        mid = register_media(conn, account, msg.id, str(dest),
                             media_kind(mime), src.name, mime, src.stat().st_size)
        print(f"#{mid}: «{src.name}» → Избранное «{account}» (msg {msg.id}) + кэш {dest.name}")
    elif op == "pull":
        existing = {r["msg_id"] for r in conn.execute(
            "SELECT msg_id FROM media WHERE account=?", (account,))}
        n = 0
        async for m in client.iter_messages("me", limit=max(5, int(args.limit or 30))):
            if not m.media or m.id in existing:
                continue
            mime, orig = "", None
            if isinstance(m.media, tl_types.Photo) or isinstance(m.file, tl_types.Photo):
                mime = "image/jpeg"
            elif hasattr(m.file, "mime_type"):
                mime = m.file.mime_type or ""
                orig = getattr(m.file, "name", None)
            else:
                continue  # стикеры/голосовые в библиотеку не берём
            kind = media_kind(mime)
            dest = MEDIA_DIR / f"lib_{m.id}_{orig or (kind + str(m.id))}"
            try:
                got = await client.download_media(m, str(dest))
            except Exception as e:
                print(f"  msg {m.id}: не скачалось ({type(e).__name__})")
                continue
            # download_media дописывает своё расширение — вернувшийся путь и есть файл
            dest = Path(got) if got else dest
            if not dest.exists():
                print(f"  msg {m.id}: скачалось, но файла нет")
                continue
            register_media(conn, account, m.id, str(dest), kind,
                           orig or dest.name, mime, dest.stat().st_size)
            n += 1
            print(f"  подтянуто msg {m.id}: {orig or dest.name} [{kind}]")
        print(f"Pull «{account}»: новых медиа {n}")
    elif op == "sync":
        m = await client.get_messages("me", ids=row["msg_id"])
        if not m or not m.media:
            sys.exit("сообщение с медиа не найдено в Избранном (удалено?)")
        name = Path(row["path"]).name if row["path"] else f"{row['kind']}_{m.id}"
        dest = MEDIA_DIR / f"lib_{m.id}_{name}"
        got = await client.download_media(m, str(dest))
        dest = Path(got) if got else dest
        if not dest.exists():
            sys.exit("не удалось сохранить файл в кэш")
        conn.execute("UPDATE media SET path=?, size=? WHERE id=?",
                     (str(dest), dest.stat().st_size, row["id"]))
        conn.commit()
        print(f"#{row['id']}: кэш восстановлен → {dest}")
    await client.disconnect()


async def cmd_align(args):
    """Выровнять аккаунт по организму: догнать публичные группы из общей базы
    (с дневным лимитом вступлений — свежему аккаунту массовый join противопоказан),
    затем единый privacy-набор, затем probe. Идемпотентно: крути хоть каждый день."""
    account = args.account
    conn = connect()
    client = get_client(account)
    await require_auth(client)
    me = await client.get_me()
    dialogs = await client.get_dialogs()
    have = {d.entity.id for d in dialogs}
    rows = conn.execute("SELECT * FROM groups "
                        "WHERE username IS NOT NULL AND username<>'' ORDER BY added_at").fetchall()
    missing = [r for r in rows if r["id"] not in have]
    priv_rows = conn.execute("SELECT id,title FROM groups "
                             "WHERE (username IS NULL OR username='') "
                             "AND id NOT IN (SELECT group_id FROM account_groups WHERE account=?)",
                             (account,)).fetchall()
    print(f"«{account}» (id={me.id}): в базе {len(rows)+len(priv_rows)} групп, "
          f"не хватает {len(missing)} публичных + {len(priv_rows)} приватных")
    for r in priv_rows:
        print(f"  ⚠ приватная группа {r['id']} «{r['title'][:44]}» — вступить можно "
              f"только по инвайт-ссылке/через админа (API не умеет)")
    if args.dry_run:
        for r in missing[:args.limit]:
            print(f"  [dry] вступить: t.me/{r['username']} «{r['title'][:40]}»")
        if len(missing) > args.limit:
            print(f"  [dry] ...и ещё {len(missing)-args.limit} — по лимиту {args.limit}/запуск")
        print("[dry] затем: privacy (единый набор) + probe (права)")
        await client.disconnect()
        return
    from telethon.errors import FloodWaitError, UserAlreadyParticipantError
    from telethon.tl.functions.channels import JoinChannelRequest
    joined = 0
    for r in missing[:args.limit]:
        try:
            ent = await client(JoinChannelRequest(r["username"]))
            record_edge(conn, account, ent.id if getattr(ent, "id", None) else r["id"])
            conn.commit()
            joined += 1
            print(f"  + t.me/{r['username']} «{r['title'][:40]}»")
        except UserAlreadyParticipantError:
            record_edge(conn, account, r["id"])
            print(f"  = уже состоит: {r['username']}")
        except FloodWaitError as e:
            fu = (datetime.now(timezone.utc) + timedelta(seconds=e.seconds)).isoformat(timespec="seconds")
            conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, account))
            conn.commit()
            print(f"  ⏸ FloodWait {e.seconds} c на вступления — пауза до {fu[11:16]} UTC, "
                  f"догонишь позже (align повторит)")
            break
        except Exception as e:
            print(f"  ✗ {r['username']}: {type(e).__name__}: {str(e)[:70]}")
        await asyncio.sleep(random.uniform(args.delay_min, args.delay_max))
    left = len(missing) - joined
    print(f"Вступления: +{joined} за этот запуск (лимит {args.limit}); осталось {max(0,left)}")
    print("Приватность:")
    await privacy_apply(client, account)
    print("Probe прав:")
    ok, bad = await probe_apply(client, account, conn, keep=False)
    print(f"Итог align «{account}»: писать можно в {ok}, закрыто {bad}, "
          f"подтянуто групп {joined}")
    await client.disconnect()


async def cmd_compare(args):
    """Сверка двух аккаунтов: покрытие групп + приватность по ключам."""
    a, b = args.account, args.other
    conn = connect()
    states = {}
    for name in (a, b):
        client = get_client(name, allow_flood=True)
        await client.connect()
        if not await client.is_user_authorized():
            print(f"  «{name}»: НЕ авторизован — сначала login --account {name}")
            await client.disconnect()
            continue
        edges = {r["group_id"] for r in conn.execute(
            "SELECT group_id FROM account_groups WHERE account=?", (name,))}
        writable = {r["group_id"] for r in conn.execute(
            "SELECT group_id FROM account_groups WHERE account=? AND can_write=1", (name,))}
        states[name] = {"edges": edges, "writable": writable,
                        "priv": await privacy_snapshot(client)}
        await client.disconnect()
    if len(states) < 2:
        if states:
            n = next(iter(states))
            all_g = {r["id"] for r in conn.execute("SELECT id FROM groups")}
            print(f"  «{n}»: состоит в {len(states[n]['edges'])}/{len(all_g)} групп базы")
        return
    ea, eb = states[a]["edges"], states[b]["edges"]
    all_g = {r["id"] for r in conn.execute("SELECT id FROM groups")}
    print(f"Группы: база {len(all_g)} | «{a}» состоит {len(ea)} (пишет в {len(states[a]['writable'])})"
          f" | «{b}» состоит {len(eb)} (пишет в {len(states[b]['writable'])})")
    only_a = ea - eb
    only_b = eb - ea
    if only_a:
        t = conn.execute("SELECT title FROM groups WHERE id IN (%s)" %
                         ",".join("?" * len(only_a)), list(only_a)).fetchall()
        print(f"  только в «{a}» ({len(only_a)}): " + "; ".join(x["title"][:30] for x in t[:8]))
        print(f"  → догнать: bot.py align --account {b}")
    if only_b:
        print(f"  только в «{b}»: {len(only_b)}  → bot.py align --account {a} "
              f"(или groups --sync --account {a})")
    diff = [(k, states[a]["priv"][k], states[b]["priv"][k])
            for k in states[a]["priv"]
            if states[a]["priv"].get(k) != states[b]["priv"].get(k)]
    if diff:
        print("  Приватность РАЗЛИЧАЕТСЯ:")
        for k, x, y in diff:
            print(f"    {k}: {a}={x} | {b}={y}  → privacy --account <какой нужный>")
    else:
        print("  Приватность: идентична ✓")


async def cmd_privacy(args):
    """Максимальная конфиденциальность аккаунта(ов): Nobody по всем ключам."""
    conn = connect()
    if args.all:
        names = [r["name"] for r in conn.execute(
            "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    else:
        names = [args.account]
    for n in names:
        client = get_client(n)
        await require_auth(client)
        me = await client.get_me()
        print(f"  «{n}» ({me.first_name}):")
        await privacy_apply(client, n, check=not args.no_check)
        await client.disconnect()
    print("Готово. Учти: скрытый last seen = и ты не видишь чужой точно; "
          "скрытые инвайты — только админы вручную (через ссылку-приглашение "
          "или по юзернейму тебя добавить смогут, где это разрешено настройками чата).")


def cmd_export(args):
    conn = connect()
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    g = out / "groups.csv"
    with g.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "username", "type", "added_at", "active", "in_broadcast"])
        for r in conn.execute("SELECT id,title,username,gtype,added_at,active,in_broadcast "
                              "FROM groups ORDER BY added_at"):
            w.writerow(list(r))
    m = out / "members.csv"
    with m.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "username", "first_name", "last_name",
                    "is_bot", "collected_at", "groups"])
        for r in conn.execute(
                "SELECT user_id,username,first_name,last_name,is_bot,collected_at, "
                "(SELECT GROUP_CONCAT(group_id) FROM member_sources ms "
                " WHERE ms.user_id=members.user_id) AS groups "
                "FROM members ORDER BY collected_at"):
            w.writerow(list(r))
    b = out / "blacklist.csv"
    with b.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "username", "reason", "ts"])
        for r in conn.execute("SELECT user_id,username,reason,ts FROM blacklist"):
            w.writerow(list(r))
    print(f"Экспорт: {g} | {m} | {b}")


def cmd_status(args):
    conn = connect()
    c = lambda t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    print(f"БД: {DB_PATH}")
    print(f"  групп в списке: {c('groups')} (активных в рассылке: "
          f"{conn.execute('SELECT COUNT(*) c FROM groups WHERE active=1 AND in_broadcast=1').fetchone()['c']})")
    print(f"  участников собрано: {c('members')}  (ботов: "
          f"{conn.execute('SELECT COUNT(*) c FROM members WHERE is_bot=1').fetchone()['c']})")
    print(f"  в blacklist: {c('blacklist')}")
    for a in conn.execute(
            "SELECT name,enabled,user_id,daily_budget,broadcast_budget,flood_until "
            "FROM accounts ORDER BY name"):
        used = today_dm_sent(conn, a["name"])
        bused = today_broadcast_sent(conn, a["name"])
        mark = "" if a["enabled"] else " [ВЫКЛ]"
        fl = " [cooldown]" if a["flood_until"] and a["flood_until"] > now_iso() else ""
        bcap = a["broadcast_budget"] if a["broadcast_budget"] else "∞"
        print(f"  аккаунт «{a['name']}»: id={a['user_id'] or '?'} "
              f"DM сегодня {used}/{a['daily_budget'] or '∞'}  "
              f"группы {bused}/{bcap}{mark}{fl}")
    print(f"  кампаний: {c('campaigns')}")
    print("  последние отправки:")
    for s in conn.execute("SELECT s.campaign,c.kind,s.target,s.status,s.ts FROM sent s "
                          "JOIN campaigns c ON c.id=s.campaign "
                          "ORDER BY s.ts DESC LIMIT 5"):
        print(f"    #{s['campaign']} {s['kind']} -> {s['target']}: {s['status']} ({s['ts']})")
    free = conn.execute(
        f"SELECT COUNT(*) c FROM members m WHERE {DM_ELIGIBLE_WHERE}").fetchone()["c"]
    print(f"  доступных для новых DM: {free}")
    print("  группы вне рассылки:")
    outside = conn.execute(
        "SELECT COALESCE(NULLIF(skip_reason, ''), '(без причины)') reason, COUNT(*) c "
        "FROM groups WHERE COALESCE(in_broadcast, 0)=0 GROUP BY 1 ORDER BY c DESC").fetchall()
    if not outside:
        print("    нет")
    for r in outside:
        print(f"    {r['c']:>5}  {REASON_RU.get(r['reason'], r['reason'])}")
    stale = conn.execute(
        "SELECT COUNT(*) c FROM groups g WHERE COALESCE(g.in_broadcast, 0)=0 "
        "AND COALESCE(g.skip_reason, '')='' "
        "AND EXISTS (SELECT 1 FROM account_groups ag "
        " WHERE ag.group_id=g.id AND ag.can_write=1)").fetchone()["c"]
    if stale:
        print(f"    {stale} из них с пустой причиной, но can_write=1 у аккаунта — "
              f"в рассылку сами не вернутся; перепроверь: bot.py groups --probe --keep")
    print("  участники вне очереди DM:")
    buckets = conn.execute(
        "SELECT reason, COUNT(*) c FROM ("
        " SELECT CASE"
        "  WHEN m.is_bot=1 OR LOWER(COALESCE(m.username, '')) LIKE '%bot' THEN 'bot'"
        "  WHEN COALESCE(m.deleted, 0)=1 THEN 'deleted'"
        "  WHEN COALESCE(m.dm_block, '')!='' THEN m.dm_block"
        "  WHEN COALESCE(m.username, '')='' AND m.access_hash IS NULL THEN 'no_address'"
        "  WHEN m.user_id IN (SELECT user_id FROM blacklist) THEN 'blacklist'"
        "  WHEN m.user_id IN (SELECT CAST(s.target AS INTEGER) FROM sent s "
        "       JOIN campaigns c ON c.id=s.campaign WHERE c.kind='dm' AND s.status='ok')"
        "       THEN 'already_sent'"
        "  WHEN m.user_id IN (SELECT user_id FROM accounts WHERE user_id IS NOT NULL) THEN 'self'"
        "  ELSE 'in_queue' END reason FROM members m"
        ") GROUP BY reason ORDER BY c DESC").fetchall()
    shown = False
    for r in buckets:
        if r["reason"] == "in_queue":
            continue
        shown = True
        print(f"    {r['c']:>5}  {REASON_RU.get(r['reason'], r['reason'])}")
    if not shown:
        print("    нет")


def cmd_blacklist(args):
    conn = connect()
    try:
        if args.cmd == "list":
            rows = conn.execute("SELECT * FROM blacklist").fetchall()
            for r in rows:
                print(f"  {r['user_id']} @{r['username'] or '-'} ({r['reason']})")
            if not rows:
                print("  пусто")
            return
        v = args.value.lstrip("@")
        if v.isdigit():
            uid, uname = int(v), ""
        else:
            row = conn.execute("SELECT user_id, username FROM members "
                               "WHERE lower(username)=lower(?)", (v,)).fetchone()
            if not row:
                sys.exit(f"Ник @{v} не найден в базе — добавляй по user_id "
                         f"или сначала выполни scrape")
            uid, uname = row["user_id"], row["username"]
        conn.execute("INSERT OR IGNORE INTO blacklist(user_id,username,reason,ts) VALUES(?,?,?,?)",
                     (uid, uname, args.reason, now_iso()))
        conn.commit()
        print(f"занесён: id={uid} @{uname or '-'}")
    finally:
        conn.close()


def cooldown_remaining(p, now_dt=None):
    """Секунды до конца паузы аккаунта (0 = свободен). Чистая функция."""
    fu = p.get("flood_until")
    if not fu:
        return 0
    now_dt = now_dt or datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(fu)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        rem = (t - now_dt).total_seconds()
        return int(rem) if rem > 0 else 0
    except (TypeError, ValueError):
        return 0


def remaining_cap(player):
    if player.get("budget"):
        return max(0, player["budget"] - player["used"])
    return 10 ** 9


def assign_targets(kind, rows, players, edges=None):
    """Раскладка без пересечений: эксклюзивные чаты своему аккаунту, общие — у кого больше слотов."""
    free = {n: p for n, p in players.items() if not cooldown_remaining(p)}
    caps = {n: remaining_cap(p) for n, p in free.items()}
    planned = {n: 0 for n in caps}
    out = []

    def take(candidates):
        eligible = [n for n in candidates if caps.get(n, 0) > 0]
        if not eligible:
            return None
        who = min(eligible, key=lambda n: (-caps[n], planned[n], n))
        caps[who] -= 1
        planned[who] += 1
        return who

    if kind == "broadcast" and edges is not None:
        exclusive, shared, nobody = [], [], []
        for row in rows:
            cands = [n for n in caps if row["id"] in edges.get(n, ())]
            if len(cands) == 1:
                exclusive.append((row, cands))
            elif len(cands) > 1:
                shared.append((row, cands))
            else:
                nobody.append(row)
        for row, cands in exclusive + shared:
            out.append((take(cands), row))
        for row in nobody:
            out.append((None, row))
    else:
        order = sorted(caps)
        i = 0
        for row in rows:
            who = None
            for _ in range(max(len(order), 1)):
                if not order:
                    break
                n = order[i % len(order)]
                i += 1
                if caps.get(n, 0) > 0:
                    who = take([n])
                    break
            out.append((who, row))
    return out, sum(1 for who, _ in out if who is None)


def preview_assign(kind, rows, players, edges=None, limit_show=30):
    """Сухой план раскладки целей по аккаунтам (для --dry-run и тестов)."""
    plan, unplanned = assign_targets(kind, rows, players, edges)
    shown = plan if limit_show is None else plan[:limit_show]
    return shown, unplanned


async def cmd_conduct(args):
    """Дирижёр: одна кампания на все включённые аккаунты. Общий организм:
    единая база целей/журнала/чёрного списка. FloodWait и FloodPremiumWait
    оставляют текущую цель этому аккаунту. PeerFlood на DM останавливает очередь
    и не отдаёт её другим.
    Дневные бюджеты считаются на аккаунт; группу отправляет тот, кто может писать."""
    import time as _t
    conn = connect()
    media = resolve_media(conn, getattr(args, "media", None))
    text = read_message(args)
    kind = args.what
    names = args.accounts or [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    from telethon.tl.types import Chat, Channel

    players = {}
    for n in names:
        try:
            c = get_client(n, allow_flood=True)  # cooldown'ами рулит дирижёр
            await c.connect()
            if not await c.is_user_authorized():
                print(f"  пропуск «{n}»: сессия не авторизована — login --account {n}")
                continue
            me = await c.get_me()
            conn.execute("UPDATE accounts SET user_id=? WHERE name=?", (me.id, n))
            arow = conn.execute(
                "SELECT daily_budget, broadcast_budget, flood_until FROM accounts WHERE name=?",
                (n,)).fetchone()
            if kind == "dm":
                raw = arow["daily_budget"] if arow else None
                budget = 30 if raw is None else raw
                used = today_dm_sent(conn, n)
            else:
                budget = (arow["broadcast_budget"] if arow and arow["broadcast_budget"] is not None
                          else 15)
                used = today_broadcast_sent(conn, n)
            players[n] = {"client": c, "me": me.id, "budget": budget, "used": used,
                          "flood_until": arow["flood_until"] if arow else None}
            rem = cooldown_remaining(players[n])
            cap_label = players[n]["budget"] or "∞"
            print(f"  игрок «{n}» (id={me.id}): "
                  + (f"пауза ещё {rem} c" if rem else "готов")
                  + f", {kind} сегодня {players[n]['used']}/{cap_label}")
        except SystemExit as e:
            print(f"  пропуск «{n}»: {e}")
        except Exception as e:
            print(f"  пропуск «{n}»: {type(e).__name__}: {str(e)[:80]}")
    conn.commit()
    if not players:
        sys.exit("Ни один аккаунт не подключён — login --account NAME")

    dmin, dmax = ordered_pause(args.delay_min, args.delay_max)
    if dmax <= 0:
        dmin, dmax = (20.0, 45.0) if kind == "dm" else (3.0, 8.0)
    if kind == "dm" and not args.dry_run and dmax < 10:
        print("! Пауза <10 c между DM — высокий риск бана аккаунта.")

    edges = {}
    if kind == "broadcast":
        emaps = {}
        for n, p in players.items():
            dialogs = await p["client"].get_dialogs()
            emaps[n] = {d.entity.id: d.entity for d in dialogs
                        if isinstance(d.entity, (Chat, Channel))}
            writable = set()
            for gid, ent in emaps[n].items():
                can, why = classify_broadcast(ent)
                if can:
                    writable.add(gid)
                    if args.dry_run:
                        # Сухой прогон не возвращает чат в рассылку. Новую строку
                        # покрытия запоминаем, уже записанный can_write=0 не затираем.
                        record_edge(conn, n, gid)
                    else:
                        apply_write_edge(conn, n, gid, True, "")
                else:
                    apply_write_edge(conn, n, gid, False, why)
            edges[n] = writable
            print(f"  покрытие «{n}»: диалогов {len(emaps[n])}, можно писать {len(writable)}")
        conn.commit()
        q = "SELECT * FROM groups WHERE active=1 AND in_broadcast=1"
        prm = []
        if args.ids:
            q += " AND id IN (%s)" % ",".join("?" * len(args.ids))
            prm = args.ids
        recent = set() if args.repeat_ok else recent_ok_targets(conn, "broadcast", 24)
        fetched = conn.execute(q, prm).fetchall()
        rows = [r for r in fetched if str(r["id"]) not in recent]
        covered_any = [r for r in rows if any(r["id"] in e for e in edges.values())]
        print(f"broadcast: групп в работе {len(covered_any)}, без права писать "
              f"{len(rows) - len(covered_any)}")
        rows = covered_any
        if args.limit:
            rows = rows[:args.limit]
        emap_all = emaps
    else:
        rows = dm_targets(conn, group_id=args.group, repeat_ok=args.repeat_ok)
        me_ids = {p["me"] for p in players.values()}
        rows = [r for r in rows if r["user_id"] not in me_ids]  # операторам не пишем
        if args.limit:
            rows = rows[:args.limit]

    plan, unplanned = assign_targets(
        kind, rows, players, edges if kind == "broadcast" else None)
    by_acc = {}
    exclusive = {n: 0 for n in players}
    shared_n = 0
    for who, row in plan:
        if who:
            by_acc[who] = by_acc.get(who, 0) + 1
        if kind == "broadcast":
            holders = [n for n in players if row["id"] in edges.get(n, ())]
            if len(holders) == 1 and who:
                exclusive[who] += 1
            elif len(holders) > 1:
                shared_n += 1
    print("раскладка: " + ", ".join(f"«{n}» {by_acc.get(n, 0)}" for n in players)
          + (f", общие чаты {shared_n}" if kind == "broadcast" else "")
          + f", без слота {unplanned}")
    if kind == "broadcast":
        print("  только у этого аккаунта: " +
              ", ".join(f"«{n}» {exclusive[n]}" for n in players))

    if args.dry_run:
        for who, row in plan:
            if kind == "broadcast":
                print(f"  [dry] {who or 'НИКОМУ'}: группа {row['id']} «{row['title'][:44]}»")
            else:
                print(f"  [dry] {who or 'НЕТ БЮДЖЕТА'}: @{row['username'] or '-'} id={row['user_id']}")
        print(f"Сухой план: целей {len(rows)}, без покрытия/бюджета {unplanned}; "
              f"аккаунтов {len(players)} — ничего не отправлено")
        for p in players.values():
            await p["client"].disconnect()
        return

    if not any(who for who, _ in plan):
        for p in players.values():
            await p["client"].disconnect()
        sys.exit("Нет целей (всё отправлено/покрыто/в blacklist/бюджеты)")

    cur = conn.execute("INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
                       (kind, text, now_iso(), "conductor"))
    camp = cur.lastrowid
    conn.commit()
    queues = {n: collections.deque() for n in players}
    for who, row in plan:
        if who:
            queues[who].append(row)
    deadline = _t.monotonic() + args.window * 3600
    stats = {"ok": 0, "fail": 0}
    stop_dm = asyncio.Event()
    deferred = {}

    def _handoff(row, name):
        """Снять чат с этого аккаунта и отдать тому, кто ещё может писать.
        Снятие до поиска не даёт двум аккаунтам перекидывать одну группу по кругу."""
        gid = row["id"]
        held = edges.get(name)
        if held is not None:
            held.discard(gid)
        other = handoff_broadcast(row, name, players, edges)
        if not other:
            return False
        queues[other].append(row)
        print(f"  передаю группу {gid} «{row['title'][:40]}» {name} → {other}")
        return True

    async def worker(name):
        p = players[name]
        skipped_groups = set()
        while _t.monotonic() < deadline and not stop_dm.is_set():
            rem = cooldown_remaining(p)
            if rem:
                await asyncio.sleep(min(rem + 1, 30))
                continue
            if p["budget"] and p["used"] >= p["budget"]:
                print(f"  «{name}»: дневной потолок {kind} исчерпан "
                      f"({p['used']}/{p['budget']}) — отдыхает до завтра")
                return
            row = deferred.pop(name, None)
            if row is None:
                if stop_dm.is_set() or not queues[name]:
                    return
                row = queues[name].popleft()
            if kind == "broadcast":
                gid = row["id"]
                if gid not in edges.get(name, ()) or gid in skipped_groups:
                    _handoff(row, name)
                    continue
                ent = emap_all[name].get(gid)
                can, why = classify_broadcast(ent)
                if not can:
                    skipped_groups.add(gid)
                    apply_write_edge(conn, name, gid, False, why)
                    conn.commit()
                    print(f"  пропуск [{name}] группа {gid}: {REASON_RU.get(why, why)}")
                    _handoff(row, name)
                    continue
                if slowmode_exceeds(ent, deadline - _t.monotonic()):
                    print(f"  пропуск [{name}] группа {gid}: {REASON_RU['slowmode']}")
                    continue
                peer = pick_target(emap_all[name], row)
                key = str(gid)
                label = f"группа {gid} «{row['title'][:44]}»"
            else:
                peer = row["user_id"]
                key = str(row["user_id"])
                label = f"@{row['username'] or '-'} id={row['user_id']}"
            try:
                if media:
                    await p["client"].send_file(peer, media, caption=text or None,
                                                supports_streaming=True)
                else:
                    await p["client"].send_message(peer, text)
                conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                             "VALUES(?,?,?,?,?)", (camp, key, "ok", now_iso(), name))
                conn.commit()
                stats["ok"] += 1
                p["used"] += 1
                print(f"  ok  [{name}] {label}")
            except Exception as e:
                disp = target_disposition(e, kind)
                if disp == "defer_same_account":
                    fu = flood_pause_until(e)
                    p["flood_until"] = fu
                    deferred[name] = row
                    conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, name))
                    conn.commit()
                    seconds = int(getattr(e, "seconds", 0) or 0)
                    print(f"  ⏸ «{name}»: {type(e).__name__} {seconds} c "
                          f"(пауза до {fu[11:16]} UTC) — цель остаётся этому аккаунту")
                    continue
                if disp in ("halt_dm_queue", "halt_account"):
                    fu = spam_pause_until()
                    p["flood_until"] = fu
                    conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, name))
                    conn.commit()
                    print(f"  ⏸ «{name}»: {type(e).__name__} — пауза до {fu[11:16]} UTC, "
                          f"очередь другим не передаётся")
                    if disp == "halt_dm_queue":
                        stop_dm.set()
                    return
                status = "skip:slowmode" if disp == "skip" else f"fail:{type(e).__name__}"
                if disp == "remember":
                    remember_permanent(conn, kind, key, permanent_reason(e, kind), name)
                if disp != "skip":
                    stats["fail"] += 1
                conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                             "VALUES(?,?,?,?,?)", (camp, key, status, now_iso(), name))
                conn.commit()
                print(f"  fail [{name}] {label}: {type(e).__name__}: {str(e)[:80]}")
            await asyncio.sleep(random.uniform(dmin, dmax))

    queued = sum(len(q) for q in queues.values())
    print(f"Кампания #{camp} ({kind}, дирижёр): старт, аккаунтов {len(players)}, целей {queued}")
    await asyncio.gather(*(worker(n) for n in players))
    for p in players.values():
        try:
            await p["client"].disconnect()
        except Exception:
            pass
    leftover = sum(len(q) for q in queues.values()) + len(deferred)
    print(f"Итог: ok {stats['ok']}, fail {stats['fail']}, "
          f"не разобрано {leftover} (бюджеты/паузы/окно {args.window} ч)")
    if leftover:
        print("  повторите conduct позже — недобратое подхватится по состоянию аккаунтов")


# ---------------------------------------------------------------- CLI

def build_parser():
    pr = argparse.ArgumentParser(description="tg-userbot: ники, рассылка, DM — оркестр из N аккаунтов")
    sub = pr.add_subparsers(dest="cmd", required=True)
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--account", default="main",
                    help="имя аккаунта из реестра (accounts list; default: main)")

    p = sub.add_parser("login", parents=[ap],
                       help="авторизация и сохранение сессии для --account")
    p.add_argument("--phone")
    p.set_defaults(func=cmd_login, delay_min=0, delay_max=0, dry_run=False, max_runs=0)

    p = sub.add_parser("groups", parents=[ap],
                       help="список рассылки: --sync авто-добавление всех групп, --list показать")
    p.add_argument("--sync", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--probe", action="store_true",
                   help="проверить права записи во всех группах аккаунта")
    p.add_argument("--keep", action="store_true",
                   help="probe: только показать, не менять in_broadcast")
    p.set_defaults(func=cmd_groups)

    p = sub.add_parser("watch", parents=[ap],
                       help="дежурный режим: новые группы добавляются автоматически")
    p.add_argument("--noreply", action="store_true", help="не отвечать на «стоп»")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("scrape", parents=[ap], help="сбор никнеймов участников групп")
    p.add_argument("--groups", type=int, nargs="*", help="id конкретных групп (по умолчанию — все из списка)")
    p.set_defaults(func=cmd_scrape)

    p = sub.add_parser("broadcast", parents=[ap], help="рассылка сообщения в группы из списка")
    p.add_argument("--text"); p.add_argument("--file")
    p.add_argument("--media", type=int, help="id медиа из библиотеки (bot.py media list)")
    p.add_argument("--ids", type=int, nargs="*", help="отправить только в эти id групп")
    p.add_argument("--repeat-ok", action="store_true", dest="repeat_ok",
                   help="не пропускать группы, куда писали <24 ч назад (любым аккаунтом)")
    p.add_argument("--delay-min", type=float, default=3.0)
    p.add_argument("--delay-max", type=float, default=8.0)
    p.add_argument("--max-runs", type=int, default=0, dest="max_runs")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_broadcast)

    p = sub.add_parser("dmsend", parents=[ap],
                       help="личные DM собранным участникам (троттлинг, кросс-дедуп, бюджет)")
    p.add_argument("--text"); p.add_argument("--file")
    p.add_argument("--media", type=int, help="id медиа из библиотеки (ботом, не бот-файлом)")
    p.add_argument("--group", type=int, help="только участники указанной группы")
    p.add_argument("--limit", type=int, default=20, help="максимум получателей за запуск (по умолчанию 20)")
    p.add_argument("--repeat-ok", action="store_true", dest="repeat_ok",
                   help="позволить повторную отправку тем, кому уже писали")
    p.add_argument("--delay-min", type=float, default=20.0)
    p.add_argument("--delay-max", type=float, default=45.0)
    p.add_argument("--max-runs", type=int, default=0, dest="max_runs")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_dmsend)

    p = sub.add_parser("privacy", parents=[ap],
                       help="максимальная конфиденциальность аккаунта (Nobody везде)")
    p.add_argument("--all", action="store_true", help="применить ко всем включённым аккаунтам")
    p.add_argument("--no-check", action="store_true", dest="no_check",
                   help="не читать настройки обратно")
    p.set_defaults(func=cmd_privacy)

    p = sub.add_parser("align", parents=[ap],
                       help="выровнять аккаунт по организму: догон публичных групп + "
                            "privacy-набор + probe (идемпотентно)")
    p.add_argument("--limit", type=int, default=10,
                   help="макс вступлений за запуск (свежему аккаунту не больше 5-10/день)")
    p.add_argument("--delay-min", type=float, default=25.0)
    p.add_argument("--delay-max", type=float, default=70.0)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_align)

    p = sub.add_parser("compare", parents=[ap],
                       help="сверка двух аккаунтов: покрытие групп + приватность")
    p.add_argument("--other", default="main", help="с чем сравнивать (default: main)")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("export", help="экспорт SQLite -> CSV")
    p.add_argument("--dir", default=str(ROOT / "exports"))
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("status", help="сводка по базе")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("blacklist", help="чёрный список: add <id|@ник> | list")
    p.add_argument("cmd", choices=["add", "list"])
    p.add_argument("value", nargs="?", default="")
    p.add_argument("--reason", default="вручную")
    p.set_defaults(func=cmd_blacklist)

    p = sub.add_parser("accounts", parents=[ap],
                       help="реестр аккаунтов оркестра")
    p.add_argument("op", choices=["list", "enable", "disable", "budget", "bbudget", "import", "remove", "secret"])
    p.add_argument("name", nargs="?", default="")
    p.add_argument("value", nargs="?", default="")
    p.add_argument("--purge", action="store_true", help="remove: удалить и session-файлы")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("conduct",
                       help="дирижёр: одна кампания на все аккаунты — "
                            "пауза остаётся на том же аккаунте, бюджеты, покрытие групп")
    p.add_argument("--what", choices=["dm", "broadcast"], required=True)
    p.add_argument("--text"); p.add_argument("--file")
    p.add_argument("--media", type=int, help="id медиа из библиотеки — уйдёт подписью")
    p.add_argument("--accounts", nargs="*", help="подмножество аккаунтов (по умолчанию все включённые)")
    p.add_argument("--group", type=int, help="dm: только участники этой группы")
    p.add_argument("--ids", type=int, nargs="*", help="broadcast: только эти id групп")
    p.add_argument("--limit", type=int, default=0, help="максимум целей за запуск (0 = все)")
    p.add_argument("--repeat-ok", action="store_true", dest="repeat_ok")
    p.add_argument("--delay-min", type=float, default=0.0)
    p.add_argument("--delay-max", type=float, default=0.0)
    p.add_argument("--window", type=float, default=6.0, help="часов максимум крутить кампанию")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.set_defaults(func=cmd_conduct, max_runs=0)

    p = sub.add_parser("media", parents=[ap],
                       help="медиа-библиотека через «Избранное»: add|pull|sync|del|list")
    p.add_argument("op", choices=["add", "pull", "sync", "del", "list"])
    p.add_argument("value", nargs="?", default="",
                   help="add: путь к файлу; sync/del: id из list")
    p.add_argument("--limit", type=int, default=30,
                   help="pull: сколько последних сообщений Избранного просмотреть")
    p.set_defaults(func=cmd_media)

    p = sub.add_parser("cloud", help="облачное зеркало Supabase: push|status|test")
    p.add_argument("op", choices=["push", "status", "test"])
    p.set_defaults(func=cmd_cloud)
    return pr


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = build_parser().parse_args()
    res = args.func(args)
    if asyncio.iscoroutine(res):
        try:
            asyncio.run(res)
        except KeyboardInterrupt:
            print("\nостановлено пользователем")
    maybe_cloud_push(args)


if __name__ == "__main__":
    main()
