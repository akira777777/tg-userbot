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
  added_at TEXT, active INTEGER DEFAULT 1, in_broadcast INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS members(
  user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
  is_bot INTEGER DEFAULT 0, collected_at TEXT, source_group INTEGER
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
CREATE INDEX IF NOT EXISTS idx_ag_group ON account_groups(group_id);
CREATE INDEX IF NOT EXISTS idx_sources_group ON member_sources(group_id);
CREATE INDEX IF NOT EXISTS idx_sources_user ON member_sources(user_id);
CREATE INDEX IF NOT EXISTS idx_sent_campaign ON sent(campaign);
CREATE INDEX IF NOT EXISTS idx_sent_ts ON sent(ts);
CREATE INDEX IF NOT EXISTS idx_campaigns_kind ON campaigns(kind);
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


def migrate(conn):
    """Идемпотентно: колонка account у кампаний/отправок + сид главного аккаунта."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(campaigns)")}
    if "account" not in cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN account TEXT DEFAULT 'main'")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sent)")}
    if "account" not in cols:
        conn.execute("ALTER TABLE sent ADD COLUMN account TEXT DEFAULT 'main'")
    # sent.account появляется только после ALTER, поэтому индекс нельзя
    # создавать в первом проходе SCHEMA на уже существующей базе.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sent_account_status_ts ON sent(account, status, ts)"
    )
    conn.execute("INSERT OR IGNORE INTO accounts(name,session,added_at) VALUES('main',?,?)",
                 (LEGACY_SESSION, now_iso()))
    conn.commit()


def record_edge(conn, account, gid):
    """Покрытие: аккаунт состоит в группе. can_write НЕ трогаем —
    его выставляет только groups --probe (sync/conduct не сбрасывают права)."""
    conn.execute("INSERT INTO account_groups(account,group_id,can_write,seen_at) "
                 "VALUES(?,?,1,?) ON CONFLICT(account,group_id) DO UPDATE SET seen_at=excluded.seen_at",
                 (account, gid, now_iso()))


def flood_guard(conn, account):
    row = conn.execute("SELECT flood_until FROM accounts WHERE name=?", (account,)).fetchone()
    if row and row["flood_until"] and row["flood_until"] > now_iso():
        sys.exit(f"Аккаунт «{account}» в cooldown до {row['flood_until']} (FloodWait) — "
                 f"подожди или раздай работу другому аккаунту")


def today_dm_sent(conn, account):
    return conn.execute(
        "SELECT COUNT(*) c FROM sent s JOIN campaigns c ON c.id=s.campaign "
        "WHERE s.account=? AND s.status='ok' AND c.kind='dm' AND s.ts>=?",
        (account, now_iso()[:10] + "T00:00:00")).fetchone()["c"]


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
            gtype = "channel" if getattr(ent, "broadcast", False) else \
                    "megagroup" if getattr(ent, "megagroup", False) else "group"
            cur = conn.execute(
                "INSERT OR IGNORE INTO groups(id,title,username,gtype,added_at) "
                "VALUES(?,?,?,?,?)",
                (ent.id, getattr(ent, "title", ""), getattr(ent, "username", None) or "",
                 gtype, now_iso()))
            record_edge(conn, args.account, ent.id)
            if cur.rowcount:
                added += 1
                print(f"  + {ent.id} {ent.title}")
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
            if not conn.execute("SELECT 1 FROM groups WHERE id=?", (chat.id,)).fetchone():
                conn.execute("INSERT INTO groups(id,title,username,gtype,added_at) VALUES(?,?,?,?,?)",
                             (chat.id, chat.title, getattr(chat, "username", None) or "",
                              "channel" if getattr(chat, "broadcast", False) else
                              "megagroup" if getattr(chat, "megagroup", False) else "group",
                              now_iso()))
                conn.commit()
                record_edge(conn, args.account, chat.id)
                conn.commit()
                print(f"[авто+] группа {chat.id} «{chat.title}» добавлена в рассылку (аккаунт «{args.account}»)")

    client.add_event_handler(on_msg, events.NewMessage())
    print("watch запущен: новые группы добавляются автоматически, «стоп» в DM -> blacklist. Ctrl+C — выход.")
    await client.run_until_disconnected()


SCRAPE_BATCH = 200


def save_members(conn, users, gid):
    """Пакетная вставка участников одной группы. Коммит остаётся на вызывающем."""
    if not users:
        return 0
    collected = now_iso()
    member_rows = [
        (u.id, u.username or "", u.first_name or "", u.last_name or "",
         int(bool(getattr(u, "bot", False))), collected, gid)
        for u in users
    ]
    source_rows = [(u.id, gid) for u in users]
    before = conn.execute("SELECT COUNT(*) c FROM members").fetchone()["c"]
    conn.executemany(
        "INSERT OR IGNORE INTO members(user_id,username,first_name,last_name,is_bot,collected_at,source_group) "
        "VALUES(?,?,?,?,?,?,?)",
        member_rows,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO member_sources(user_id,group_id) VALUES(?,?)",
        source_rows,
    )
    after = conn.execute("SELECT COUNT(*) c FROM members").fetchone()["c"]
    return after - before


def save_member(conn, u, gid):
    return save_members(conn, [u], gid)


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
        buf = []
        try:
            async for u in client.iter_participants(ent, aggressive=True):
                buf.append(u)
                if len(buf) >= SCRAPE_BATCH:
                    n += save_members(conn, buf, gid)
                    buf.clear()
            if buf:
                n += save_members(conn, buf, gid)
                buf.clear()
        except FloodWaitError as e:
            if buf:
                n += save_members(conn, buf, gid)
                buf.clear()
            conn.commit()
            if e.seconds > 600:  # часовые/суточные паузы — смысл ждать теряет
                sys.exit(f"FloodWait {e.seconds} c (>10 мин) — прерываю scrape; "
                         f"сохранённое не потеряно, смотри status и повтори scrape")
            print(f"  FloodWait {e.seconds} c в группе {gid} — жду и оставляю группу на следующий scrape",
                  flush=True)
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            name = type(e).__name__
            if "AdminRequired" in name or "Forbidden" in name or "ChatWriteForbidden" in name:
                print(f"  группа {gid} «{ent.title}»: список участников закрыт "
                      f"для не-админов ({name}) — пропущена")
            else:
                print(f"  группа {gid} «{ent.title}»: ошибка {name}: {str(e)[:100]}")
        conn.commit()
        print(f"  группа {gid} «{ent.title}»: новых участников {n}")
        total += n
        await asyncio.sleep(2)  # мягкая пауза между группами
    all_m = conn.execute("SELECT COUNT(*) c FROM members").fetchone()["c"]
    print(f"Готово: собрано {total} новых (всего в базе: {all_m})", flush=True)


# s.target хранится текстом; сравнение со стороны sent оставляет индекс пригодным.
NOT_YET_DMED = (
    "NOT EXISTS (SELECT 1 FROM sent s JOIN campaigns c ON c.id=s.campaign "
    "WHERE c.kind='dm' AND s.status='ok' AND s.target=CAST(m.user_id AS TEXT))"
)


def dm_targets(conn, group_id=None, repeat_ok=False, limit=None):
    """Утиный выбор: боты, blacklist и получатели прошлых DM-кампаний исключены."""
    q = ("SELECT m.* FROM members m WHERE m.is_bot=0 "
         "AND m.user_id NOT IN (SELECT user_id FROM blacklist)")
    p = []
    if group_id:
        q += " AND m.user_id IN (SELECT user_id FROM member_sources WHERE group_id=?)"
        p.append(group_id)
    if not repeat_ok:
        q += f" AND {NOT_YET_DMED}"
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
    from telethon.errors import FloodWaitError
    cur = conn.execute("INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
                       (kind, text, now_iso(), account))
    camp = cur.lastrowid
    conn.commit()
    done = ok = 0
    halt = False
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
            except FloodWaitError as e:
                fu = (datetime.now(timezone.utc) + timedelta(seconds=e.seconds)).isoformat(timespec="seconds")
                conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, account))
                conn.commit()
                print(f"  FloodWait {e.seconds} c на аккаунте «{account}» (cooldown до {fu})")
                halt = True  # флуд аккаунта — вся кампания бессмысленна, стоп
                break
            except Exception as e:
                conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                             "VALUES(?,?,?,?,?)",
                             (camp, key, f"fail:{type(e).__name__}", now_iso(), account))
                print(f"  fail {label}: {type(e).__name__}: {str(e)[:120]}")
                break
        conn.commit()
        done += 1
        if args.delay_max > 0 and not halt and done < len(targets):
            await asyncio.sleep(random.uniform(args.delay_min, args.delay_max))
    suffix = " [dry-run, ничего не отправлено]" if args.dry_run else ""
    if halt:
        suffix += " [остановлено из-за FloodWait — аккаунт в cooldown]"
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
        cap = bud["daily_budget"] if bud else 0
        if cap:  # 0 = без лимита (не рекомендую)
            used = today_dm_sent(conn, account)
            left = max(0, cap - used)
            if left == 0:
                sys.exit(f"Дневной DM-бюджет «{account}» исчерпан ({used}/{cap}). "
                         f"Завтра продолжится сам; объём = accounts budget {account} N, "
                         f"или подключи второй аккаунт (login --account work2).")
            if len(rows) > left:
                print(f"бюджет «{account}»: обрезаю выборку {len(rows)} -> {left} "
                      f"(сегодня ok {used}/{cap})")
                rows = rows[:left]
    if not rows:
        sys.exit("Нет доступных получателей (все отправлены/в blacklist/боты).")
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
            flags = []
            if not a["enabled"]:
                flags.append("ВЫКЛ")
            if a["flood_until"] and a["flood_until"] > now_iso():
                flags.append(f"cooldown до {a['flood_until'][11:16]} UTC")
            print(f"  {a['name']:<14} id={a['user_id'] or '-':<12} "
                  f"DM сегодня {used:>3}/{a['daily_budget'] or '∞'}  "
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
        if ent is None:
            can, why = False, "аккаунт не состоит (после sync других — перепроверь)"
        elif getattr(ent, "left", False):
            can, why = False, "выход из чата (left)"
        elif getattr(ent, "broadcast", False):
            admin = getattr(ent, "admin_rights", None) or getattr(ent, "creator", False)
            can = bool(admin)
            why = "канал: админ ✓" if can else "канал: подписчик — постинг закрыт"
        else:
            dbr = getattr(ent, "default_banned_rights", None)
            # ChatBannedRights: флаг True = право ЗАПРЕЩЕНО для всех по умолчанию
            blocked = bool(dbr and getattr(dbr, "send_messages", False))
            can = not blocked
            why = "чат: писать нельзя (ограничение для всех)" if blocked else "чат: открыт"
        if can:
            ok_cnt += 1
            if not keep:
                conn.execute("UPDATE groups SET active=1 WHERE id=?", (r["id"],))
        else:
            bad_cnt += 1
            if not keep:
                conn.execute("UPDATE groups SET in_broadcast=0 WHERE id=?", (r["id"],))
        if ent is not None:
            conn.execute("INSERT OR REPLACE INTO account_groups(account,group_id,can_write,seen_at)"
                         " VALUES(?,?,?,?)", (account, r["id"], int(can), now_iso()))
        if verbose:
            print(f"  {'✓' if can else '✗'} {r['id']:<15} {(r['title'] or '')[:46]:<48} {why}")
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


def _skip_cloud_push(args) -> bool:
    """Чтение не меняет зеркало: полный push после list/export только греет SQLite."""
    name = getattr(args.func, "__name__", "")
    if name == "cmd_export":
        return True
    if name == "cmd_blacklist" and getattr(args, "cmd", None) == "list":
        return True
    if name == "cmd_accounts" and getattr(args, "op", None) == "list":
        return True
    if name == "cmd_groups" and getattr(args, "list", False) and not getattr(args, "sync", False) and not getattr(args, "probe", False):
        return True
    return False


def maybe_cloud_push(args):
    if os.environ.get("AUTO_CLOUD") != "1":
        return
    if getattr(args.func, "__name__", "") not in CLOUD_HOOK:
        return
    if _skip_cloud_push(args):
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
    from telethon.errors import (FloodWaitError, UserAlreadyJoinedError,
                                 UsernameNotPresentError)
    joined = 0
    for r in missing[:args.limit]:
        try:
            ent = await client.join_chat(r["username"])
            record_edge(conn, account, ent.id if getattr(ent, "id", None) else r["id"])
            conn.commit()
            joined += 1
            print(f"  + t.me/{r['username']} «{r['title'][:40]}»")
        except UserAlreadyJoinedError:
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
    for a in conn.execute("SELECT name,enabled,user_id,daily_budget,flood_until "
                          "FROM accounts ORDER BY name"):
        used = today_dm_sent(conn, a["name"])
        mark = "" if a["enabled"] else " [ВЫКЛ]"
        fl = " [cooldown]" if a["flood_until"] and a["flood_until"] > now_iso() else ""
        print(f"  аккаунт «{a['name']}»: id={a['user_id'] or '?'} "
              f"DM сегодня {used}/{a['daily_budget'] or '∞'}{mark}{fl}")
    print(f"  кампаний: {c('campaigns')}")
    print("  последние отправки:")
    for s in conn.execute("SELECT s.campaign,c.kind,s.target,s.status,s.ts FROM sent s "
                          "JOIN campaigns c ON c.id=s.campaign "
                          "ORDER BY s.ts DESC LIMIT 5"):
        print(f"    #{s['campaign']} {s['kind']} -> {s['target']}: {s['status']} ({s['ts']})")
    free = conn.execute(
        "SELECT COUNT(*) c FROM members m WHERE m.is_bot=0 AND m.user_id "
        f"NOT IN (SELECT user_id FROM blacklist) AND {NOT_YET_DMED}").fetchone()["c"]
    print(f"  доступных для новых DM: {free}")


def cmd_blacklist(args):
    conn = connect()
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


def preview_assign(kind, rows, players, edges=None, limit_show=30):
    """Сухой план раскладки целей по аккаунтам (для --dry-run и тестов)."""
    free = {n: p for n, p in players.items() if not cooldown_remaining(p)}
    out = []
    caps = {n: (max(0, (p["budget"] - p["used"])) if (kind == "dm" and p["budget"])
               else 10 ** 9) for n, p in free.items()}
    order = sorted(free)
    i = 0
    for row in rows[:limit_show]:
        who = None
        for _ in range(len(order)):
            n = order[i % len(order)]
            i += 1
            if kind == "broadcast" and edges is not None:
                gid = row["id"]
                if gid not in edges.get(n, ()):
                    continue
            if caps.get(n, 0) > 0:
                caps[n] -= 1
                who = n
                break
        out.append((who, row))
    unplanned = sum(1 for w, _ in out if w is None)
    return out, unplanned


async def cmd_conduct(args):
    """Дирижёр: одна кампания на все включённые аккаунты. Общий организм:
    единая база целей/журнала/чёрного списка; если аккаунт ушёл на cooldown
    (FloodWait) — цель немедленно подхватывает другой; дневные DM-бюджеты
    считаются на аккаунт; группы отправляются тем аккаунтом, который в них состоит."""
    import time as _t
    conn = connect()
    media = resolve_media(conn, getattr(args, "media", None))
    text = read_message(args)
    kind = args.what
    names = args.accounts or [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    from telethon.errors import FloodWaitError
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
            arow = conn.execute("SELECT daily_budget,flood_until FROM accounts WHERE name=?",
                                (n,)).fetchone()
            players[n] = {"client": c, "me": me.id,
                          "budget": (arow["daily_budget"] or 0) if arow else 0,
                          "used": today_dm_sent(conn, n) if kind == "dm" else 0,
                          "flood_until": arow["flood_until"] if arow else None}
            rem = cooldown_remaining(players[n])
            print(f"  игрок «{n}» (id={me.id}): "
                  + (f"пауза ещё {rem} c" if rem else "готов")
                  + (f", DM сегодня {players[n]['used']}/{players[n]['budget']}"
                     if kind == 'dm' and players[n]['budget'] else ""))
        except SystemExit as e:
            print(f"  пропуск «{n}»: {e}")
        except Exception as e:
            print(f"  пропуск «{n}»: {type(e).__name__}: {str(e)[:80]}")
    conn.commit()
    if not players:
        sys.exit("Ни один аккаунт не подключён — login --account NAME")

    dmin, dmax = args.delay_min or 0, args.delay_max or 0
    if not dmax:
        dmin, dmax = (20.0, 45.0) if kind == "dm" else (3.0, 8.0)

    edges = {}
    if kind == "broadcast":
        emaps = {}
        for n, p in players.items():
            dialogs = await p["client"].get_dialogs()
            emaps[n] = {d.entity.id: d.entity for d in dialogs
                        if isinstance(d.entity, (Chat, Channel))}
            for gid in emaps[n]:
                record_edge(conn, n, gid)
            edges[n] = set(emaps[n])
        conn.commit()
        q = "SELECT * FROM groups WHERE active=1 AND in_broadcast=1"
        prm = []
        if args.ids:
            q += " AND id IN (%s)" % ",".join("?" * len(args.ids))
            prm = args.ids
        recent = set() if args.repeat_ok else recent_ok_targets(conn, "broadcast", 24)
        rows = [r for r in conn.execute(q, prm).fetchall() if str(r["id"]) not in recent]
        covered_any = [r for r in rows if any(r["id"] in e for e in edges.values())]
        print(f"broadcast: групп в работе {len(covered_any)}, без аккаунта-члена "
              f"{len(rows) - len(covered_any)}")
        rows = covered_any
        emap_all = emaps
    else:
        rows = dm_targets(conn, group_id=args.group, repeat_ok=args.repeat_ok,
                          limit=args.limit if args.limit else None)
        me_ids = {p["me"] for p in players.values()}
        rows = [r for r in rows if r["user_id"] not in me_ids]  # операторам не пишем
        if args.limit:
            rows = rows[:args.limit]

    if args.dry_run:
        plan, unplanned = preview_assign(kind, rows, players,
                                         edges if kind == "broadcast" else None)
        for who, row in plan:
            if kind == "broadcast":
                print(f"  [dry] {who or 'НИКОМУ'}: группа {row['id']} «{row['title'][:44]}»")
            else:
                print(f"  [dry] {who or 'НЕТ БЮДЖЕТА'}: @{row['username'] or '-'} id={row['user_id']}")
        print(f"Сухой план: целей {len(rows)}, показано {len(plan)}, "
              f"без покрытия/бюджета {unplanned}; аккаунтов {len(players)} — ничего не отправлено")
        for p in players.values():
            await p["client"].disconnect()
        return

    if not rows:
        for p in players.values():
            await p["client"].disconnect()
        sys.exit("Нет целей (всё отправлено/покрыто/в blacklist/бюджеты)")

    cur = conn.execute("INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
                       (kind, text, now_iso(), "conductor"))
    camp = cur.lastrowid
    conn.commit()
    pool = collections.deque(rows)
    deadline = _t.monotonic() + args.window * 3600
    stats = {"ok": 0, "fail": 0}

    async def worker(name):
        p = players[name]
        rot = 0
        while pool and _t.monotonic() < deadline:
            rem = cooldown_remaining(p)
            if rem:
                await asyncio.sleep(min(rem + 1, 120))
                continue
            if kind == "dm" and p["budget"] and p["used"] >= p["budget"]:
                print(f"  «{name}»: дневной бюджет DM исчерпан ({p['used']}/{p['budget']}) — отдыхает до завтра")
                return
            row = pool.popleft()
            if kind == "broadcast":
                gid = row["id"]
                if gid not in edges.get(name, ()):
                    pool.append(row)
                    rot += 1
                    if rot > 3 * len(pool) + 8:
                        return  # для этого аккаунта тут делать нечего
                    await asyncio.sleep(0.3)
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
                rot = 0
                if kind == "dm":
                    p["used"] += 1
                print(f"  ok  [{name}] {label}")
            except FloodWaitError as e:
                fu = (datetime.now(timezone.utc) + timedelta(seconds=e.seconds)).isoformat(timespec="seconds")
                p["flood_until"] = fu
                conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, name))
                conn.commit()
                pool.appendleft(row)  # цель подхватит другой аккаунт
                print(f"  ⏸ «{name}»: FloodWait {e.seconds} c (пауза до {fu[11:16]} UTC) — цель передана")
                continue
            except Exception as e:
                conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                             "VALUES(?,?,?,?,?)",
                             (camp, key, f"fail:{type(e).__name__}", now_iso(), name))
                conn.commit()
                stats["fail"] += 1
                print(f"  fail [{name}] {label}: {type(e).__name__}: {str(e)[:80]}")
            await asyncio.sleep(random.uniform(dmin, dmax))

    print(f"Кампания #{camp} ({kind}, дирижёр): старт, аккаунтов {len(players)}, целей {len(pool)}")
    await asyncio.gather(*(worker(n) for n in players))
    for p in players.values():
        try:
            await p["client"].disconnect()
        except Exception:
            pass
    print(f"Итог: ok {stats['ok']}, fail {stats['fail']}, "
          f"не разобрано {len(pool)} (бюджеты/паузы/окно {args.window} ч)")
    if pool:
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
    p.add_argument("op", choices=["list", "enable", "disable", "budget", "import", "remove", "secret"])
    p.add_argument("name", nargs="?", default="")
    p.add_argument("value", nargs="?", default="")
    p.add_argument("--purge", action="store_true", help="remove: удалить и session-файлы")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("conduct",
                       help="дирижёр: одна кампания на все аккаунты — "
                            "failover при FloodWait, бюджеты, покрытие групп")
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
