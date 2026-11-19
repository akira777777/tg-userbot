#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tg-userbot — Telegram userbot-инструмент:

  * сбор никнеймов участников групп  -> SQLite (members)
  * авто-добавление групп в список рассылки (groups --sync / watch)
  * рассылка сообщений в сами группы        (broadcast)
  * личные DM собранным участникам          (dmsend) — с троттлингом,
    дедупликацией по прошлым кампаниям и чёрным списком.

Полная инструкция — README.md и РУКОВОДСТВО.md. Скрипт можно запускать без .env
для команд status / export / blacklist (они работают только с базой).
"""
import argparse
import asyncio
import collections
import csv
import json
import math
import mimetypes
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass
from pathlib import Path

from control import orchestra  # планирование оркестра: профили, группы, гейты

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("USERBOT_DB", ROOT / "userbot.db"))
SESSION_DIR = Path(os.environ.get("USERBOT_SESSIONS", ROOT / "sessions"))
MEDIA_DIR = Path(os.environ.get("USERBOT_MEDIA", ROOT / "media_cache"))
SECRETS_PATH = Path(os.environ.get("USERBOT_SECRETS", ROOT / "account-secrets.json"))
STOP_PATH = Path(os.environ.get("USERBOT_STOP", ROOT / "control-stop.json"))
ACCOUNTS_RE = re.compile(r"^[a-z0-9_\-]{1,32}$")
LEGACY_SESSION = os.environ.get("USERBOT_SESSION", str(ROOT / "userbot.session"))
STOP_RE = re.compile(r"^(stop|opt.?out|unsubscribe|отпиш|отпис|стоп)", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups(
  id INTEGER PRIMARY KEY,
  title TEXT, username TEXT, gtype TEXT,
  added_at TEXT, active INTEGER DEFAULT 1, in_broadcast INTEGER DEFAULT 1,
  skip_reason TEXT, discovered_at TEXT
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
  msg_id INTEGER,          -- id отправленного сообщения: основа ответа/доставки
  replied_at TEXT,         -- человек ответил — единственный твёрдый сигнал «дошло»
  blocked_at TEXT,         -- он нас заблокировал: сигнал «не надо»
  PRIMARY KEY(campaign, target)
);
CREATE TABLE IF NOT EXISTS blacklist(
  user_id INTEGER PRIMARY KEY, username TEXT, reason TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS accounts(
  name TEXT PRIMARY KEY, session TEXT, user_id INTEGER, api_id TEXT, api_hash TEXT,
  enabled INTEGER DEFAULT 1, daily_budget INTEGER DEFAULT 30,
  broadcast_budget INTEGER DEFAULT 15,
  flood_until TEXT, note TEXT, added_at TEXT,
  registered_at TEXT, cohort TEXT, spam_strikes INTEGER DEFAULT 0,
  last_peerflood_at TEXT
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


_SCHEMA_READY: set = set()


def connect(migrate_schema=None):
    """Соединение с базой.

    migrate_schema: True — гарантированно прогнать схему и миграцию. По
    умолчанию — один раз на каждый файл базы в пределах процесса. Раньше connect()
    делал migrate() при каждом вызове, а migrate() пишет в базу
    (INSERT INTO accounts, ALTER, CREATE INDEX). Внутри cmd_conduct
    get_client() открывает своё соединение, пока у вызывающего висит
    незакоммиченная транзакция, — второе соединение на запись получало
    «database is locked» и аккаунт молча выпадал из прогона.
    """
    base = Path(DB_PATH)
    if migrate_schema is None:
        # Один раз на конкретную базу, а не на процесс: тесты и `--db`
        # подменяют DB_PATH, и каждой новой базе нужна своя схема.
        migrate_schema = base not in _SCHEMA_READY
    conn = sqlite3.connect(base, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # читаем из web.py параллельно с записью
    conn.execute("PRAGMA busy_timeout=15000")
    if migrate_schema:
        try:
            conn.executescript(SCHEMA)
            migrate(conn)
            _SCHEMA_READY.add(base)
        except sqlite3.OperationalError:
            # База занята другим процессом, который уже всё мигрировал:
            # данных это не ломает, поэтому не выдаём себя за ошибку схемы.
            conn.rollback()
    return conn


def db_call_retry(fn, *, attempts=6, first_delay=0.5, max_delay=8.0, sleep=time.sleep):
    """Выполнить fn() с повтором, если SQLite занят другим процессом-воркером.

    Несколько процессов `bot.py watch` пишут в одну базу; при долгой чужой
    write-транзакции ожидание внутри busy_timeout может кончиться
    'database is locked'. Записи-«стопы» и авторегистрация групп обязаны
    это переживать. fn() должен быть идемпотентным (OR IGNORE / ON CONFLICT).
    """
    delay = first_delay
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            text = str(exc).lower()
            if "locked" not in text and "busy" not in text:
                raise
            if attempt == attempts - 1:
                raise
            sleep(delay + random.uniform(0, 0.15))
            delay = min(delay * 2, max_delay)


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
    # Анти-спам: возраст аккаунта, контингент и счётчик PEER_FLOOD.
    _add_column(conn, "accounts", "registered_at", "TEXT")
    _add_column(conn, "accounts", "cohort", "TEXT")
    _add_column(conn, "accounts", "spam_strikes", "INTEGER DEFAULT 0")
    _add_column(conn, "accounts", "last_peerflood_at", "TEXT")
    # Эффективность: ответ на конкретное сообщение и блокировка после доставки.
    for col, ddl in (("msg_id", "INTEGER"), ("replied_at", "TEXT"), ("blocked_at", "TEXT")):
        _add_column(conn, "sent", col, ddl)
    # Частичный индекс: «был ли ответ» ищет по replied_at, а не сканом всем sent.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sent_msg_id ON sent(msg_id) "
                 "WHERE msg_id IS NOT NULL")
    # Оркестр: инвайт-ссылка приватной группы и статус общего набора групп.
    _add_column(conn, "groups", "invite_link", "TEXT")
    _add_column(conn, "groups", "join_status", "TEXT")
    _add_column(conn, "groups", "gate_at", "TEXT")
    _add_column(conn, "groups", "discovered_at", "TEXT")
    # sent.account появляется только после ALTER, поэтому индекс нельзя
    # создавать в первом проходе SCHEMA на уже существующей базе.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sent_account_status_ts ON sent(account, status, ts)"
    )
    # Покрывающий индекс для дедупа DM и агрегата кампаний. Колонки есть
    # в исходной схеме, но индекс создаём здесь, рядом с остальными,
    # чтобы старая база получила его тем же проходом, что и ALTER.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sent_campaign_status_target "
        "ON sent(campaign, status, target)"
    )
    # deleted и dm_block появляются ALTER'ом выше. Частичный индекс повторяет
    # фильтр очереди, поэтому ORDER BY collected_at не сортирует всю таблицу.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_members_dm_collected ON members(collected_at) "
        "WHERE is_bot=0 AND COALESCE(deleted, 0)=0 AND COALESCE(dm_block, '')=''"
    )
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


# Причины, при которых группу выключили ОСОЗНАННО: watch/--sync их не
# отменяют. Иначе дежурный режим добавляет чат по первому же сообщению и
# возвращает в рассылку то, что уже признали непригодным (отклоняет
# перефорвард, платная, закрытая тема). Право записи тут ни при чём.
BLOCKED_REASONS = ("forward_blocked", "payment", "topic_closed", "restricted",
                   "client_chat")


def is_manually_blocked(conn, gid):
    row = conn.execute("SELECT skip_reason, active FROM groups WHERE id=?",
                       (gid,)).fetchone()
    if row is None:
        return False
    return (row["skip_reason"] in BLOCKED_REASONS) or not row["active"]


def refresh_group_flags(conn, gid, reason):
    """Группа в рассылке, если хоть один аккаунт может в неё писать.

    Ручной стоп-лист сильнее автосинхронизации: выключенную группу
    watch не возвращает, пока её не включат явно (groups enable).
    """
    row = conn.execute("SELECT skip_reason, active FROM groups WHERE id=?",
                       (gid,)).fetchone()
    if row is not None and (row["skip_reason"] in BLOCKED_REASONS or not row["active"]):
        return
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


def sync_account_age(conn, account, registration_ts=None, now=None):
    """Возраст аккаунта из Telegram (registered_at раздаётся рядом с user_id).
    Повторный вызов с тем же значением ничего не меняет."""
    if not registration_ts:
        return None
    now = now or datetime.now(timezone.utc)
    iso = datetime.fromtimestamp(int(registration_ts), timezone.utc).isoformat(timespec="seconds")
    conn.execute("UPDATE accounts SET registered_at=COALESCE(registered_at, ?) WHERE name=?",
                 (iso, account))
    return account_age_days(iso, now)


def account_age_days(registered_at, now=None):
    if not registered_at:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(str(registered_at))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds() / 86400)


def account_dm_cap(conn, account, configured=None, now=None):
    """Дневной потолок DM: явный бюджет аккаунта, но не выше потолка контингента.
    Возраст берётся из accounts.registered_at; без даты — консервативный unknown."""
    row = conn.execute("SELECT registered_at, cohort, daily_budget FROM accounts "
                       "WHERE name=?", (account,)).fetchone()
    if row is None:
        return cohort_budget("unknown", configured)
    age = account_age_days(row["registered_at"], now)
    cohort = account_cohort(age, row["cohort"] or "auto")
    base = configured if configured is not None else row["daily_budget"]
    return cohort_budget(cohort, base)


def account_health(conn, account, now=None):
    """Однострочный индикатор риска аккаунта для status/accounts list."""
    row = conn.execute("SELECT registered_at, cohort, COALESCE(spam_strikes,0) s, "
                       "last_peerflood_at FROM accounts WHERE name=?", (account,)).fetchone()
    if row is None:
        return "нет в реестре"
    now = now or datetime.now(timezone.utc)
    age = account_age_days(row["registered_at"], now)
    cohort = account_cohort(age, row["cohort"] or "auto")
    parts = [COHORT_RU.get(cohort, cohort)]
    if age is not None:
        parts.append(f"{int(age)} дн")
    if int(row["s"] or 0) > 0:
        parts.append(f"PEER_FLOOD ×{int(row['s'])} (посл. {str(row['last_peerflood_at'] or '-')[:10]})")
    return ", ".join(parts)


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
    bud = conn.execute(f"SELECT {col} AS c FROM accounts WHERE name=?", (account,)).fetchone()  # nosec B608
    if kind == "dm":
        return account_dm_cap(conn, account, bud["c"] if bud else None)
    if not bud or bud["c"] is None:
        return 15
    return bud["c"]


def load_account_secrets():
    if not SECRETS_PATH.exists():
        return {}
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_account_secrets(data):
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(SECRETS_PATH, 0o600)
    except OSError:
        pass


def persist_account_secret(name, api_id, api_hash):
    data = load_account_secrets()
    current = data.get(name, {})
    if api_id:
        current["api_id"] = str(api_id)
    if api_hash:
        current["api_hash"] = str(api_hash)
    if current:
        data[name] = current
    elif name in data:
        del data[name]
    save_account_secrets(data)


def migrate_plaintext_secret(conn, account, row):
    if not row or not (row["api_id"] or row["api_hash"]):
        return
    persist_account_secret(account, row["api_id"], row["api_hash"])
    conn.execute("UPDATE accounts SET api_id=NULL, api_hash=NULL WHERE name=?", (account,))
    conn.commit()


def stop_requested(account, started_at):
    if not STOP_PATH.exists():
        return False
    try:
        data = json.loads(STOP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    at = str(data.get("at") or "")
    # mtime файла надёжнее поля at: at пишется с точностью до секунды, и
    # стоп, пришедший в ту же секунду, что старт, выглядел бы «старым».
    try:
        stamp = STOP_PATH.stat().st_mtime
    except OSError:
        stamp = 0.0
    try:
        start = datetime.fromisoformat(str(started_at)).timestamp()
    except (TypeError, ValueError):
        start = 0.0
    if stamp and start and stamp + 1.0 < start:
        return False
    if at and not stamp and at <= str(started_at):
        return False
    targets = data.get("accounts") or []
    return "all" in targets or account in targets


def request_stop(account):
    STOP_PATH.write_text(
        json.dumps({"accounts": [account or "all"], "at": now_iso()}, ensure_ascii=False),
        encoding="utf-8",
    )


def get_client(account="main", allow_flood=False):
    load_env()
    if not ACCOUNTS_RE.fullmatch(account):
        sys.exit("Некорректное имя аккаунта: a-z, 0-9, _ , - до 32 символов")
    # Схему мигрирует точка входа процесса, а не фабрика клиента: get_client
    # зовут из cmd_conduct по кругу, пока там висит своя запись в базу.
    conn = connect(migrate_schema=False)
    row = conn.execute("SELECT * FROM accounts WHERE name=?", (account,)).fetchone()
    if row and not row["enabled"]:
        conn.close()
        sys.exit(f"Аккаунт «{account}» отключён — bot.py accounts enable {account}")
    if row and not allow_flood:
        flood_guard(conn, account)
    session = (row["session"] if row and row["session"]
               else str(SESSION_DIR / f"{account}.session"))
    migrate_plaintext_secret(conn, account, row)
    # приоритет секретов: файл account-secrets.json > env API_ID_<NAME> > общий API_ID
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

    stored = load_account_secrets().get(account, {})
    api_id = stored.get("api_id") or _env_secret("API_ID") or ENV.get("API_ID")
    api_hash = stored.get("api_hash") or _env_secret("API_HASH") or ENV.get("API_HASH")
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
    # Выключенную вручную группу не воскрешаем: дежурный режим видит её
    # сообщения постоянно и иначе возвращал бы в рассылку каждый стоп-лист.
    blocked = is_manually_blocked(conn, gid)
    cur = conn.execute(
        "INSERT OR IGNORE INTO groups(id,title,username,gtype,added_at,in_broadcast,skip_reason) "
        "VALUES(?,?,?,?,?,?,?)",
        (gid, title, username, gtype, now_iso(), 0 if blocked else (1 if can else 0),
         "client_chat" if blocked else ("" if can else why)))
    added = cur.rowcount
    conn.execute(
        "UPDATE groups SET title=?, username=?, gtype=? WHERE id=?",
        (title, username, gtype, gid))
    record_edge(conn, account, gid, can_write=1 if can else 0)
    if not blocked:
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
    if args.enable:
        # Снять ручной стоп-лист: вернуть группу в рассылку, если писать есть кому.
        done = 0
        for gid in args.enable:
            conn.execute("UPDATE groups SET active=1, skip_reason='' WHERE id=?", (gid,))
            refresh_group_flags(conn, gid, "")
            done += 1
        conn.commit()
        print(f"Возвращено в рассылку: {done} (дальше решает право записи аккаунтов)")
        return
    if not args.sync:
        sys.exit("Выберите действие: groups --sync / --list / --probe [--keep] / --enable <id...>")
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

    async def on_gate(msg, client=client, conn=conn):
        """Новое сообщение бота с кнопкой проверки — нажимаем, если она безопасна."""
        from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
        markup = getattr(msg, "reply_markup", None)
        if markup is None or not getattr(markup, "rows", None):
            return
        buttons = []
        for r_index, row in enumerate(markup.rows):
            for c_index, button in enumerate(getattr(row, "buttons", []) or []):
                if getattr(button, "url", None):
                    continue
                buttons.append((r_index, c_index, getattr(button, "text", "") or ""))
        candidates = orchestra.botgate_candidates(buttons)
        if not candidates:
            return
        try:
            await client(GetBotCallbackAnswerRequest(peer=msg.chat_id, msg_id=msg.id, data=b""))
            print(f"[gate] чат {msg.chat_id}: нажал «{candidates[0].text}»")
        except Exception as exc:
            print(f"[gate] чат {msg.chat_id}: {type(exc).__name__}: {str(exc)[:60]}")

    async def on_msg(e):
        if e.is_private:
            txt = (e.raw_text or "").strip()
            # Любой ответ = доказанная доставка. Считаем до разбора «стоп»,
            # чтобы отписка тоже попала в статистику как реакция.
            try:
                if record_reply(conn, e.sender_id, msg_id=getattr(e, "reply_to_msg_id", None),
                                text=txt):
                    conn.commit()
                    print(f"[reply] {e.sender_id}: «{txt[:40]}» — доставка подтверждена")
            except Exception as exc:
                print(f"[reply] {e.sender_id}: не записано ({type(exc).__name__})")
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
    # Проверки на бота: кнопка в новом сообщении бота нажимается сама, если она
    # из белого списка (сообщения обычных людей без кнопок проверки не трогаем).
    client.add_event_handler(on_gate, events.NewMessage(incoming=True))
    print("watch запущен: новые группы добавляются автоматически, «стоп» в DM -> blacklist, "
          "кнопки проверки ботов нажимаются сами. Ctrl+C — выход.")
    await client.run_until_disconnected()


def record_reply(conn, sender_id, chat_id=None, msg_id=None, text="", now=None):
    """Человек ответил — единственный твёрдый сигнал «сообщение дошло».

    Ищет нашу отправку этому человеку: сначала по reply на конкретное
    сообщение, затем по свежей (последние 30 дней) отправке в этот чат.
    Возвращает id записи sent или None, если ответили не на рассылку.
    """
    now = now or datetime.now(timezone.utc)
    iso = now.isoformat(timespec="seconds")
    uid = int(sender_id)
    if msg_id:
        row = conn.execute(
            "SELECT s.rowid rid FROM sent s JOIN campaigns c ON c.id=s.campaign "
            "WHERE s.msg_id=? AND c.kind='dm' AND s.replied_at IS NULL",
            (int(msg_id),)).fetchone()
        if row:
            conn.execute("UPDATE sent SET replied_at=? WHERE rowid=?", (iso, row["rid"]))
            return row["rid"]
    cutoff = (now - timedelta(days=30)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT s.rowid rid FROM sent s JOIN campaigns c ON c.id=s.campaign "
        "WHERE s.target=? AND c.kind='dm' AND s.status='ok' AND s.replied_at IS NULL "
        "AND s.ts>=? ORDER BY s.ts DESC LIMIT 1", (str(uid), cutoff)).fetchone()
    if row:
        conn.execute("UPDATE sent SET replied_at=? WHERE rowid=?", (iso, row["rid"]))
        return row["rid"]
    return None


def record_block(conn, sender_id, now=None):
    """Человек заблокировал нас: помечаем последнюю доставку и выводим из базы."""
    now = now or datetime.now(timezone.utc)
    iso = now.isoformat(timespec="seconds")
    conn.execute("UPDATE sent SET blocked_at=? WHERE rowid=("
                 "SELECT s.rowid FROM sent s JOIN campaigns c ON c.id=s.campaign "
                 "WHERE s.target=? AND c.kind='dm' AND s.status='ok' "
                 "ORDER BY s.ts DESC LIMIT 1)", (iso, str(int(sender_id))))
    conn.execute("UPDATE members SET dm_block='user_blocked' WHERE user_id=? "
                 "AND COALESCE(dm_block,'')=''", (int(sender_id),))


def delivery_report(conn, days=7, now=None):
    """Что на самом деле дала рассылка: принято, дошло, заблокировали.

    reply  — человек ответил на наше сообщение (доставка доказуема);
    blocked — после доставки заблокировал (тоже доказуемо, но со знаком минус);
    silent — принято Telegram, реакции нет; про такое нельзя сказать «доставлено».
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT c.kind kind, COUNT(*) total,"
        " SUM(CASE WHEN s.status='ok' THEN 1 ELSE 0 END) accepted,"
        " SUM(CASE WHEN s.replied_at IS NOT NULL THEN 1 ELSE 0 END) replied,"
        " SUM(CASE WHEN s.blocked_at IS NOT NULL THEN 1 ELSE 0 END) blocked,"
        " SUM(CASE WHEN s.status LIKE 'fail:%' THEN 1 ELSE 0 END) failed"
        " FROM sent s JOIN campaigns c ON c.id=s.campaign WHERE s.ts>=? "
        "GROUP BY c.kind ORDER BY c.kind", (cutoff,)).fetchall()
    out = []
    for r in rows:
        accepted = r["accepted"] or 0
        replied = r["replied"] or 0
        blocked = r["blocked"] or 0
        out.append({
            "kind": r["kind"], "total": r["total"],
            "accepted": accepted, "replied": replied, "blocked": blocked,
            "failed": r["failed"] or 0,
            "silent": max(0, accepted - replied - blocked),
            "reply_rate": (100.0 * replied / accepted) if accepted else 0.0,
            "block_rate": (100.0 * blocked / accepted) if accepted else 0.0,
        })
    return out


def campaign_report(conn, days=30, now=None, min_sample=1):
    """Разрез по кампаниям: что реально дало ответы, а что сожгло пул.

    Отвечает на вопрос «какой оффер работает», а не «сколько мы отправили»:
    reply_rate и block_rate считаются по принятым отправкам, а не по плану.
    Кампании с сэмплом меньше min_sample помечаются как «мало данных».
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT c.id id, c.kind kind, c.account account, c.created_at created_at, "
        "       c.text text,"
        "       COUNT(*) total,"
        "       SUM(CASE WHEN s.status='ok' THEN 1 ELSE 0 END) accepted,"
        "       SUM(CASE WHEN s.replied_at IS NOT NULL THEN 1 ELSE 0 END) replied,"
        "       SUM(CASE WHEN s.blocked_at IS NOT NULL THEN 1 ELSE 0 END) blocked,"
        "       SUM(CASE WHEN s.status LIKE 'fail:%' THEN 1 ELSE 0 END) failed "
        "FROM campaigns c JOIN sent s ON s.campaign=c.id "
        "WHERE s.ts>=? GROUP BY c.id ORDER BY c.created_at DESC",
        (cutoff,)).fetchall()
    out = []
    for r in rows:
        accepted = r["accepted"] or 0
        replied = r["replied"] or 0
        blocked = r["blocked"] or 0
        out.append({
            "id": r["id"], "kind": r["kind"], "account": r["account"],
            "created_at": r["created_at"],
            "preview": (r["text"] or "").replace("\n", " ")[:60],
            "total": r["total"], "accepted": accepted, "replied": replied,
            "blocked": blocked, "failed": r["failed"] or 0,
            "silent": max(0, accepted - replied - blocked),
            "reply_rate": (100.0 * replied / accepted) if accepted else 0.0,
            "block_rate": (100.0 * blocked / accepted) if accepted else 0.0,
            "low_confidence": accepted < min_sample,
        })
    return out


def source_report(conn, days=30, now=None, min_sample=1):
    """Разрез по группам-источникам: откуда пришли те, кто отвечает.

    Люди собираются из групп (members.source_group), поэтому именно источник
    показывает, какая аудитория живая. Для групп, где человек состоит в
    нескольких, берётся основной source_group участника — без размножения
    одной отправки на несколько источников.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT COALESCE(m.source_group, 0) gid,"
        "       COALESCE(g.title, '(без источника)') title,"
        "       COUNT(*) total,"
        "       SUM(CASE WHEN s.status='ok' THEN 1 ELSE 0 END) accepted,"
        "       SUM(CASE WHEN s.replied_at IS NOT NULL THEN 1 ELSE 0 END) replied,"
        "       SUM(CASE WHEN s.blocked_at IS NOT NULL THEN 1 ELSE 0 END) blocked "
        "FROM sent s JOIN campaigns c ON c.id=s.campaign "
        "LEFT JOIN members m ON c.kind='dm' AND m.user_id=CAST(s.target AS INTEGER) "
        "LEFT JOIN groups g ON g.id=m.source_group "
        "WHERE s.ts>=? GROUP BY COALESCE(m.source_group, 0)",
        (cutoff,)).fetchall()
    out = []
    for r in rows:
        accepted = r["accepted"] or 0
        replied = r["replied"] or 0
        blocked = r["blocked"] or 0
        out.append({
            "group_id": r["gid"], "title": r["title"],
            "total": r["total"], "accepted": accepted, "replied": replied,
            "blocked": blocked,
            "reply_rate": (100.0 * replied / accepted) if accepted else 0.0,
            "block_rate": (100.0 * blocked / accepted) if accepted else 0.0,
            "low_confidence": accepted < min_sample,
        })
    out.sort(key=lambda x: (-x["reply_rate"], -x["accepted"]))
    return out


def experiment_verdict(campaigns, min_sample=10):
    """Что делать дальше — по данным, а не по ощущению.

    no_data    — мало принятых: расширять выборку бессмысленно, сначала объём;
    stop_copy  — ответов нет ни на одной кампании: проблема в тексте/аудитории;
    scale_winner — есть кампания с ответами: увеличивать именно её;
    mixed      — данные есть, но разница в пределах шума.
    """
    with_data = [c for c in campaigns if c["accepted"] >= min_sample]
    if not with_data:
        return "no_data"
    best = max(with_data, key=lambda c: c["reply_rate"])
    if all(c["replied"] == 0 for c in with_data):
        return "stop_copy"
    if best["reply_rate"] >= 5.0:
        return "scale_winner"
    return "mixed"


def username_is_bot(username):
    """Ник Telegram-бота обязан заканчиваться на bot. Имена вроде Subbotina и Rabota — нет."""
    return bool(username) and str(username).lower().endswith("bot")


def member_is_bot(u):
    return int(bool(getattr(u, "bot", False)) or username_is_bot(getattr(u, "username", None)))


# --- отсев «твинков» и очевидных ботов в личной рассылке ---------------------
# Правила собраны по реальной базе: накрученные/автогенерированные ники,
# рекламные имена и аккаунты-пустышки. Сомнительная цель пропускается, а не
# досылается: жалоба стоит дороже, чем один недосланный контакт.
DM_JUNK_NAME = re.compile(
    r"(@\w+bot\b"                 # имя рекламирует другого бота
    r"|t\.me/"
    r"|m[yv]\s*age\s*app"        # массовый автоспам-апп «MY AGE APP»
    r"|\b(?:casino|bet|ставк|казино|giveaway|giveaways|разыгр|airdrop|crypto)\b"
    r"|поддержк|support|уведомл|рассылк"
    r")",
    re.IGNORECASE,
)
# Хвост из 4+ цифр — автогенерированный ник (Kami072895, Willie6531).
# По реальной базе таких 1 046 против 339 с 5+ цифрами; живые люди с годом
# в нике (masha_2024) редки и попадают в тот же риск-профиль массового слива.
DM_NUMERIC_TAIL = re.compile(r"\d{4,}$")
DM_PHONE_LIKE = re.compile(r"\d{7,}")         # Elainaa_74825648562
_WORD_CHAR = re.compile(r"[^\W\d_]", re.UNICODE)


def member_username_is_twin(username):
    """Ник похож на автогенерированный твинк: длинный числовой хвост."""
    u = str(username or "")
    if not u:
        return False
    return bool(DM_NUMERIC_TAIL.search(u) or DM_PHONE_LIKE.search(u))


def member_name_is_junk(first_name, last_name=""):
    """Имя-пустышка: пусто, 1 символ, только эмодзи или реклама в самом имени."""
    name = f"{first_name or ''} {last_name or ''}".strip()
    if len(name) <= 1:
        return True
    if not _WORD_CHAR.search(name):
        return True
    return bool(DM_JUNK_NAME.search(name))


def classify_dm_target(row):
    """Стоит ли писать этой цели. (ok, reason); reason пустой, если писать можно."""
    if int(_field(row, "is_bot", 0) or 0) or username_is_bot(_field(row, "username", None)):
        return False, "bot"
    if member_username_is_twin(_field(row, "username", "")):
        return False, "twin_username"
    if member_name_is_junk(_field(row, "first_name", ""), _field(row, "last_name", "")):
        return False, "junk_name"
    return True, ""


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


SPAM_ERRORS = {
    "PeerFloodError",          # 403 PEER_FLOOD — аккаунт ограничен как спамер
    "UserRestrictedError",     # 403 USER_RESTRICTED — ограниченный аккаунт
}
# Пауза аккаунта на время сервера. SlowModeWaitError сюда не входит: это лимит чата.
ACCOUNT_FLOOD_ERRORS = {"FloodWaitError", "FloodPremiumWaitError"}
# Право ограниченного аккаунта писать незнакомцам теряет вся серия DM (FAQ: 24 ч × 2^n),
# даже если технический FloodWait давно истёк.
FLOOD_JITTER = 1.15          # 15% сверху к серверной паузе, чтобы не биться синхронно

# Бюджет DM по возрасту аккаунта (не официальный лимит Telegram, а эвристика
# из docs/telegram/limits-and-antispam.md). Ключ — code из account_cohort.
COHORT_BUDGET = {"fresh": 15, "warm": 30, "mature": 60, "unknown": 20}
COHORT_RU = {
    "fresh": "свежий (<2 недель)",
    "warm": "прогретый (2 недели — 3 месяца)",
    "mature": "зрелый (>3 месяцев)",
    "unknown": "дата регистрации неизвестна",
}

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
    "TOPIC_CLOSED": "topic_closed",
    "ALLOW_PAYMENT_REQUIRED": "payment",
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
    "payment": "чат требует оплату сообщения",
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


def _error_tokens(exc):
    """Имя класса и код сервера. Telethon 1.45 отдаёт TOPIC_CLOSED и
    ALLOW_PAYMENT_REQUIRED_1 общим BadRequestError/ForbiddenError."""
    names = [_error_name(exc)]
    if isinstance(exc, str):
        return names
    message = str(getattr(exc, "message", "") or "").strip()
    if not message:
        return names
    code = message.split()[0]
    if code and code not in names:
        names.append(code)
    if len(code) > 2 and code.rsplit("_", 1)[-1].isdigit():
        base = code.rsplit("_", 1)[0]
        if base not in names:
            names.append(base)
    return names


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
    table = PERMANENT_DM if kind == "dm" else PERMANENT_BROADCAST
    for name in _error_tokens(exc):
        reason = table.get(name)
        if reason:
            return reason
    return None


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
    if permanent_reason(exc, kind):
        return "remember"
    return "fail"


def flood_pause_until(exc, now=None):
    """Пауза на число секунд от сервера (+15% jitter). Минимум 1 с."""
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(seconds=flood_wait_seconds(exc))).isoformat(timespec="seconds")


def flood_wait_seconds(exc, jitter=None):
    """Сколько реально спать после FloodWait: секунды сервера + jitter.
    Минимум 1 с, чтобы не крутить пустой цикл."""
    try:
        seconds = int(getattr(exc, "seconds", 0) or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds < 1:
        seconds = 1
    factor = FLOOD_JITTER if jitter is None else max(1.0, float(jitter))
    # Округление вверх: пауза никогда не короче серверной, 100 с → 115 с.
    return min(math.ceil(seconds * factor), 86400)


def peerflood_backoff(strikes):
    """Пауза после PEER_FLOOD. Первый раз — сутки, дальше экспоненциально,
    с потолком 30 дней: «повторные нарушения — дольше, упорство — навсегда»."""
    n = max(1, int(strikes or 1))
    hours = min(24 * (2 ** (n - 1)), 24 * 30)
    return timedelta(hours=hours)


def peerflood_pause_until(strikes=1, now=None):
    now = now or datetime.now(timezone.utc)
    return (now + peerflood_backoff(strikes)).isoformat(timespec="seconds")


def account_cohort(age_days, config_cohort="auto"):
    """Контингент аккаунта по возрасту: fresh | warm | mature | unknown."""
    if config_cohort and config_cohort != "auto":
        return config_cohort
    if age_days is None:
        return "unknown"
    try:
        days = float(age_days)
    except (TypeError, ValueError):
        return "unknown"
    if days < 14:
        return "fresh"
    if days < 90:
        return "warm"
    return "mature"


def cohort_budget(cohort, base=None):
    """Бюджет DM на сутки. Явный бюджет аккаунта не понижает, но не поднимает
    выше потолка контингента: age не обходится флагом CLI."""
    cap = COHORT_BUDGET.get(cohort, COHORT_BUDGET["unknown"])
    if base is None or int(base) <= 0:
        return cap
    return min(int(base), cap)


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


BOTH_SLEEP_CAP = 30
DIALOG_REFRESH_SECONDS = 15 * 60
DM_MIN_GAP = 20.0
GROUP_PAUSE = (3.0, 8.0)
DM_PAUSE = (20.0, 45.0)
_LINK_ERRORS = {
    "ConnectionError", "ConnectionResetError", "TimeoutError",
    "ServerError", "IncompleteReadError", "ConnectionErrorError",
}


def earliest_moment(now, deadlines):
    """Ближайший момент в будущем. None, если ждать нечего."""
    future = []
    for item in deadlines:
        if not item:
            continue
        if isinstance(item, str):
            try:
                item = datetime.fromisoformat(item)
            except ValueError:
                continue
        if item.tzinfo is None:
            item = item.replace(tzinfo=timezone.utc)
        if item > now:
            future.append(item)
    return min(future) if future else None


def next_utc_midnight(now):
    now = now.astimezone(timezone.utc)
    nxt = now.date() + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=timezone.utc)


def wait_seconds(now, moment, cap=BOTH_SLEEP_CAP):
    """Сколько спать до момента, но не дольше cap: так Стоп замечается без долгой паузы."""
    if moment is None:
        return float(cap)
    return max(0.0, min(float(cap), (moment - now).total_seconds()))


def reassign_groups_on_flood(name, pending, players, edges):
    """Ещё не отправленные группы уходят тому, кто может писать и у кого есть бюджет.

    Сообщение, на котором пришёл FloodWait, в pending не входит: оно остаётся name.
    used у принимающего растёт, чтобы пачка не превысила остаток.
    """
    stay, moves = [], []
    for row in pending:
        other = handoff_broadcast(row, name, players, edges)
        if other:
            moves.append((other, row))
            players[other]["used"] = players[other].get("used", 0) + 1
        else:
            stay.append(row)
    return stay, moves


def hold_dm_queue(current, pending):
    """PeerFlood: люди из очереди этого аккаунта никому не передаются."""
    held = []
    if current is not None:
        held.append(current)
    held.extend(pending)
    return held


def both_should_continue(dry_run, stopped):
    """Сухой план печатается один раз. Боевой цикл живёт, пока не попросили стоп."""
    return not dry_run and not stopped


def next_channel(has_group, has_dm, since_any, last_kind, dm_gap=DM_MIN_GAP):
    """Что слать следующим с одной сессии. None — рано для личного, групп нет."""
    dm_ready = has_dm and (since_any is None or since_any >= dm_gap)
    if dm_ready and (not has_group or last_kind == "broadcast"):
        return "dm"
    if has_group:
        return "broadcast"
    if dm_ready:
        return "dm"
    return None


def channel_open(player, kind, now=None):
    if kind == "dm" and cooldown_remaining({"flood_until": player.get("dm_halt_until")}, now):
        return False
    if kind == "dm" and cooldown_remaining({"flood_until": player.get("flood_until")}, now) \
            and int(player.get("spam_strikes") or 0) > 0:
        # PEER_FLOOD: ограниченный аккаунт не шлёт DM даже после конца общего cooldown.
        return False
    cap = player.get("broadcast_budget") if kind == "broadcast" else player.get("dm_budget")
    used = player.get("broadcast_used") if kind == "broadcast" else player.get("dm_used")
    if not cap:
        return True
    return int(used or 0) < int(cap)


def register_peerflood(conn, account, now=None):
    """PEER_FLOOD засчитывается аккаунту: +1 страйк и backoff от числа страйков.
    Чистые дни без спама снимают страйки (см. decay_peerflood)."""
    now = now or datetime.now(timezone.utc)
    cur = conn.execute("SELECT COALESCE(spam_strikes, 0) s FROM accounts WHERE name=?",
                       (account,)).fetchone()
    strikes = int((cur["s"] if cur else 0) or 0) + 1
    fu = peerflood_pause_until(strikes, now)
    try:
        conn.execute("UPDATE accounts SET spam_strikes=?, last_peerflood_at=?, flood_until=? "
                     "WHERE name=?", (strikes, now.isoformat(timespec="seconds"), fu, account))
    except sqlite3.OperationalError:
        # Старая схема без колонок — пауза всё равно важнее счётчика.
        conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, account))
    return strikes


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
        # Твинки и очевидный мусор: та же логика, что в member_username_is_twin /
        # member_name_is_junk, но в SQL, чтобы отсекалось до выборки в Python.
        "COALESCE(m.username, '') NOT GLOB '*[0-9][0-9][0-9][0-9]'",
        "COALESCE(m.first_name, '') NOT GLOB '*@[A-Za-z0-9_]*[bB][oO][tT]*'",
        "COALESCE(m.first_name, '') NOT GLOB '*[tT].[mM][eE]/*'",
        "LOWER(COALESCE(m.first_name, '')) NOT LIKE '%age app%'",
        "LOWER(COALESCE(m.first_name, '')) NOT LIKE '%поддержк%'",
        "LOWER(COALESCE(m.first_name, '')) NOT LIKE '%support%'",
        "LOWER(COALESCE(m.first_name, '')) NOT LIKE '%casino%'",
        "LOWER(COALESCE(m.first_name, '')) NOT LIKE '%crypto%'",
    ]
    if not repeat_ok:
        # CAST остаётся: цель «05» всё ещё исключает user_id 5.
        # Обход с campaigns.kind использует idx_campaigns_kind и
        # idx_sent_campaign_status_target вместо полного скана sent.
        parts.append(
            "m.user_id NOT IN (SELECT CAST(s.target AS INTEGER) FROM campaigns c "
            "JOIN sent s ON s.campaign=c.id AND s.status='ok' WHERE c.kind='dm')"
        )
    return " AND ".join(parts)


# Тот же предикат, что у dmsend по умолчанию: status и панель считают одну очередь.
DM_ELIGIBLE_WHERE = dm_eligible_where(False)
NOT_YET_DMED = (
    "NOT EXISTS (SELECT 1 FROM sent s JOIN campaigns c ON c.id=s.campaign "
    "WHERE c.kind='dm' AND s.status='ok' AND s.target=CAST(m.user_id AS TEXT))"
)


_MEMBER_INSERT = (
    "INSERT OR IGNORE INTO members(user_id,username,first_name,last_name,is_bot,collected_at,"
    "source_group,deleted,access_hash) VALUES(?,?,?,?,?,?,?,?,?)"
)
SCRAPE_CHUNK = 500


def save_members(conn, users, gid):
    """Пакетная вставка участников одной группы. Коммит остаётся на вызывающем.

    Возвращает число новых строк members. Повторный scrape не переписывает
    ник, имена, collected_at и source_group. is_bot и deleted только
    поднимаются в 1, access_hash пишется, когда новое значение не None.
    """
    if not users:
        return 0
    collected_at = now_iso()
    rows = []
    bot_ids = []
    deleted_ids = []
    hashes = []
    sources = []
    for user in users:
        uname = user.username or ""
        is_bot = member_is_bot(user)
        deleted = int(bool(getattr(user, "deleted", False)))
        access_hash = getattr(user, "access_hash", None)
        if access_hash is not None:
            access_hash = int(access_hash)
        rows.append((
            user.id, uname, user.first_name or "", user.last_name or "", is_bot,
            collected_at, gid, deleted, access_hash,
        ))
        if is_bot:
            bot_ids.append((user.id,))
        if deleted:
            deleted_ids.append((user.id,))
        if access_hash is not None:
            hashes.append((access_hash, user.id, access_hash))
        sources.append((user.id, gid))
    before = conn.total_changes
    conn.executemany(_MEMBER_INSERT, rows)
    new_count = conn.total_changes - before
    # INSERT OR IGNORE не обновляет уже существующую строку — иначе ник *bot
    # навсегда остаётся is_bot=0, если первый scrape записал его человеком.
    if bot_ids:
        conn.executemany(
            "UPDATE members SET is_bot=1 WHERE user_id=? AND COALESCE(is_bot, 0)=0",
            bot_ids)
    if deleted_ids:
        conn.executemany(
            "UPDATE members SET deleted=1 WHERE user_id=? AND COALESCE(deleted, 0)=0",
            deleted_ids)
    if hashes:
        conn.executemany(
            "UPDATE members SET access_hash=? WHERE user_id=? AND access_hash IS NOT ?",
            hashes)
    conn.executemany(
        "INSERT OR IGNORE INTO member_sources(user_id,group_id) VALUES(?,?)",
        sources)
    return new_count


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
        while True:
            pending = []

            def flush_pending():
                nonlocal n
                if not pending:
                    return
                n += save_members(conn, pending, gid)
                # Коммит на каждую порцию: иначе открытая write-транзакция
                # держится всю группу (10k участников = 20 порций подряд) и
                # второй параллельный scrape падает с "database is locked",
                # не дождавшись даже busy_timeout=15s.
                conn.commit()
                pending.clear()

            try:
                async for u in client.iter_participants(ent):
                    pending.append(u)
                    if len(pending) >= SCRAPE_CHUNK:
                        flush_pending()
                flush_pending()
                break
            except FloodWaitError as e:
                flush_pending()
                conn.commit()
                if e.seconds > 600:  # часовые/суточные паузы — смысл ждать теряет
                    conn.commit()
                    sys.exit(f"FloodWait {e.seconds} c (>10 мин) — прерываю scrape; "
                             f"сохранённое не потеряно, смотри status и повтори scrape")
                print(f"  FloodWait {e.seconds} c в группе {gid} — жду и повторяю", flush=True)
                await asyncio.sleep(e.seconds + 1)
                continue
            except Exception as e:
                flush_pending()
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


# Строка members в том виде, в каком её отдают план и dm_targets: тот же набор
# полей и тот же доступ по ключу, что у sqlite3.Row из SELECT m.*.
_MEMBER_COLUMNS = ("user_id", "username", "first_name", "last_name", "is_bot",
                   "collected_at", "source_group", "deleted", "access_hash", "dm_block")

# Отсеваемые слова в имени: пять NOT LIKE '%слово%' из dm_eligible_where.
# В SQL каждое — отдельный вызов LOWER, а LOWER в SQLite не встроенная функция:
# эти проверки на 12.5 тыс. строк стоили дороже чтения всей таблицы.
# Тот же набор слов здесь — одно регулярное выражение.
_NAME_JUNK_RE = re.compile(r"age app|поддержк|support|casino|crypto")


def _lower_like_sqlite(value):
    """Нижний регистр ровно как у SQLite LOWER: меняются только ASCII-буквы."""
    if value.isascii():
        return value.lower()
    return "".join(ch.lower() if ch.isascii() else ch for ch in value)


def _name_junk(first_name):
    """То же, что LOWER(first_name) NOT LIKE '%<слово>%' в dm_eligible_where."""
    if not first_name:
        return False
    return _NAME_JUNK_RE.search(_lower_like_sqlite(first_name)) is not None


def _dm_candidate_ok(row):
    """Python-половина очереди DM для строки (user_id, username, first_name, access_hash).

    Повторяет ровно те условия dm_eligible_where, которые не проверяет ни
    индекс, ни встроенные NOT GLOB из _CANDIDATE_SQL. Именно они дорого
    стоили в SQL: LOWER(COALESCE(username,'')) NOT LIKE '%bot' и пять
    LOWER(first_name) LIKE-подстрок. Имена вроде 'Rabota' и 'Subbotina'
    остаются, ники на '...bot' отсекаются регистронезависимо.
    """
    username = row[1] or ""
    if username:
        if _lower_like_sqlite(username).endswith("bot"):
            return False
    elif row[3] is None:          # (username != '' OR access_hash IS NOT NULL)
        return False
    return not _name_junk(row[2] or "")


def _dm_excluded_users(conn, repeat_ok=False):
    """Цели, которых не должно быть в очереди: чёрный список, свои аккаунты,
    прошлые успешные DM.

    Раньше это были три NOT IN-подзапроса, которые SQLite пересчитывал на
    каждой строке members (36 строк sent — но 12 500 проверок). Набор — сотни
    id, поэтому сверка по множеству в Python дешевле и читается проще.
    """
    excluded = {row[0] for row in conn.execute(
        "SELECT user_id FROM accounts WHERE user_id IS NOT NULL")}
    excluded |= {row[0] for row in conn.execute("SELECT user_id FROM blacklist")}
    if not repeat_ok:
        excluded |= {row[0] for row in conn.execute(
            "SELECT CAST(s.target AS INTEGER) FROM campaigns c "
            "JOIN sent s ON s.campaign=c.id AND s.status='ok' WHERE c.kind='dm'")}
    return excluded


_MEMBER_BY_IDS_SQL = (
    "SELECT m.user_id, m.username, m.first_name, m.last_name, m.is_bot, "
    "m.collected_at, m.source_group, m.deleted, m.access_hash, m.dm_block "
    "FROM members m JOIN json_each(?) j ON m.user_id = j.value"
)


def _fetch_rows(conn, ids):
    """Строки members для набора user_id в переданном порядке, в порядке _MEMBER_COLUMNS."""
    ids = list(ids)
    if not ids:
        return []
    by_id = {}
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        for row in conn.execute(_MEMBER_BY_IDS_SQL, (json.dumps(chunk),)):
            by_id[row["user_id"]] = row
    return [by_id[uid] for uid in ids if uid in by_id]


# Кандидат очереди DM со всем, что нужно для отсева без чтения строки таблицы:
# user_id и collected_at лежат в самой записи индекса, username и first_name
# читаются только для 1000-строчных окон.
_CANDIDATE_SQL = (
    "SELECT m.user_id, m.username, m.first_name, m.access_hash, m.collected_at "
    "FROM members m INDEXED BY idx_members_dm_collected "
    "WHERE m.is_bot=0 AND COALESCE(m.deleted, 0)=0 AND COALESCE(m.dm_block, '')='' "
    # Встроенные проверки: NOT GLOB сравнивает сырой текст, без LOWER на строку.
    "AND COALESCE(m.username, '') NOT GLOB '*[0-9][0-9][0-9][0-9]' "
    "AND COALESCE(m.first_name, '') NOT GLOB '*@[A-Za-z0-9_]*[bB][oO][tT]*' "
    "AND COALESCE(m.first_name, '') NOT GLOB '*[tT].[mM][eE]/*'"
)


def candidate_ids(conn):
    """Курсор кандидатов очереди по частичному индексу в порядке collected_at."""
    return conn.execute(_CANDIDATE_SQL + " ORDER BY m.collected_at")


def dm_targets(conn, group_id=None, repeat_ok=False, limit=None, shuffle=False):
    """Очередь DM: боты, удалённые, без адреса, dm_block, blacklist и прошлые DM исключены.

    Пустой профиль с username или access_hash остаётся: его можно адресовать.
    Имена людей, внутри которых есть буквы bot (Subbotina, Rabota), не отсекаются.
    shuffle=True — случайная выборка из всей базы: пачка не липнет к одному
    времени сбора (и одной группе), и свежие люди попадают в неё наравне.

    Отбор идёт по кандидатам индекса (5 колонок на 12.6 тыс. строк — ~18 мс),
    и LIMIT срабатывает внутри этого прохода. Полные строки members читаются
    только для отобранных id: на круг conduct --limit 200 очередь стоит ~1 мс
    против ~62 мс, когда весь предикат dm_eligible_where шёл в SQL одним
    запросом. Результат при том же наборе строк тот же — это сверяет
    test_dm_queue_matches_sql_predicate.
    """
    excluded = _dm_excluded_users(conn, repeat_ok)
    if group_id:
        # Фильтр по группе остаётся в SQL: он сужает выборку сильнее всего.
        # Всё остальное — тот же двухэтапный отбор, что и без группы.
        group_sql = (
            " AND m.user_id IN "
            "(SELECT user_id FROM member_sources WHERE group_id=?) "
            "ORDER BY m.collected_at"
        )
        keep = []
        for row in conn.execute(_CANDIDATE_SQL + group_sql, (group_id,)):
            if row[0] in excluded or not _dm_candidate_ok(row):
                continue
            keep.append(row)
            if limit and not shuffle and len(keep) >= limit:
                break
    else:
        keep = []
        for row in candidate_ids(conn):
            if row[0] in excluded or not _dm_candidate_ok(row):
                continue
            keep.append(row)
            if limit and not shuffle and len(keep) >= limit:
                break
    if shuffle and limit:
        # Случайные цели: выборка из всех кандидатов, а не из первых по времени сбора.
        keep = random.sample(keep, min(limit, len(keep)))
    rows = _fetch_rows(conn, [row[0] for row in keep])
    if shuffle:
        random.shuffle(rows)
    return rows if not limit else rows[:limit]


def recent_ok_targets(conn, kind, hours=24):
    """Цели, которым успешно писали этого вида за последние N часов (все аккаунты)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return {str(r["target"]) for r in conn.execute(
        "SELECT DISTINCT s.target FROM sent s JOIN campaigns c ON c.id=s.campaign "
        "WHERE c.kind=? AND s.status='ok' AND s.ts>=?", (kind, cutoff))}


# --------------------------------------------- эффективность рассылки
# Персонализация {name}/{username}/{chat}/{date} и варианты [[a|b]] на каждую
# цель: соседние группы/получатели не получают символ-в-символ один текст —
# это главный признак массовой рассылки и повод для жалоб.

SPINTAX_RE = re.compile(r"\[\[([^\[\]]*\|[^\[\]]*)\]\]")
OPT_OUT_FOOTER = "Если не интересно — ответьте «стоп», больше не напишу."
# Только целое слово: «стопка» и «автостоп» не должны отменять футер.
OPT_OUT_MARK_RE = re.compile(r"\b(?:стоп|stop)\b", re.I)
CAPTION_LIMIT = 1024
DRY_PREVIEWS = 3  # сколько отрендеренных примеров печатать в --dry-run


def _drop_placeholder(text, token):
    """Убирает пустой плейсхолдер вместе с висящим рядом разделителем."""
    text = re.sub(r"[ \t]*[,;:—–-][ \t]*" + re.escape(token), "", text)
    text = re.sub(re.escape(token) + r"[ \t]*[,;:—–-][ \t]*", "", text)
    return text.replace(token, "")


def _tidy_message(text):
    """Косметика после подстановок: «Привет, !» -> «Привет!», «()» -> пусто."""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,;:!?])", r"\1", text)
    text = re.sub(r"([,;:!?])(?=[!?])", "", text)
    text = re.sub(r"[ \t]*\(\s*\)", "", text)
    text = re.sub(r"@@+", "@", text)   # шаблон «@{username}» не даёт «@@anna»
    text = re.sub(r"[ \t]*@(?![0-9A-Za-z_])", "", text)  # пустой {username} не оставляет «@»
    return text.strip()


def render_message(template, context=None, rng=None):
    """Подставляет данные цели и выбирает вариант текста.

    context: name/first_name, last_name, full_name, username, chat, date.
    Пустое значение не оставляет дыр: «Привет, {name}!» без имени -> «Привет!».
    Неизвестные {ключи} и [[скобки без палки]] остаются как есть.
    """
    if not template:
        return template
    ctx = dict(context or {})
    ctx.setdefault("date", datetime.now().strftime("%d.%m.%Y"))
    first = str(ctx.get("name") or ctx.get("first_name") or "").strip()
    last = str(ctx.get("last_name") or "").strip()
    username = str(ctx.get("username") or "").strip().lstrip("@")
    values = {
        "name": first,
        "first_name": first,
        "full_name": " ".join(item for item in (first, last) if item).strip(),
        "username": "@" + username if username else "",
        "chat": str(ctx.get("chat") or ctx.get("group") or "").strip(),
        "group": str(ctx.get("group") or ctx.get("chat") or "").strip(),
        "date": str(ctx.get("date") or ""),
    }
    rng = rng or random

    def pick(match):
        options = [part.strip() for part in match.group(1).split("|")]
        options = [part for part in options if part]
        return rng.choice(options) if options else ""

    text = SPINTAX_RE.sub(pick, template)
    for key, value in values.items():
        token = "{" + key + "}"
        if token not in text:
            continue
        text = text.replace(token, value) if value else _drop_placeholder(text, token)
    return _tidy_message(text)


def with_opt_out(text, enabled=True):
    """Просьба ответить «стоп» снижает жалобы — главную причину бана аккаунта."""
    if not enabled:
        return text
    if OPT_OUT_MARK_RE.search(text or ""):
        return text
    return f"{text}\n\n{OPT_OUT_FOOTER}" if text else OPT_OUT_FOOTER


def caption_text(text, limit=CAPTION_LIMIT):
    """Подпись к медиа ограничена Telegram (1024 символа), длинную режем явно.

    Режем по границе слова, если она не слишком далеко от лимита: обрывок
    посреди слова выглядит как сбой, а не как сокращение.
    """
    if text is None or len(text) <= limit:
        return text
    cut = text[:max(limit - 1, 0)]
    space = cut.rfind(" ")
    if space > 0 and space >= (limit - 1) * 0.6:
        cut = cut[:space]
    return cut.rstrip() + "…"


def web_page_preview():
    """Флаг «ссылка без предпросмотра» для messages.sendMessage.

    Telethon 1.45 не умеет custom webpage preview (в send_message нет параметра
    webpage), а телеграм-клиенты без MTProto-обхода получают его только через
    UpdateBotWebhookJSONQuery. Поэтому честный потолок — выключить превью:
    no_webpage=True даёт ту же полосу под текстом, что и официальный клиент,
    но без развёрнутой карточки ссылки и, главное, без её же кеша."""
    return {"no_webpage": True}


def prepare_message(text, context=None, opt_out=False, media=False):
    """Готовое тело отправки: рендер -> подпись -> опт-аут в одном месте.

    Для медиа подпись ограничена 1024 символами, поэтому текст укорачивается
    с запасом под футер: просьба ответить «стоп» не должна исчезать из-за лимита.
    """
    body = render_message(text, context)
    if media:
        if opt_out:
            room = CAPTION_LIMIT - len(OPT_OUT_FOOTER) - 2
            body = with_opt_out(caption_text(body or "", room))
        else:
            body = caption_text(body)
        return body
    return with_opt_out(body) if opt_out else body


async def _dm_peer_known(client, peer, cache=None):
    """Знает ли этот аккаунт получателя — быстрый локальный резолв без сети.

    send_file/send_message по числовому user_id требуют entity в кэше сессии;
    иначе Telethon бросает ValueError «Could not find the input entity».
    Проверяем заранее и пропускаем незнакомых, а не тратим попытку.
    """
    if isinstance(peer, str) and peer.startswith("@"):
        return True                      # @username резолвится любым аккаунтом
    cache = cache if cache is not None else {}
    if peer in cache:
        return cache[peer]
    try:
        get_input = getattr(client.session, "get_input_entity", None)
        ok = bool(get_input and get_input(peer))
    except Exception:
        ok = False
    cache[peer] = ok
    return ok


def dm_peer(row):
    """Куда адресовать личное сообщение: @username, иначе int user_id.

    @username работает из любого аккаунта оркестра. access_hash для адресации
    не годится: он привязан к аккаунту, который делал scrape («каждый аккаунт
    видит своё значение»), и чужой hash даёт UserIdInvalidError — живой человек
    был бы ошибочно помечен мёртвым. Без ника int разрешается из кэша сессии.
    """
    username = str(_field(row, "username", "") or "").strip().lstrip("@")
    if username:
        return "@" + username
    user_id = _field(row, "user_id", None)
    return int(user_id) if user_id is not None else None


def dm_context(row):
    """Плейсхолдеры для личного сообщения."""
    first = str(_field(row, "first_name", "") or "")
    return {
        "name": first,
        "first_name": first,
        "last_name": str(_field(row, "last_name", "") or ""),
        "username": str(_field(row, "username", "") or ""),
        "chat": "",
    }


def chat_context(row):
    """Плейсхолдеры для сообщения в группу."""
    title = str(_field(row, "title", "") or "")
    return {"chat": title, "group": title, "name": ""}


def group_key(group_id):
    """Ключ группы в sent: у супергрупп/каналов Telethon отдаёт id без -100.

    join записывает группу с полным id (-100...), а в диалогах она приходит
    с "обрезанным" — без ключа один чат выглядел бы как два, и дедуп бы врал.
    """
    value = int(group_id)
    raw = abs(value)
    if raw > 10 ** 12 and str(raw).startswith("100"):
        raw = int(str(raw)[3:])
    return -raw if value < 0 else raw


def interleave_targets(rows, limit=None):
    """Порядок DM: по одному человеку из каждой группы-источника по кругу.

    Пачка из двадцати человек одной группы подряд — самый заметный признак
    рассылки (люди видят её в чате и жалуются). Круг по источникам рассыпает шаблон.
    """
    buckets = collections.OrderedDict()
    for row in rows:
        buckets.setdefault(_field(row, "source_group", None), collections.deque()).append(row)
    queues = collections.deque(buckets.values())
    out = []
    while queues:
        if limit and len(out) >= limit:
            break
        queue = queues.popleft()
        out.append(queue.popleft())  # deque: выборка одной группы не O(n^2)
        if queue:
            queues.append(queue)
    return out


def campaign_stats(conn, hours=168):
    """Итог за период по видам: ok / fail / skip / total. Основа для оценки."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT c.kind AS kind,"
        " SUM(CASE WHEN s.status='ok' THEN 1 ELSE 0 END) AS ok,"
        " SUM(CASE WHEN s.status LIKE 'fail:%' THEN 1 ELSE 0 END) AS fail,"
        " SUM(CASE WHEN s.status LIKE 'skip:%' THEN 1 ELSE 0 END) AS skip,"
        " COUNT(*) AS total"
        " FROM sent s JOIN campaigns c ON c.id=s.campaign"
        " WHERE s.ts>=? GROUP BY c.kind ORDER BY c.kind", (cutoff,)).fetchall()
    return [dict(row) for row in rows]


async def deliver_once(client, media, peer, body, no_preview=False):
    """Одна отправка: файл с подписью или обычный текст.

    no_preview=True — явно выключить предпросмотр ссылок (см. web_page_preview)."""
    if media:
        return await client.send_file(peer, media, caption=body or None,
                                      supports_streaming=True)
    if no_preview:
        from telethon.tl.functions.messages import SendMessageRequest
        return await client(SendMessageRequest(
            peer=peer, message=body or "", random_id=random.getrandbits(63),
            **web_page_preview()))
    return await client.send_message(peer, body)


async def deliver_with_reconnect(client, media, peer, body, on_retry=None, no_preview=False):
    """Отправить; при обрыве связи переподключить сессию и повторить один раз.

    Оборванный сокет не должен съедать цель: connect() поднимает ту же сессию,
    и сообщение уходит. Ошибки Telegram (FloodWait, приватность) не повторяем —
    их разбирает target_disposition.
    """
    try:
        return await deliver_once(client, media, peer, body, no_preview=no_preview)
    except Exception as exc:
        if type(exc).__name__ not in _LINK_ERRORS:
            raise
        if on_retry:
            on_retry()
        await client.connect()
        return await deliver_once(client, media, peer, body, no_preview=no_preview)


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
    done = ok = previews = 0
    halt = False
    halt_reason = ""
    # Просьбу ответить «стоп» добавляем только к личным: в группах она лишняя.
    opt_out = kind == "dm" and bool(getattr(args, "opt_out", True))
    no_preview = bool(getattr(args, "no_preview", False))
    pause_lo, pause_hi = ordered_pause(
        getattr(args, "delay_min", 0), getattr(args, "delay_max", 0))
    started_at = now_iso()
    for target in targets:
        if stop_requested(account, started_at):
            print(f"  остановка запрошена для «{account}»")
            break
        resolved = resolve(target)
        label, peer, key = resolved[0], resolved[1], resolved[2]
        context = resolved[3] if len(resolved) > 3 else {}
        if peer is None:
            print(f"  пропуск {label}: нет ни ника, ни access_hash")
            continue
        body = prepare_message(text, context, opt_out=opt_out, media=bool(media))
        if args.dry_run:
            # Показываем несколько примеров (варианты и персонализация), а не
            # простыню на всю очередь; футер «стоп» тоже виден в превью.
            if previews < DRY_PREVIEWS and body:
                flat = " / ".join(part.strip() for part in body.splitlines() if part.strip())
                print(f"  [dry] -> {label}: {flat[:120]}{'…' if len(flat) > 120 else ''}")
                previews += 1
            else:
                print(f"  [dry] -> {label}")
            continue
        if args.max_runs and done >= args.max_runs:
            print("  достигнут лимит --max-runs, стоп.")
            break
        if halt:
            break
        try:
            sent_msg = await deliver_with_reconnect(
                client, media, peer, body, no_preview=no_preview,
                on_retry=lambda: print(f"  связь «{account}» оборвалась, переподключаюсь"))
            conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account,msg_id) "
                         "VALUES(?,?,?,?,?,?)",
                         (camp, key, "ok", now_iso(), account, message_id(sent_msg)))
            ok += 1
            print(f"  ok  {label}")
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
            elif disp in ("halt_dm_queue", "halt_account"):
                strikes = register_peerflood(conn, account)
                fu = conn.execute("SELECT flood_until FROM accounts WHERE name=?",
                                  (account,)).fetchone()["flood_until"]
                conn.commit()
                print(f"  {type(e).__name__}: аккаунт «{account}» на паузе до {fu} "
                      f"(страйк {strikes}). Цель не помечена мёртвой и другому "
                      f"аккаунту не отдаётся.")
                halt = True
                halt_reason = type(e).__name__
            else:
                status = f"fail:{type(e).__name__}"
                if disp == "skip":
                    status = "skip:slowmode"
                elif disp == "remember":
                    remember_permanent(conn, kind, key, permanent_reason(e, kind), account)
                conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                             "VALUES(?,?,?,?,?)", (camp, key, status, now_iso(), account))
                print(f"  fail {label}: {type(e).__name__}: {str(e)[:120]}")
        conn.commit()
        done += 1
        if pause_hi > 0 and not halt and done < len(targets):
            await asyncio.sleep(random.uniform(pause_lo, pause_hi))
    suffix = " [dry-run, ничего не отправлено]" if args.dry_run else ""
    if halt:
        suffix += f" [остановлено: {halt_reason} — аккаунт в cooldown]"
    if args.dry_run:
        print(f"Сухой прогон ({kind}, «{account}»): целей {len(targets)}, "
              f"превью {min(previews, DRY_PREVIEWS)}, отправлено 0{suffix}")
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


def forward_ids(conn, args):
    """Что пересылать: --msg-id или последнее сохранённое через --forward-ids."""
    ids = list(getattr(args, "msg_id", None) or [])
    if ids:
        return ids
    args_dir = Path(os.environ.get("USERBOT_DIR", ROOT / "media_cache"))
    marker = args_dir / "forward-ids.json"
    if marker.exists():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
            return [int(x) for x in (saved.get("ids") or [])]
        except (OSError, ValueError, TypeError):
            return []
    return []


async def _filter_broadcast_targets(conn, account, rows, emap, args):
    """Общий отбор групп для broadcast: покрытие, дедуп, право записи.

    Вынесено из cmd_broadcast, чтобы режим пересылки шёл по тем же правилам."""
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
    return rows


async def resolve_source(client, source, dialogs=None):
    """Куда смотрит --from-source: @канал, id, t.me/... или ссылка t.me/c/...

    Возвращает объект peer из диалогов аккаунта. Источник обязан быть виден
    этому аккаунту: пересылать из закрытого канала может только его участник."""
    raw = str(source or "").strip()
    if not raw:
        sys.exit("--from-source пуст: укажите @канал, id, t.me/... или 'me' (Избранное)")
    # «Избранное»: источник — сам аккаунт. Удобно, когда не хочется заводить
    # канал: пост сохраняется в Saved Messages этого же аккаунта и оттуда
    # пересылается в группы.
    if raw.lower() in ("me", "self", "saved", "избранное", "избранное "):
        me = await client.get_me()
        return await client.get_input_entity(me)
    link = re.search(r"(?:https?://)?t\.me/(?:c/)?(-?\d+|[A-Za-z0-9_]{4,})", raw)
    if link:
        raw = link.group(1)
    if raw.lstrip("-").isdigit():
        needle = int(raw)
        dialogs = dialogs or await client.get_dialogs()
        for d in dialogs:
            if getattr(d.entity, "id", None) in (needle, abs(needle) % (10 ** 12)):
                return d.entity
        sys.exit(f"источник {source} не найден среди диалогов аккаунта — "
                 f"сначала подпишитесь на канал / добавьте его в избранное")
    try:
        return await client.get_entity(raw)
    except Exception as exc:
        sys.exit(f"источник {source} недоступен: {type(exc).__name__}: {str(exc)[:120]}")


def _campaign_ok_count(conn, camp):
    if camp is None:
        return 0
    row = conn.execute("SELECT COUNT(*) c FROM sent WHERE campaign=? AND status='ok'", (camp,)).fetchone()
    return int(row["c"]) if row else 0


async def cmd_broadcast(args):
    account = args.account
    conn = connect()
    client = None
    try:
        from_source = getattr(args, "from_source", None)
        media = None if from_source else resolve_media(conn, getattr(args, "media", None))
        text = "" if from_source else read_message(args)
        client = get_client(account)
        await require_auth(client)
        from telethon.tl.types import Chat, Channel
        dialogs = await client.get_dialogs()
        emap = {d.entity.id: d.entity for d in dialogs if isinstance(d.entity, (Chat, Channel))}

        def resolve(r):
            return (f"группа {r['id']} «{r['title']}»", pick_target(emap, r),
                    str(group_key(r["id"])), chat_context(r))

        q = "SELECT * FROM groups WHERE active=1 AND in_broadcast=1"
        p = []
        if args.ids:
            q += " AND id IN (%s)" % ",".join("?" * len(args.ids))
            p = args.ids
        rows = conn.execute(q, p).fetchall()
        rows = await _filter_broadcast_targets(conn, account, rows, emap, args)
        if not rows:
            sys.exit("Нечего отправлять: список пуст или всё уже покрыто")
        if from_source:
            # Режим пересылки: сообщения уже лежат в канале-источнике, текст не
            # рендерим — оригинал не пересобирается и не отличается у соседей.
            args.drop_author = bool(getattr(args, "drop_author", True)) and (
                getattr(args, "forward_from", "raw") == "raw"
            )
            source = from_source if args.dry_run else await resolve_source(client, from_source, dialogs=dialogs)
            ids = forward_ids(conn, args)
            if not ids:
                sys.exit("нет сообщений для пересылки: укажите --msg-id или "
                         "запомните их через media add --forward-ids")
            print(f"Пересылка {len(ids)} сообщ. из «{from_source}» в {len(rows)} групп(ы) "
                  f"аккаунтом «{account}» (drop_author={bool(args.drop_author)}):")
            camp = await forward_run(client, conn, "broadcast", rows, source, ids, args,
                                     resolve=resolve, account=account,
                                     no_preview=getattr(args, "no_preview", False))
            return _campaign_ok_count(conn, camp)
        print(f"Рассылка {len(rows)} групп(ы) аккаунтом «{account}»"
              + (f" + медиа #{args.media}" if getattr(args, "media", None) else "") + ":")
        camp = await send_run(client, conn, "broadcast", rows, text, args, resolve=resolve,
                              account=account, media=media)
        return _campaign_ok_count(conn, camp)
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        conn.close()


async def _attach_sources(players, source):
    """Положить в каждого игрока его собственный peer-объект источника."""
    for name, player in list(players.items()):
        try:
            player["source"] = await resolve_source(player["client"], source)
        except SystemExit as exc:
            print(f"  пропуск «{name}»: {exc}")
            removed = players.pop(name, None)
            if removed and removed.get("client") is not None:
                try:
                    await removed["client"].disconnect()
                except Exception:
                    pass


def message_id(sent):
    """id отправленного сообщения (Telethon Message/Updates/list) или None.

    Нужен, чтобы потом сопоставить ответ человека с конкретной отправкой:
    без этого «доставка» остаётся только «API принял»."""
    if isinstance(sent, (list, tuple)):
        for item in sent:
            found = message_id(item)
            if found is not None:
                return found
        return None
    for attr in ("id", "msg_id"):
        value = getattr(sent, attr, None)
        if isinstance(value, int):
            return value
    msg = getattr(sent, "message", None)
    if msg is not None:
        value = getattr(msg, "id", None)
        if isinstance(value, int):
            return value
    upd = getattr(sent, "updates", None)
    if upd:
        for item in upd:
            for attr in ("id", "msg_id"):
                value = getattr(item, attr, None)
                if isinstance(value, int):
                    return value
            nested = getattr(item, "message", None)
            if nested is not None:
                value = getattr(nested, "id", None)
                if isinstance(value, int):
                    return value
    return None


def apply_forward_mode(args, conn):
    """Подготовить режим пересылки для conduct/conduct both.

    Возвращает ids сообщений источника или None, если кампания текстовая.
    --forward-from raw скрывает заголовок источника в чате; self оставляет его.
    """
    source = getattr(args, "from_source", None)
    if not source:
        return None
    if not getattr(args, "msg_id", None):
        saved = forward_ids(conn, args)
        if not saved:
            sys.exit("нет сообщений для пересылки: укажите --msg-id")
        args.msg_id = saved
    args.drop_author = bool(getattr(args, "drop_author", True)) and \
        getattr(args, "forward_from", "raw") == "raw"
    return list(args.msg_id)


async def forward_batch(client, kind, peer, source_peer, source_ids, args):
    """Переслать пачку сообщений источника одной цели."""
    from telethon.tl.functions.messages import ForwardMessagesRequest
    return await client(ForwardMessagesRequest(
        from_peer=source_peer, id=list(source_ids), to_peer=peer,
        random_id=[random.getrandbits(63) for _ in source_ids],
        drop_author=bool(getattr(args, "drop_author", True))))


async def forward_run(client, conn, kind, targets, source_peer, source_ids, args,
                      resolve, account="main", no_preview=False):
    """Пересылка заранее заготовленных сообщений из закрытого канала-источника.

    Кто «автор» — решает флаг --forward-from (см. build_parser):
      * raw (по умолчанию) — drop_author=True: «Forwarded from» в чате не
        подсвечивается, аккаунт выглядит отправителем;
      * именованный режим — аккаунт пересылает со своим заголовком, чтобы
        видимость совпадала с тем, кто фактически пишет.

    Оба режима лишь снижают заметность для людей в чате.

    Модератор всё равно может проследить исходное сообщение по контексту жалобы
    (messageFwdHeader.from_id + жалоба на чат), поэтому drop_author — снижение
    заметности, а не иммунитет. Ошибки пересылки считаются точно так же, как
    у отправки: FloodWait/PeerFlood обрабатывает target_disposition.
    """
    if args.dry_run:
        camp = None
    else:
        cur = conn.execute(
            "INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
            (kind, f"forward:{source_peer}:{source_ids[:3]}", now_iso(), account))
        camp = cur.lastrowid
        conn.commit()
    done = ok = previews = 0
    halt, halt_reason = False, ""
    pause_lo, pause_hi = ordered_pause(
        getattr(args, "delay_min", 0), getattr(args, "delay_max", 0))
    started_at = now_iso()
    source = source_peer  # один источник на весь прогон
    for target in targets:
        if stop_requested(account, started_at):
            print(f"  остановка запрошена для «{account}»")
            break
        resolved = resolve(target)
        label, peer, key = resolved[0], resolved[1], resolved[2]
        if args.dry_run:
            if previews < DRY_PREVIEWS:
                print(f"  [dry] -> {label}: forward {len(source_ids)} msg "
                      f"из «{source_peer}» (drop_author={bool(getattr(args, 'drop_author', True))})")
                previews += 1
            else:
                print(f"  [dry] -> {label}")
            continue
        if args.max_runs and done >= args.max_runs:
            print("  достигнут лимит --max-runs, стоп.")
            break
        if halt:
            break
        try:
            sent_msg = await forward_batch(client, kind, peer, source, source_ids, args)
            conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account,msg_id) "
                         "VALUES(?,?,?,?,?,?)",
                         (camp, key, "ok", now_iso(), account, message_id(sent_msg)))
            ok += 1
            print(f"  ok  {label}")
        except Exception as e:
            disp = target_disposition(e, kind)
            if disp == "defer_same_account":
                fu = flood_pause_until(e)
                conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, account))
                conn.commit()
                print(f"  {type(e).__name__} {int(getattr(e, 'seconds', 0) or 0)} c "
                      f"на аккаунте «{account}» (cooldown до {fu}). Цель не помечена мёртвой.")
                halt, halt_reason = True, type(e).__name__
            elif disp in ("halt_dm_queue", "halt_account"):
                strikes = register_peerflood(conn, account)
                print(f"  {type(e).__name__}: аккаунт «{account}» на паузе "
                      f"(страйк {strikes}). Цель не помечена мёртвой и другому "
                      f"аккаунту не отдаётся.")
                halt, halt_reason = True, type(e).__name__
            else:
                status = "skip:slowmode" if disp == "skip" else f"fail:{type(e).__name__}"
                if disp == "remember":
                    remember_permanent(conn, kind, key, permanent_reason(e, kind), account)
                conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                             "VALUES(?,?,?,?,?)", (camp, key, status, now_iso(), account))
                print(f"  fail {label}: {type(e).__name__}: {str(e)[:120]}")
        conn.commit()
        done += 1
        if pause_hi > 0 and not halt and done < len(targets):
            await asyncio.sleep(random.uniform(pause_lo, pause_hi))
    suffix = " [dry-run, ничего не отправлено]" if args.dry_run else ""
    if halt:
        suffix += f" [остановлено: {halt_reason} — аккаунт в cooldown]"
    if args.dry_run:
        print(f"Сухой прогон (forward {kind}, «{account}»): целей {len(targets)}, "
              f"превью {min(previews, DRY_PREVIEWS)}, отправлено 0{suffix}")
    else:
        print(f"Кампания #{camp} (forward {kind}, «{account}»): "
              f"переслано {ok}, попыток {done}{suffix}")
    return camp


async def cmd_dmsend(args):
    account = args.account
    conn = connect()
    client = None
    try:
        from_source = getattr(args, "from_source", None)
        media = None if from_source else resolve_media(conn, getattr(args, "media", None))
        text = "" if from_source else read_message(args)
        if args.delay_max < 10 and not args.dry_run:
            print("! Пауза <10 c между DM — высокий риск бана аккаунта.")
        if args.dry_run:
            # Сухой прогон не трогает Telegram: ни сессии, ни сети — только база.
            # Так превью можно смотреть до логина и без расхода сессии.
            client = None
            acct = conn.execute("SELECT user_id FROM accounts WHERE name=?", (account,)).fetchone()
            me_id = acct["user_id"] if acct else None
        else:
            client = get_client(account)
            await require_auth(client)
            me_id = (await client.get_me()).id
        # Запас шире лимита: случайная выборка + круг по группам-источникам, чтобы
        # пачка не выгребала подряд людей одной группы (это видно в чате и вызывает жалобы).
        pool = max(args.limit * 4, 40) if args.limit else None
        rows = dm_targets(conn, group_id=args.group, repeat_ok=args.repeat_ok,
                          limit=pool, shuffle=True)
        rows = [r for r in rows if r["user_id"] != me_id]  # себе не пишем
        want = args.limit or None
        if not args.dry_run:
            bud = conn.execute("SELECT daily_budget FROM accounts WHERE name=?", (account,)).fetchone()
            raw = bud["daily_budget"] if bud else None
            # 0 = без лимита (не рекомендую); NULL = потолок по контингенту аккаунта.
            cap = account_dm_cap(conn, account, raw)
            if cap:
                used = today_dm_sent(conn, account)
                left = max(0, cap - used)
                if left == 0:
                    sys.exit(f"Дневной DM-бюджет «{account}» исчерпан ({used}/{cap}). "
                             f"Завтра продолжится сам; объём = accounts budget {account} N.")
                if len(rows) > left:
                    print(f"бюджет «{account}»: беру не больше {left} из {len(rows)} подходящих "
                          f"(сегодня ok {used}/{cap})")
                want = left if not args.limit else min(args.limit, left)
        rows = interleave_targets(rows, want)
        if not rows:
            sys.exit("Нет доступных получателей (отправлены, в blacklist, боты, "
                     "удалённые или без адреса).")
        if from_source:
            # DM-пересылка: тот же журнал sent, тот же кросс-дедуп и бюджеты —
            # формат доставки меняется, политика анти-спама нет.
            args.drop_author = bool(getattr(args, "drop_author", True)) and (
                getattr(args, "forward_from", "raw") == "raw"
            )
            source = from_source if args.dry_run else await resolve_source(client, from_source)
            ids = forward_ids(conn, args)
            if not ids:
                sys.exit("нет сообщений для пересылки: укажите --msg-id")
            print(f"DM-пересылка {len(ids)} сообщ. из «{from_source}» "
                  f"{len(rows)} получателям аккаунтом «{account}»:", flush=True)
            camp = await forward_run(client, conn, "dm", rows, source, ids, args,
                                     resolve=lambda r: (f"@{r['username'] or '-'} id={r['user_id']}",
                                                        dm_peer(r), str(r["user_id"]), dm_context(r)),
                                     account=account, no_preview=getattr(args, "no_preview", False))
            return _campaign_ok_count(conn, camp)
        print(f"DM {len(rows)} получателям аккаунтом «{account}» (лимит за-run: {args.limit}):")
        camp = await send_run(client, conn, "dm", rows, text, args,
                              resolve=lambda r: (f"@{r['username'] or '-'} id={r['user_id']}",
                                                 dm_peer(r), str(r["user_id"]), dm_context(r)),
                              account=account, media=media)
        return _campaign_ok_count(conn, camp)
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        conn.close()


def cmd_accounts(args):
    conn = connect()
    try:
        _accounts_apply(conn, args)
    finally:
        conn.close()


def _accounts_apply(conn, args):
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
            dcap = account_dm_cap(conn, a["name"], a["daily_budget"]) or "∞"
            print(f"  {a['name']:<14} id={a['user_id'] or '-':<12} "
                  f"DM сегодня {used:>3}/{dcap}  "
                  f"группы {bused:>3}/{bcap if bcap else '∞'}  "
                  f"session={a['session']}  {' '.join(flags)}")
            print(f"    {'':<14} риск: {account_health(conn, a['name'])}")
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
        persist_account_secret(name, api_id, api_hash)
        conn.execute("UPDATE accounts SET api_id=NULL, api_hash=NULL WHERE name=?", (name,))
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
            bad_cnt += 1  # сюда входят и группы, где аккаунта нет (not_member)
        if ent is None:
            conn.execute("DELETE FROM account_groups WHERE account=? AND group_id=?",
                         (account, r["id"]))
        else:
            record_edge(conn, account, r["id"], can_write=1 if can else 0)
        if not keep:
            if can:
                conn.execute("UPDATE groups SET active=1 WHERE id=?", (r["id"],))
            refresh_group_flags(conn, r["id"], why)
        if ent is None and not verbose:
            continue  # молча: оркестр проверяет покрытие отдельно
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


def cmd_stop(args):
    account = args.account or "all"
    request_stop(account)
    print(f"остановка запрошена: {account}")


# ---------------------------------------------------------------- оркестр ---
# Всё, что нужно для ансамбля из 11 аккаунтов: оформление профилей, единый
# набор групп, проверки на бота и раскладка «никому дважды».

IDENTITY_DIR = Path(os.environ.get("USERBOT_IDENTITY", ROOT / "assets" / "identity"))
IDENTITY_STORE = Path(os.environ.get("USERBOT_IDENTITY_STATE", ROOT / "profiles.json"))


def identity_plan(accounts):
    """Сопоставить аккаунты с profiles.csv и avatars/ из IDENTITY_DIR."""
    profiles_path = IDENTITY_DIR / "profiles.csv"
    problems = []
    profiles = []
    if profiles_path.exists():
        profiles = orchestra.parse_profile_csv(profiles_path.read_text(encoding="utf-8"))
    elif accounts:
        problems.append(f"нет {profiles_path} — профили не изменятся")
    avatar_dir = IDENTITY_DIR / "avatars"
    avatars = []
    if avatar_dir.is_dir():
        for path in sorted(avatar_dir.iterdir()):
            if path.suffix.lower() in orchestra.AVATAR_SUFFIXES:
                issues = orchestra.validate_avatar(str(path))
                if issues:
                    problems.extend(issues)
                else:
                    avatars.append(str(path))
    if len(profiles) > len(accounts):
        for extra in orchestra.leftover_profiles(accounts, profiles):
            problems.append(f"лишняя строка profiles.csv: «{extra.name} {extra.last_name}» — "
                            f"аккаунтов {len(accounts)}")
    plan = orchestra.assign_identities(accounts, profiles, avatars)
    return plan, problems, avatars


def _identity_state():
    if not IDENTITY_STORE.exists():
        return {}
    try:
        data = json.loads(IDENTITY_STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _identity_save(state):
    IDENTITY_STORE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def apply_identity(client, identity):
    """Записать имя/фамилию/о себе и фото. Возвращает (ok, [строки отчёта])."""
    from telethon.tl.functions.account import UpdateProfileRequest
    from telethon.tl.functions.photos import UploadProfilePhotoRequest

    notes, ok = [], True
    profile, warnings = orchestra.validate_profile(identity.profile)
    for warning in warnings:
        notes.append(f"  ! {warning}")
    params = {}
    if profile.name:
        params["first_name"] = profile.name
    if profile.last_name:
        params["last_name"] = profile.last_name
    if profile.about:
        params["about"] = profile.about
    if params:
        try:
            await client(UpdateProfileRequest(**params))
            notes.append(f"  ok имя/о себе: {profile.name or '—'} {profile.last_name or ''}"
                         f" ({(profile.about or '—')[:40]})")
        except Exception as exc:
            ok = False
            notes.append(f"  fail профиль: {type(exc).__name__}: {str(exc)[:100]}")
    if identity.avatar:
        try:
            uploaded = await client.upload_file(identity.avatar)
            await client(UploadProfilePhotoRequest(file=uploaded))
            notes.append(f"  ok аватарка: {Path(identity.avatar).name}")
        except Exception as exc:
            ok = False
            notes.append(f"  fail аватарка: {type(exc).__name__}: {str(exc)[:100]}")
    return ok, notes


async def cmd_profile(args):
    """profile apply / status — единое оформление всех аккаунтов."""
    conn = connect()
    if args.op == "status":
        state = _identity_state()
        plan, problems, avatars = identity_plan(
            [r["name"] for r in conn.execute("SELECT name FROM accounts ORDER BY name")])
        print(f"материалы: {IDENTITY_DIR}")
        print(f"  profiles.csv: {'есть' if (IDENTITY_DIR / 'profiles.csv').exists() else 'НЕТ'}")
        print(f"  аватарок: {len(avatars)}")
        for problem in problems:
            print(f"  ! {problem}")
        for identity in plan:
            mark = "применено" if state.get(identity.account, {}).get("ok") else "не применено"
            print(f"  {identity.account:<8} {identity.profile.name or '—':<16} "
                  f"{(identity.profile.about or '—')[:32]:<34} "
                  f"{Path(identity.avatar).name if identity.avatar else 'нет фото':<18} {mark}")
        conn.close()
        return
    names = args.accounts or [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    plan, problems, _avatars = identity_plan(names)
    if problems:
        for problem in problems:
            print(f"  ! {problem}")
        if any("профили не изменятся" in p for p in problems):
            sys.exit("нет profiles.csv — положи материалы в assets/identity/")
    if args.dry_run:
        for identity in plan:
            print(f"  [dry] {identity.account}: {identity.profile.name} "
                  f"{identity.profile.last_name} · «{identity.profile.about}» · "
                  f"{Path(identity.avatar).name if identity.avatar else 'без фото'}")
        conn.close()
        return
    state = _identity_state()
    for identity in plan:
        client = get_client(identity.account)
        await require_auth(client)
        ok, notes = await apply_identity(client, identity)
        for note in notes:
            print(f"  [{identity.account}] {note}")
        state[identity.account] = {"ok": ok, "at": now_iso(),
                                   "profile": identity.profile.__dict__,
                                   "avatar": identity.avatar}
        _identity_save(state)
        await client.disconnect()
    done = sum(1 for v in state.values() if v.get("ok"))
    print(f"Профили: применено {done} из {len(plan)} (состояние в {IDENTITY_STORE.name})")
    conn.close()


def _accounts_edges(conn, accounts):
    """account -> {group_id}, где аккаунт состоит и может писать (ключ group_key)."""
    edges = {name: set() for name in accounts}
    for row in conn.execute("SELECT account, group_id, can_write FROM account_groups"):
        if row["account"] in edges and row["can_write"]:
            edges[row["account"]].add(group_key(row["group_id"]))
    return edges


async def cmd_join(args):
    """join: все включённые аккаунты в один и тот же набор групп."""
    conn = connect()
    accounts = args.accounts or [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    groups = [dict(r) for r in conn.execute("SELECT * FROM groups ORDER BY title")]
    if args.groups:
        wanted = set(args.groups)
        groups = [g for g in groups if g["id"] in wanted or group_key(g["id"]) in wanted]
    edges = _accounts_edges(conn, accounts)
    plans = orchestra.plan_joins(groups, edges, accounts)
    linked = []
    for plan in plans:
        if plan.link_needed:
            linked.append(plan)
    todo = orchestra.stagger_joins(plans, args.limit)
    missing_total = sum(1 for plan in plans for _acc in plan.missing)
    print(f"Набор групп: {len(plans)}; не хватает вступлений: {missing_total} "
          f"(аккаунтов {len(accounts)}, лимит {args.limit}/аккаунт за запуск)")
    for plan in linked:
        print(f"  ! приватная группа {plan.group_id} «{plan.title}»: нужна инвайт-ссылка")
    if args.dry_run:
        for account, plan in todo:
            print(f"  [dry] {account}: вступить в {plan.group_id} «{plan.title}» "
                  f"(@{plan.username or '—'})")
        print(f"[dry] вступлений в этом запуске: {len(todo)} из {missing_total}")
        conn.close()
        return
    if not todo:
        print("Все аккаунты уже во всех группах, где это возможно.")
        conn.close()
        return
    clients = {}
    joined = failed = 0
    from telethon.errors import FloodWaitError, UserAlreadyParticipantError
    from telethon.tl.functions.channels import JoinChannelRequest

    try:
        for account, plan in todo:
            if account not in clients:
                client = get_client(account)
                await require_auth(client)
                clients[account] = client
            client = clients[account]
            try:
                peer = plan.username or plan.group_id
                ent = await client(JoinChannelRequest(peer))
                gid = getattr(ent, "id", None) or plan.group_id
                record_edge(conn, account, gid, can_write=1)
                conn.commit()
                joined += 1
                print(f"  + {account}: {plan.group_id} «{plan.title[:36]}»")
            except UserAlreadyParticipantError:
                record_edge(conn, account, plan.group_id, can_write=1)
                conn.commit()
            except FloodWaitError as exc:
                fu = (datetime.now(timezone.utc) + timedelta(seconds=exc.seconds)).isoformat(
                    timespec="seconds")
                conn.execute("UPDATE accounts SET flood_until=? WHERE name=?", (fu, account))
                conn.commit()
                failed += 1
                print(f"  ⏸ {account}: FloodWait {exc.seconds} c — пауза до {fu[11:16]} UTC")
            except Exception as exc:
                failed += 1
                print(f"  ✗ {account}: {plan.group_id}: {type(exc).__name__}: {str(exc)[:80]}")
            await asyncio.sleep(random.uniform(args.delay_min, args.delay_max))
    finally:
        for client in clients.values():
            await client.disconnect()
    print(f"Вступления за запуск: +{joined}, проблем {failed}; всего не хватало {missing_total}")
    conn.close()


# ---------------------------------------------------------------- discover

# Города/гео, по которым ищем аудиторию. Ключ — то, что попадает в название
# группы (там лежит гео-контекст), значения — поисковые фразы.
GEO_QUERIES = {
    "praga": ["Прага", "Praha", "Чехия", "Чехия Прага", "Czech Republic Prague",
              "Praha Česko", "Прага Чехия чат", "жизнь в Праге", "аренда Прага",
              "работа Прага", "барахолка Прага", "Prague expats", "Prague life",
              "Praha bazar", "Praha bydlení", "Praha práce"],
}

# Тематические фразы — ищутся уже ВНУТРИ найденной группы, чтобы прочитать
# её название/описание и понять характер аудитории.
GEO_THEMES = [
    ("rent", r"аренд|жиль|квартир|bydlen|pronájem|nájem|соседн|комнат|жить"),
    ("jobs", r"работ|ваканс|фушк|подработ|práce|brigád|praca|зарплат"),
    ("market", r"барахол|базар|bazar|объявлен|продам|daruj|даром|отдам"),
    ("news", r"новост|zpráv|news|инфо"),
    ("expat", r"эмигрант|релок|переезд|expats|ukrajin|украин|студент|adapt"),
]


def _geo_themes(text):
    low = (text or "").lower()
    return [name for name, rx in GEO_THEMES if re.search(rx, low)]


def _theme_matches(text, theme):
    """Основной фильтр: название или описание должны попадать в тему.

    Пустая тема ("всё подряд") пропускает всё; для своей темы слово должно
    встретиться в тексте, иначе группа не то, что нужно.
    """
    if not theme:
        return True
    low = (text or "").lower()
    patterns = {
        "rent": r"аренд|жиль|квартир|bydlen|pronájem|nájem|сосед|жилплощ|комнат",
        "jobs": r"работ|ваканс|фушк|подработ|prác|brigád|praca|зарплат|вакан",
        "market": r"барахол|базар|bazar|объявлен|продам|daruj|отдам|маркет",
        "news": r"новост|zpráv|news|инфо|сми",
    }
    if theme == "jobs":
        # "работа" в английском "провайдер"/"Праце" ловим отдельно, чтобы
        # не тащить в рабочие чаты всё где есть буквы "работ" внутри слова
        return bool(re.search(r"\bработ|ваканс|фушк|prác|brigád|praca|vacanc|job", low))
    return bool(re.search(patterns.get(theme, theme), low))


def _entity_row(ent, geo, queries):
    """Общие поля группы из Channel/Chat от contacts.search."""
    title = getattr(ent, "title", "") or ""
    username = getattr(ent, "username", None)
    megagroup = bool(getattr(ent, "megagroup", False))
    broadcast = bool(getattr(ent, "broadcast", False))
    if broadcast:
        gtype = "broadcast"
    elif megagroup:
        gtype = "megagroup"
    else:
        gtype = "chat"
    return {
        "id": int(ent.id),
        "title": title,
        "username": username,
        "gtype": gtype,
        "members": getattr(ent, "participants_count", 0) or 0,
        "description": "",
        "geo": geo,
        "themes": _geo_themes(f"{title} {username or ''}"),
        "scam": bool(getattr(ent, "scam", False) or getattr(ent, "fake", False)),
        "restricted": bool(getattr(ent, "restricted", False)),
        "join_request": bool(getattr(ent, "join_request", False)),
        "slowmode": 0,
        "writable": None,   # None = не проверяли; True/False = проверено
        "reason": "",
        "hits": [],
    }


async def _enrich(client, row):
    """Дотянуть описание и режим слоумода из GetFullChannelRequest."""
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.functions.messages import GetFullChatRequest
    try:
        if row["gtype"] == "chat":
            full = await client(GetFullChatRequest(row["id"]))
            chat = full.full_chat
            row["description"] = getattr(chat, "about", "") or ""
            row["members"] = getattr(chat, "participants_count", 0) or row["members"]
        else:
            full = await client(GetFullChannelRequest(row["username"] or row["id"]))
            chat = full.full_chat
            row["description"] = getattr(chat, "about", "") or ""
            row["members"] = getattr(chat, "participants_count", 0) or row["members"]
            row["slowmode"] = getattr(chat, "slowmode_seconds", 0) or 0
            banned = getattr(chat, "default_banned_rights", None)
            # send_messages запрещён "по умолчанию" у всех участников =>
            # пишут только админы: постинг закрыт.
            if banned is not None and getattr(banned, "send_messages", False):
                row["writable"] = False
                row["reason"] = "write_closed"
    except Exception:
        pass
    return row


def _score(row, theme):
    """Рейтинг группы: большая живая аудитория, совпавшая тема, пишем свободно."""
    score = 0.0
    members = row["members"] or 0
    if members:
        score += min(45.0, math.log10(members + 1) * 14)
    if theme:
        score += 20
    if row["writable"]:
        score += 15
    elif row["writable"] is None:
        score += 5
    if row["gtype"] == "megagroup":
        score += 10          # чат: есть где общаться и где видна история
    if row["scam"]:
        score -= 60
    if row["username"]:
        score += 3
    return round(score, 1)


async def cmd_discover(args):
    """discover: искать новые каналы/группы по гео+темам и складывать в базу."""
    from telethon.errors import FloodWaitError
    from telethon.tl.functions.contacts import SearchRequest

    theme = args.theme or ""
    if theme and theme not in ("rent", "jobs", "market", "news"):
        sys.exit(f"Неизвестная тема «{theme}» — rent | jobs | market | news")
    geo = "praga"
    queries = args.queries or GEO_QUERIES.get(geo, [])

    conn = connect()
    known = {row["id"] for row in conn.execute("SELECT id FROM groups")}
    found = {}
    per_query = []
    client = None

    async def search(client, query, retries=2):
        out = []
        try:
            res = await client(SearchRequest(q=query, limit=100))
        except FloodWaitError as exc:
            if exc.seconds > 600 or retries <= 0:
                print(f"  ⏸ FloodWait {exc.seconds} c на «{query}» — пропускаю")
                return out
            print(f"  ⏸ FloodWait {exc.seconds} c — жду")
            await asyncio.sleep(exc.seconds + 2)
            return await search(client, query, retries=retries - 1)
        except Exception as exc:
            print(f"  ✗ «{query}»: {type(exc).__name__}: {str(exc)[:80]}")
            return out
        for ent in res.chats:
            if type(ent).__name__ not in ("Channel", "Chat"):
                continue        # ChatForbidden/User — не группа
            if not getattr(ent, "username", None):
                continue        # без @username не можем ни проверить, ни вступить
            row = _entity_row(ent, geo, [])
            row["hits"] = [query]
            out.append(row)
        return out

    try:
        client = get_client(args.account)
        await require_auth(client)
        print(f"Поиск: «{geo}», тем {len(queries)} запросов, тема={theme or 'все'}")
        n = 0
        for query in queries:
            got = await search(client, query)
            per_query.append((query, len(got)))
            for row in got:
                prev = found.get(row["id"])
                if prev is None:
                    found[row["id"]] = row
                else:
                    prev["hits"].append(query)
                    prev["members"] = max(prev["members"], row["members"])
            n += 1
            await asyncio.sleep(args.delay_min + random.random() * max(0.0, args.delay_max - args.delay_min))
            print(f"  [{n}/{len(queries)}] «{query}» → {len(got)}")

        rows = [r for r in found.values() if not r["scam"]]
        print(f"Уникальных групп до обогащения: {len(rows)} (из {len(found)})")

        # читаем полные описания — там и характеристика, и слова о рекламе
        for row in rows:
            await _enrich(client, row)
            row["themes"] = sorted(set(row["themes"]) | set(_geo_themes(row["description"])))
            await asyncio.sleep(0.6 + random.random() * 1.2)
        if theme:
            rows = [r for r in rows if _theme_matches(r["title"], theme)
                    or _theme_matches(r["description"], theme)]
            print(f"Уникальных групп после фильтра по теме «{theme}»: {len(rows)}")

        if args.verify:
            from telethon.tl.functions.channels import GetParticipantRequest
            me = await client.get_input_entity("me")
            for row in rows:
                if row["gtype"] == "broadcast":
                    row["writable"] = False
                    row["reason"] = row["reason"] or "channel_read_only"
                    continue
                try:
                    part = await client(GetParticipantRequest(row["username"] or row["id"], me))
                    rights = getattr(part, "participant", None)
                    banned = getattr(rights, "banned_rights", None)
                    if banned is not None and getattr(banned, "send_messages", False):
                        row["writable"] = False
                        row["reason"] = "write_closed"
                    else:
                        row["writable"] = True
                        row["reason"] = ""
                except Exception:
                    # не участник -> определяет default_banned_rights (уже в _enrich);
                    # если и его нет — считаем, что писать можно.
                    pass
                await asyncio.sleep(0.5)

        new_rows = [r for r in rows if r["id"] not in known]
        for row in rows:
            row["score"] = _score(row, theme)
        rows.sort(key=lambda r: r["score"], reverse=True)
        print(f"Новых (нет в базе): {len(new_rows)}")

        if args.dry_run:
            for row in rows[:60]:
                mark = "нов" if row["id"] not in known else "—"
                wr = {True: "можно писать", False: "только админы", None: "?"}[row["writable"]]
                print(f"  [{mark}] {row['score']:5.1f} {row['gtype'][:9]:9} "
                      f"{('@' + row['username']) if row['username'] else row['id']:24} "
                      f"{row['members'] or 0:>7}  {wr:14} {row['title'][:44]}")
            return

        imported = 0
        for row in rows:
            if row["id"] in known:
                # уже в базе: обновляем свежими фактами, но не трогаем in_broadcast
                conn.execute("UPDATE groups SET title=COALESCE(NULLIF(title,''), ?), "
                             "username=COALESCE(username, ?), gtype=COALESCE(gtype, ?) "
                             "WHERE id=?", (row["title"], row["username"], row["gtype"], row["id"]))
                continue
            conn.execute(
                "INSERT INTO groups(id,title,username,gtype,added_at,active,in_broadcast,"
                "skip_reason,discovered_at) VALUES(?,?,?,?,?,1,?,?,?)",
                (row["id"], row["title"], row["username"], row["gtype"], now_iso(),
                 int(bool(row["writable"])), row["reason"] or "", now_iso()))
            imported += 1
        conn.commit()
        print(f"Добавлено в базу: {imported}")

        if args.export:
            out = Path(args.export)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh, delimiter=";")
                writer.writerow(["score", "id", "username", "title", "type", "members",
                                 "writable", "created", "themes", "queries", "in_db", "description"])
                for row in rows:
                    writer.writerow([row["score"], row["id"], row["username"] or "",
                                     row["title"], row["gtype"], row["members"] or 0,
                                     {True: "yes", False: "no", None: "?"}[row["writable"]],
                                     "", ",".join(row["themes"]), ",".join(sorted(set(row["hits"]))),
                                     "old" if row["id"] in known else "new",
                                     (row["description"] or "").replace("\n", " ")[:400]])
            print(f"Выгрузка: {out}")
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        conn.close()


async def cmd_seed(args):
    """seed: влить вручную проверенный список каналов (файл username;title;members;tag).

    Существующие группы не перезаписываются, новые добавляются как найденные.
    """
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"Нет файла {path}")
    rows = []
    with path.open(encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(";")]
            if len(parts) < 2 or not parts[0]:
                continue
            rows.append({"username": parts[0].lstrip("@"),
                         "title": parts[1],
                         "tag": parts[3] if len(parts) > 3 else ""})
    conn = connect()
    known = {row["username"].lower() for row in conn.execute(
        "SELECT username FROM groups WHERE username IS NOT NULL")}
    todo = [r for r in rows if r["username"].lower() not in known]
    print(f"В файле: {len(rows)}, новых: {len(todo)}")
    if args.dry_run:
        for r in todo:
            print(f"  [dry] @{r['username']:26} {r['title'][:50]}")
        conn.close()
        return

    client = get_client(args.account)
    await require_auth(client)
    added = failed = 0
    try:
        for r in todo:
            try:
                ent = await client.get_entity(r["username"])
                title = getattr(ent, "title", None) or r["title"]
                gtype = ("megagroup" if getattr(ent, "megagroup", False)
                         else "broadcast" if getattr(ent, "broadcast", False) else "chat")
                conn.execute(
                    "INSERT INTO groups(id,title,username,gtype,added_at,active,in_broadcast,"
                    "skip_reason,discovered_at) VALUES(?,?,?,?,?,1,0,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title, username=excluded.username",
                    (int(ent.id), title, r["username"], gtype, now_iso(),
                     f"seed:{r['tag']}", now_iso()))
                added += 1
                print(f"  + @{r['username']:26} {title[:46]}")
            except Exception as exc:
                failed += 1
                print(f"  ✗ @{r['username']:26} {type(exc).__name__}: {str(exc)[:60]}")
            await asyncio.sleep(args.delay_min + random.random() * max(0.0, args.delay_max - args.delay_min))
        conn.commit()
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        conn.close()
    print(f"Добавлено: {added}, не удалось: {failed}")


async def _group_gate_message(client, entity, rows_before=20):
    """Последнее сообщение бота с безопасной кнопкой проверки в чате.

    Возвращает (msg, candidates, manual): manual=True — проверка видна, но
    безопасной кнопки нет (капча по ссылке, фото и т.п.) — решает человек.
    """
    manual = False
    async for msg in client.iter_messages(entity, limit=rows_before):
        markup = getattr(msg, "reply_markup", None)
        buttons = []
        if markup is not None and getattr(markup, "rows", None):
            for r_index, row in enumerate(markup.rows):
                for c_index, button in enumerate(getattr(row, "buttons", []) or []):
                    text = getattr(button, "text", "") or ""
                    if getattr(button, "url", None):
                        # кнопка-ссылка: кликать нечем, капча уводит в браузер
                        if orchestra.GATE_URL_HINTS.search(str(button.url)):
                            manual = True
                        continue
                    buttons.append((r_index, c_index, text))
            candidates = orchestra.botgate_candidates(buttons)
            if candidates:
                return msg, candidates, False
        if orchestra.is_gate_message(getattr(msg, "message", "") or ""):
            manual = True
    return None, [], manual


async def run_gates(client, conn, account, groups, dry_run=False, delay=(3.0, 8.0)):
    """Пройти проверки на бота во всех группах аккаунта. Возвращает счётчики."""
    from telethon.tl.types import Chat, Channel
    from telethon.tl.functions.messages import GetBotCallbackAnswerRequest

    stats = {"clicked": 0, "manual": 0, "none": 0, "fail": 0}
    dialogs = await client.get_dialogs()
    emap = {d.entity.id: d.entity for d in dialogs if isinstance(d.entity, (Chat, Channel))}
    for row in groups:
        gid = group_key(row["id"])
        ent = emap.get(row["id"]) or emap.get(gid)
        if ent is None:
            continue
        msg, candidates, manual = await _group_gate_message(client, ent)
        if not candidates:
            if manual:
                stats["manual"] += 1
                conn.execute("UPDATE groups SET join_status=?, gate_at=? WHERE id=?",
                             ("gate_manual", now_iso(), row["id"]))
                print(f"  ? {account} «{row['title'][:40]}»: проверка без безопасной кнопки — вручную")
            else:
                stats["none"] += 1
            continue
        button = candidates[0]
        if dry_run:
            print(f"  [dry] {account} «{row['title'][:40]}»: нажал бы «{button.text}»")
            continue
        try:
            answer = await client(GetBotCallbackAnswerRequest(peer=ent, msg_id=msg.id,
                                                              data=b""))
            stats["clicked"] += 1
            conn.execute("UPDATE groups SET join_status=?, gate_at=? WHERE id=?",
                         ("gate_ok", now_iso(), row["id"]))
            tail = f" → {getattr(answer, 'message', '')[:60]}" if getattr(answer, "message", None) else ""
            print(f"  ✓ {account} «{row['title'][:40]}»: нажал «{button.text}»{tail}")
        except Exception as exc:
            stats["fail"] += 1
            conn.execute("UPDATE groups SET join_status=?, gate_at=? WHERE id=?",
                         ("gate_fail", now_iso(), row["id"]))
            print(f"  ✗ {account} «{row['title'][:40]}»: {type(exc).__name__}: {str(exc)[:80]}")
        await asyncio.sleep(random.uniform(*delay))
    conn.commit()
    return stats


async def cmd_gates(args):
    """gates: пройти проверки на бота по всем аккаунтам и группам."""
    conn = connect()
    accounts = args.accounts or [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    groups = [dict(r) for r in conn.execute("SELECT * FROM groups ORDER BY title")]
    if args.groups:
        wanted = set(args.groups)
        groups = [g for g in groups if g["id"] in wanted or group_key(g["id"]) in wanted]
    total = {"clicked": 0, "manual": 0, "none": 0, "fail": 0}
    for account in accounts:
        client = get_client(account, allow_flood=True)
        await require_auth(client)
        stats = await run_gates(client, conn, account, groups, dry_run=args.dry_run)
        for key in total:
            total[key] += stats[key]
        print(f"  {account}: нажато {stats['clicked']}, вручную {stats['manual']}, "
              f"чисто {stats['none']}, ошибок {stats['fail']}")
        await client.disconnect()
    print(f"Проверки: нажато {total['clicked']}, вручную {total['manual']}, "
          f"без проверки {total['none']}, ошибок {total['fail']}")
    conn.close()


def cmd_orchestra(args):
    """orchestra: матрица покрытия и список групп, где набор неполный."""
    conn = connect()
    accounts = [r["name"] for r in conn.execute(
        "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    groups = [dict(r) for r in conn.execute("SELECT * FROM groups ORDER BY title")]
    edges = _accounts_edges(conn, accounts)
    matrix = orchestra.coverage_matrix(groups, edges, accounts)
    print(f"Оркестр: аккаунтов {len(accounts)}, групп {len(groups)}")
    print("  " + " " * 48 + " ".join(name[:6].ljust(6) for name in accounts))
    for row in matrix["rows"]:
        marks = " ".join(("✓" if row["marks"][name] else "·").ljust(6) for name in accounts)
        print(f"  {row['id']:<14} {row['title'][:32].ljust(32)} {marks}")
    print("  по аккаунтам: " + ", ".join(f"{k}={v}" for k, v in matrix["per_account"].items()))
    print(f"  полный набор: {len(matrix['full_groups'])} из {matrix['groups']} групп")
    for row in groups:
        got = sum(1 for name in accounts if row["id"] in edges[name])
        if got < len(accounts):
            who = ", ".join(name for name in accounts if row["id"] not in edges[name])
            print(f"    нет {len(accounts) - got}: {row['title'][:40]} — {who[:70]}")
    conn.close()


def cmd_cloud(args):
    """Облачное зеркало Supabase (push|status|test) — делегирует cloud.py."""
    code = subprocess.call([sys.executable, str(ROOT / "cloud.py"), args.op])
    sys.exit(code)


# команды, после которых стоит обновить зеркало при AUTO_CLOUD=1
CLOUD_HOOK = {"cmd_broadcast", "cmd_dmsend", "cmd_conduct", "cmd_scrape",
              "cmd_groups", "cmd_privacy", "cmd_align", "cmd_accounts",
              "cmd_media", "cmd_blacklist", "cmd_export", "cmd_profile",
              "cmd_join", "cmd_gates"}


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
        t = conn.execute("SELECT title FROM groups WHERE id IN (%s)" %  # nosec B608
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
        sync_account_age(conn, n, getattr(me, "bot_registered", None))
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


def cmd_report(args):
    """report — что реально работает: кампании, источники, вердикт."""
    conn = connect()
    try:
        days = args.days
        min_sample = args.min_sample
        camps = campaign_report(conn, days=days, min_sample=min_sample)
        sources = source_report(conn, days=days, min_sample=min_sample)
        labels = {"dm": "личные", "broadcast": "группы"}
        print(f"Отчёт за {days} дн. (ответ = человек ответил, это доказанная доставка)")
        if not camps:
            print("  нет отправок за период — сначала нужна кампания, а уже потом масштаб")
            return
        print("\n  кампании:")
        for c in camps:
            mark = " [мало данных]" if c["low_confidence"] else ""
            print(f"    #{c['id']:<4} {labels.get(c['kind'], c['kind']):<8} "
                  f"{c['created_at'][:16]} «{c['preview']}»{mark}")
            print(f"           принято {c['accepted']}/{c['total']}, ответили {c['replied']} "
                  f"({c['reply_rate']:.1f}%), блок {c['blocked']} ({c['block_rate']:.1f}%), "
                  f"тишина {c['silent']}, отказ {c['failed']}")
        if sources:
            print("\n  источники (откуда пришли люди):")
            for s in sources[:15]:
                mark = " [мало данных]" if s["low_confidence"] else ""
                print(f"    {s['title'][:44]:<46} принято {s['accepted']:>4}, "
                      f"ответили {s['replied']:>3} ({s['reply_rate']:>5.1f}%), "
                      f"блок {s['blocked']:>3}{mark}")
        verdict = experiment_verdict(camps, min_sample=min_sample)
        print("\n  вывод:")
        advice = {
            "no_data": "данных мало — добирайте выборку до "
                      f"{min_sample} принятых на кампанию, но не теряйте качество",
            "stop_copy": "ответов нет ни на одной кампании: проблема в тексте или "
                         "аудитории. Увеличивать объём сейчас — только жечь аккаунты",
            "scale_winner": "есть кампания с ответами — масштабировать именно её, "
                             "а не всё подряд",
            "mixed": "данные есть, но разница в пределах шума: нужна ещё "
                     "пара кампаний с другим текстом",
        }
        print(f"    {advice.get(verdict, verdict)}")
    finally:
        conn.close()

def cmd_status(args):
    conn = connect()
    c = lambda t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]  # nosec B608
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
        dcap = account_dm_cap(conn, a["name"], a["daily_budget"]) or "∞"
        print(f"  аккаунт «{a['name']}»: id={a['user_id'] or '?'} "
              f"DM сегодня {used}/{dcap}  "
              f"группы {bused}/{bcap}{mark}{fl}")
        print(f"    риск: {account_health(conn, a['name'])}")
    print(f"  кампаний: {c('campaigns')}")
    print("  эффективность за 7 дней:")
    stats = campaign_stats(conn, hours=24 * 7)
    labels = {"dm": "личные", "broadcast": "группы"}
    if not stats:
        print("    нет отправок")
    for row in stats:
        total = row["total"] or 0
        rate = 100 * (row["ok"] or 0) / total if total else 0
        print(f"    {labels.get(row['kind'], row['kind'])}: ok {row['ok']} из {total} "
              f"({rate:.0f}%), отказов {row['fail']}, пропусков {row['skip']}")
    print("  доставка за 7 дней (ok = принято API, reply = человек ответил):")
    delivery = delivery_report(conn, days=7)
    if not delivery:
        print("    нет данных — ответы и блокировки появятся после первых отправок")
    for d in delivery:
        print(f"    {labels.get(d['kind'], d['kind'])}: принято {d['accepted']} из {d['total']}, "
              f"ответили {d['replied']} ({d['reply_rate']:.1f}%), "
              f"заблокировали {d['blocked']} ({d['block_rate']:.1f}%), "
              f"тишина {d['silent']}, отказ {d['failed']}")
        if d["accepted"] and not d["replied"]:
            print("      ! ни одного ответа: доставка не доказана — меняйте текст/аудиторию, "
                  "а не объём")
    print("  последние отправки:")
    for s in conn.execute("SELECT s.campaign,c.kind,s.target,s.status,s.ts FROM sent s "
                          "JOIN campaigns c ON c.id=s.campaign "
                          "ORDER BY s.ts DESC LIMIT 5"):
        print(f"    #{s['campaign']} {s['kind']} -> {s['target']}: {s['status']} ({s['ts']})")
    free = conn.execute(
        f"SELECT COUNT(*) c FROM members m WHERE {DM_ELIGIBLE_WHERE}").fetchone()["c"]  # nosec B608
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
        "  WHEN m.user_id IN (SELECT CAST(s.target AS INTEGER) FROM campaigns c "
        "       JOIN sent s ON s.campaign=c.id AND s.status='ok' WHERE c.kind='dm')"
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


def _both_stopped(names, started_at):
    if stop_requested("all", started_at) or stop_requested("conductor", started_at):
        return True
    return any(stop_requested(name, started_at) for name in names)


def _broadcast_shadow(players, queues):
    shadow = {}
    for name, player in players.items():
        shadow[name] = {
            "budget": player["broadcast_budget"],
            "used": player["broadcast_used"] + len(queues[name]["broadcast"]),
            "flood_until": player["flood_until"],
        }
    return shadow


def _player_view(players, kind, now):
    view = {}
    for name, player in players.items():
        if cooldown_remaining(player, now) or not channel_open(player, kind, now):
            continue
        cap = player["broadcast_budget"] if kind == "broadcast" else player["dm_budget"]
        used = player["broadcast_used"] if kind == "broadcast" else player["dm_used"]
        view[name] = {"budget": cap, "used": used, "flood_until": None,
                      "spam_strikes": player.get("spam_strikes") or 0}
    return view


def _idle_deadlines(conn, players, slow_until, now):
    items = [next_utc_midnight(now), now + timedelta(seconds=DIALOG_REFRESH_SECONDS)]
    for player in players.values():
        items.append(player.get("flood_until"))
        items.append(player.get("dm_halt_until"))
    items.extend(slow_until.values())
    cutoff = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT MIN(s.ts) ts FROM sent s JOIN campaigns c ON c.id=s.campaign "
        "WHERE c.kind='broadcast' AND s.status='ok' AND s.ts>=?",
        (cutoff,)).fetchone()
    if row and row["ts"]:
        ts = datetime.fromisoformat(row["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        items.append(ts + timedelta(hours=24))
    return items


async def _sleep_chunked(seconds, names, started_at):
    left = float(seconds)
    while left > 0:
        if _both_stopped(names, started_at):
            return False
        step = min(left, BOTH_SLEEP_CAP)
        await asyncio.sleep(step)
        left -= step
    return not _both_stopped(names, started_at)


async def cmd_conduct_both(args):
    """Группы и личные вместе, без окна времени.

    Один процесс держит сессии включённых аккаунтов. Круг набирает ещё допустимые
    цели и засыпает, когда Телеграм или дневной потолок не дают слать сейчас.
    Сухой прогон печатает один план и выходит.
    """
    import time as _t
    conn = connect()
    players = {}
    try:
        forward_ids_ = apply_forward_mode(args, conn)
        media = None if forward_ids_ else resolve_media(conn, getattr(args, "media", None))
        text = "" if forward_ids_ else read_message(args)
        opt_out = bool(getattr(args, "opt_out", True))
        names = args.accounts or [r["name"] for r in conn.execute(
            "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
        from telethon.tl.types import Chat, Channel

        for name in names:
            client = None
            try:
                client = get_client(name, allow_flood=True)
                await client.connect()
                if not await client.is_user_authorized():
                    print(f"  пропуск «{name}»: сессия не авторизована — login --account {name}")
                    await client.disconnect()
                    continue
                me = await client.get_me()
                conn.execute("UPDATE accounts SET user_id=? WHERE name=?", (me.id, name))
                sync_account_age(conn, name, getattr(me, "bot_registered", None))
                arow = conn.execute(
                    "SELECT daily_budget, broadcast_budget, flood_until, COALESCE(spam_strikes,0) "
                    "strikes FROM accounts WHERE name=?", (name,)).fetchone()
                bcap = (arow["broadcast_budget"] if arow and arow["broadcast_budget"] is not None else 15)
                draw = arow["daily_budget"] if arow else None
                dcap = account_dm_cap(conn, name, draw)
                bused = today_broadcast_sent(conn, name)
                dused = today_dm_sent(conn, name)
                players[name] = {
                    "client": client, "me": me.id,
                    "broadcast_budget": bcap, "broadcast_used": bused,
                    "dm_budget": dcap, "dm_used": dused,
                    "flood_until": arow["flood_until"] if arow else None,
                    "dm_halt_until": None, "held_dm": set(),
                    "spam_strikes": arow["strikes"] if arow else 0,
                    "budget": bcap, "used": bused,
                }
                print(f"  игрок «{name}» (id={me.id}): группы {bused}/{bcap or '∞'}, "
                      f"личные {dused}/{dcap or '∞'}")
            except SystemExit as exc:
                if client is not None and name not in players:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                print(f"  пропуск «{name}»: {exc}")
            except Exception as exc:
                if client is not None and name not in players:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                print(f"  пропуск «{name}»: {type(exc).__name__}: {str(exc)[:80]}")
        conn.commit()
        if not players:
            sys.exit("Ни один аккаунт не подключён — login --account NAME")

        if forward_ids_:
            # Источник резолвится отдельно для каждого аккаунта: закрытый канал
            # виден только его участникам, и у каждого свой кэш entity.
            await _attach_sources(players, args.from_source)
            if not players:
                sys.exit("ни один аккаунт не видит источник — подпишитесь на канал")
            print(f"  режим пересылки: {len(forward_ids_)} сообщ. из «{args.from_source}» "
                  f"(drop_author={bool(args.drop_author)})")

        started_at = now_iso()
        names = list(players)
        edges, emaps = {}, {}
        slow_until = {}
        dialogs_at = None
        camp_text = f"forward:{args.from_source}:{forward_ids_[:3]}" if forward_ids_ else text

        def refresh_caps():
            for name, player in players.items():
                row = conn.execute(
                    "SELECT flood_until, COALESCE(spam_strikes,0) strikes FROM accounts "
                    "WHERE name=?", (name,)).fetchone()
                if row:
                    player["flood_until"] = row["flood_until"]
                    player["spam_strikes"] = row["strikes"]
                player["broadcast_used"] = today_broadcast_sent(conn, name)
                player["dm_used"] = today_dm_sent(conn, name)
                player["budget"] = player["broadcast_budget"]
                player["used"] = player["broadcast_used"]
                if player.get("dm_halt_until") and not cooldown_remaining(
                        {"flood_until": player["dm_halt_until"]}):
                    player["dm_halt_until"] = None
                    player["held_dm"].clear()

        async def refresh_edges(force):
            nonlocal dialogs_at
            if not force and dialogs_at is not None and (_t.monotonic() - dialogs_at) < DIALOG_REFRESH_SECONDS:
                return
            for name, player in players.items():
                try:
                    if not player["client"].is_connected():
                        await player["client"].connect()
                    dialogs = await player["client"].get_dialogs()
                except Exception as exc:
                    print(f"  диалоги «{name}» не обновлены: {type(exc).__name__}: {str(exc)[:80]}")
                    continue
                emaps[name] = {
                    d.entity.id: d.entity for d in dialogs if isinstance(d.entity, (Chat, Channel))
                }
                writable = set()
                for gid, ent in emaps[name].items():
                    can, why = classify_broadcast(ent)
                    if can:
                        writable.add(gid)
                        if args.dry_run:
                            record_edge(conn, name, gid)
                        else:
                            apply_write_edge(conn, name, gid, True, "")
                    else:
                        apply_write_edge(conn, name, gid, False, why)
                edges[name] = writable
                print(f"  покрытие «{name}»: диалогов {len(emaps[name])}, можно писать {len(writable)}")
            dialogs_at = _t.monotonic()
            conn.commit()

        def load_targets(now):
            recent = set() if args.repeat_ok else recent_ok_targets(conn, "broadcast", 24)
            query = "SELECT * FROM groups WHERE active=1 AND in_broadcast=1"
            params = []
            if args.ids:
                query += " AND id IN (%s)" % ",".join("?" * len(args.ids))
                params = list(args.ids)
            groups = []
            for row in conn.execute(query, params):
                if str(row["id"]) in recent:
                    continue
                ready = slow_until.get(row["id"])
                if ready and ready > now:
                    continue
                if any(row["id"] in edge for edge in edges.values()):
                    groups.append(row)
            people = dm_targets(conn, group_id=args.group, repeat_ok=args.repeat_ok, shuffle=True)
            me_ids = {player["me"] for player in players.values()}
            held = set()
            for player in players.values():
                if cooldown_remaining({"flood_until": player.get("dm_halt_until")}, now):
                    held |= set(player["held_dm"])
            people = [row for row in people if row["user_id"] not in me_ids and row["user_id"] not in held]
            people = interleave_targets(people)
            if args.limit:
                groups = groups[:args.limit]
                people = people[:args.limit]
            return groups, people

        async def one_round():
            if _both_stopped(names, started_at):
                return 0
            now = datetime.now(timezone.utc)
            refresh_caps()
            await refresh_edges(force=dialogs_at is None or args.dry_run)
            groups, people = load_targets(now)
            gplan, g_unplanned = assign_targets(
                "broadcast", groups, _player_view(players, "broadcast", now), edges)
            dplan, d_unplanned = assign_targets(
                "dm", people, _player_view(players, "dm", now), None)
            print("раскладка групп: " + ", ".join(
                f"«{name}» {sum(1 for who, _row in gplan if who == name)}" for name in players)
                + f", без слота {g_unplanned}")
            print("раскладка личных: " + ", ".join(
                f"«{name}» {sum(1 for who, _row in dplan if who == name)}" for name in players)
                + f", без слота {d_unplanned}")
            if args.dry_run:
                for who, row in gplan:
                    print(f"  [dry] {who or 'НИКОМУ'}: группа {row['id']} «{(row['title'] or '')[:44]}»")
                for who, row in dplan:
                    print(f"  [dry] {who or 'НЕТ БЮДЖЕТА'}: @{row['username'] or '-'} id={row['user_id']}")
                print(f"Сухой план: групп {len(groups)}, людей {len(people)}; "
                      f"аккаунтов {len(players)} — ничего не отправлено")
                return 0

            queues = {name: {"broadcast": collections.deque(), "dm": collections.deque()} for name in players}
            for who, row in gplan:
                if who:
                    queues[who]["broadcast"].append(row)
            for who, row in dplan:
                if who:
                    queues[who]["dm"].append(row)
            if not any(queue["broadcast"] or queue["dm"] for queue in queues.values()):
                print("  сейчас нечего отправлять")
                return 0
            camps = {}
            if any(queue["broadcast"] for queue in queues.values()):
                cur = conn.execute(
                    "INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
                    ("broadcast", camp_text, now_iso(), "conductor"))
                camps["broadcast"] = cur.lastrowid
            if any(queue["dm"] for queue in queues.values()):
                cur = conn.execute(
                    "INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
                    ("dm", camp_text, now_iso(), "conductor"))
                camps["dm"] = cur.lastrowid
            conn.commit()
            stats = {"ok": 0}
            handed_off = set()

            async def worker(name):
                player = players[name]
                deferred = None
                blocked = set()
                while not _both_stopped(names, started_at):
                    rem = cooldown_remaining(player)
                    if rem:
                        await asyncio.sleep(min(rem, BOTH_SLEEP_CAP))
                        continue
                    if deferred is not None:
                        kind, row = deferred
                        deferred = None
                        if not channel_open(player, kind):
                            queues[name][kind].appendleft(row)
                            blocked.add(kind)
                            if blocked >= {"broadcast", "dm"} or not any(queues[name].values()):
                                return
                            continue
                    else:
                        has_group = (
                            "broadcast" not in blocked
                            and bool(queues[name]["broadcast"])
                            and channel_open(player, "broadcast")
                        )
                        has_dm = (
                            "dm" not in blocked
                            and bool(queues[name]["dm"])
                            and channel_open(player, "dm")
                        )
                        last_any = player.get("last_mono")
                        since = None if last_any is None else _t.monotonic() - last_any
                        kind = next_channel(has_group, has_dm, since, player.get("last_kind"))
                        if kind is None:
                            if not queues[name]["broadcast"] and not queues[name]["dm"]:
                                return
                            if queues[name]["dm"] and since is not None and since < DM_MIN_GAP:
                                await asyncio.sleep(min(DM_MIN_GAP - since, BOTH_SLEEP_CAP))
                                continue
                            return
                        row = queues[name][kind].popleft()
                    if kind == "broadcast":
                        gid = row["id"]
                        ent = emaps.get(name, {}).get(gid)
                        can, why = classify_broadcast(ent)
                        if not can:
                            apply_write_edge(conn, name, gid, False, why)
                            conn.commit()
                            held = edges.get(name)
                            if held is not None:
                                held.discard(gid)
                            print(f"  пропуск [{name}] группа {gid}: {REASON_RU.get(why, why)}")
                            other = handoff_broadcast(row, name, _broadcast_shadow(players, queues), edges)
                            if other:
                                queues[other]["broadcast"].append(row)
                                handed_off.add(other)
                                print(f"  передаю группу {gid} «{(row['title'] or '')[:40]}» {name} → {other}")
                            continue
                        if ent is not None and slowmode_exceeds(ent, DIALOG_REFRESH_SECONDS):
                            seconds = int(getattr(ent, "slowmode_seconds", 0) or 0)
                            slow_until[gid] = datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 1))
                            print(f"  пропуск [{name}] группа {gid}: {REASON_RU['slowmode']}")
                            continue
                        peer = pick_target(emaps.get(name, {}), row)
                        key = str(group_key(gid))
                        label = f"группа {gid} «{(row['title'] or '')[:44]}»"
                        ctx = chat_context(row)
                    else:
                        peer = dm_peer(row) or row["user_id"]
                        key = str(row["user_id"])
                        label = f"@{row['username'] or '-'} id={row['user_id']}"
                        ctx = dm_context(row)
                        if not await _dm_peer_known(player["client"], peer):
                            conn.execute(
                                "INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                                "VALUES(?,?,?,?,?)",
                                (camps[kind], key, "skip:unknown_peer", now_iso(), name))
                            conn.commit()
                            print(f"  skip [{name}] {label}: аккаунт не знает получателя")
                            continue
                    body = "" if forward_ids_ else prepare_message(
                        text, ctx, opt_out=(kind == "dm" and opt_out), media=bool(media))
                    sent_ok = False
                    try:
                        if not player["client"].is_connected():
                            await player["client"].connect()
                        if forward_ids_:
                            sent_msg = await forward_batch(player["client"], kind, peer,
                                                           player["source"], forward_ids_, args)
                        else:
                            sent_msg = await deliver_with_reconnect(
                                player["client"], media, peer, body,
                                no_preview=bool(getattr(args, "no_preview", False)),
                                on_retry=lambda: print(
                                    f"  связь [{name}] оборвалась, поднимаю ту же сессию"))
                        sent_ok = True
                    except Exception as exc:
                        disp = target_disposition(exc, kind)
                        if disp == "defer_same_account":
                            fu = flood_pause_until(exc)
                            player["flood_until"] = fu
                            deferred = (kind, row)
                            conn.execute(
                                "UPDATE accounts SET flood_until=? WHERE name=?", (fu, name))
                            conn.commit()
                            pending = list(queues[name]["broadcast"])
                            queues[name]["broadcast"].clear()
                            stay, moves = reassign_groups_on_flood(
                                name, pending, _broadcast_shadow(players, queues), edges)
                            queues[name]["broadcast"].extend(stay)
                            for other, moved in moves:
                                queues[other]["broadcast"].append(moved)
                                handed_off.add(other)
                                print(f"  передаю группу {moved['id']} "
                                      f"«{(moved['title'] or '')[:40]}» {name} → {other}")
                            seconds = int(getattr(exc, "seconds", 0) or 0)
                            print(f"  ⏸ «{name}»: {type(exc).__name__} {seconds} c "
                                  f"(пауза до {fu[11:16]} UTC) — эта цель остаётся ему")
                        elif disp in ("halt_dm_queue", "halt_account") and kind == "dm":
                            held_rows = hold_dm_queue(row, list(queues[name]["dm"]))
                            queues[name]["dm"].clear()
                            player["held_dm"].update(item["user_id"] for item in held_rows)
                            strikes = register_peerflood(conn, name)
                            conn.commit()
                            player["dm_halt_until"] = peerflood_pause_until(strikes)
                            player["spam_strikes"] = strikes
                            print(f"  ⏸ «{name}»: {type(exc).__name__} — личные на "
                                  f"{player['dm_halt_until'][:16]} UTC (страйк {strikes}), "
                                  f"эти люди другим аккаунтам не отдаются")
                        elif disp == "halt_account":
                            strikes = register_peerflood(conn, name)
                            player["spam_strikes"] = strikes
                            fu = peerflood_pause_until(strikes)
                            player["flood_until"] = fu
                            held_rows = hold_dm_queue(None, list(queues[name]["dm"]))
                            queues[name]["dm"].clear()
                            player["held_dm"].update(item["user_id"] for item in held_rows)
                            player["dm_halt_until"] = fu
                            conn.execute(
                                "UPDATE accounts SET flood_until=? WHERE name=?", (fu, name))
                            conn.commit()
                            print(f"  ⏸ «{name}»: {type(exc).__name__} — аккаунт молчит до {fu[11:16]} UTC")
                            return
                        elif disp == "skip":
                            seconds = int(getattr(exc, "seconds", 0) or 0)
                            if kind == "broadcast":
                                slow_until[row["id"]] = datetime.now(timezone.utc) + timedelta(
                                    seconds=max(seconds, 1))
                            print(f"  skip [{name}] {label}: {REASON_RU['slowmode']}")
                        else:
                            status = f"fail:{type(exc).__name__}"
                            if disp == "remember":
                                remember_permanent(conn, kind, key, permanent_reason(exc, kind), name)
                            conn.execute(
                                "INSERT OR REPLACE INTO sent(campaign,target,status,ts,account) "
                                "VALUES(?,?,?,?,?)",
                                (camps[kind], key, status, now_iso(), name))
                            conn.commit()
                            print(f"  fail [{name}] {label}: {type(exc).__name__}: {str(exc)[:80]}")
                    if not sent_ok:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO sent(campaign,target,status,ts,account,msg_id) "
                        "VALUES(?,?,?,?,?,?)",
                        (camps[kind], key, "ok", now_iso(), name, message_id(sent_msg)))
                    conn.commit()
                    stats["ok"] += 1
                    if kind == "broadcast":
                        player["broadcast_used"] += 1
                        player["used"] = player["broadcast_used"]
                    else:
                        player["dm_used"] += 1
                    print(f"  ok  [{name}] {label}")
                    player["last_mono"] = _t.monotonic()
                    player["last_kind"] = kind
                    lo, hi = GROUP_PAUSE if kind == "broadcast" else DM_PAUSE
                    await asyncio.sleep(random.uniform(lo, hi))

            queued = sum(len(q["broadcast"]) + len(q["dm"]) for q in queues.values())
            print(f"Круг both: целей {queued}, аккаунтов {len(players)}")
            batch = list(players)
            while batch and not _both_stopped(names, started_at):
                handed_off.clear()
                await asyncio.gather(*(worker(name) for name in batch))
                # Переданные задачи могли прийти в очередь аккаунта, чей воркер уже вышел.
                batch = [
                    name for name in sorted(handed_off)
                    if queues[name]["broadcast"] and channel_open(players[name], "broadcast")
                ]
            print(f"  круг завершён: ok {stats['ok']}")
            return stats["ok"]

        total_sent = 0
        while True:
            if _both_stopped(names, started_at):
                print("остановка")
                break
            sent = await one_round()
            total_sent += int(sent or 0)
            if not both_should_continue(args.dry_run, _both_stopped(names, started_at)):
                break
            if sent:
                continue
            now = datetime.now(timezone.utc)
            moment = earliest_moment(now, _idle_deadlines(conn, players, slow_until, now))
            seconds = wait_seconds(now, moment)
            stamp = moment.isoformat(timespec="seconds") if moment else "следующая проверка"
            print(f"  сплю {int(seconds)} с, ближайшее событие {stamp}")
            if not await _sleep_chunked(seconds, names, started_at):
                print("остановка")
                break
        return total_sent
    finally:
        for player in players.values():
            try:
                await player["client"].disconnect()
            except Exception:
                pass
        conn.close()


async def cmd_conduct(args):
    """Дирижёр: одна кампания на все включённые аккаунты. Общий организм:
    единая база целей/журнала/чёрного списка. FloodWait и FloodPremiumWait
    оставляют текущую цель этому аккаунту. PeerFlood на DM останавливает очередь
    и не отдаёт её другим.
    Дневные бюджеты считаются на аккаунт; группу отправляет тот, кто может писать."""
    if args.what == "both":
        return await cmd_conduct_both(args)
    import time as _t
    conn = connect()
    players = {}
    try:
        forward_ids_ = apply_forward_mode(args, conn)
        media = None if forward_ids_ else resolve_media(conn, getattr(args, "media", None))
        text = "" if forward_ids_ else read_message(args)
        kind = args.what
        opt_out = bool(getattr(args, "opt_out", True))
        names = args.accounts or [r["name"] for r in conn.execute(
            "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
        from telethon.tl.types import Chat, Channel

        for n in names:
            c = None
            try:
                c = get_client(n, allow_flood=True)  # cooldown'ами рулит дирижёр
                await c.connect()
                if not await c.is_user_authorized():
                    print(f"  пропуск «{n}»: сессия не авторизована — login --account {n}")
                    await c.disconnect()
                    continue
                me = await c.get_me()
                conn.execute("UPDATE accounts SET user_id=? WHERE name=?", (me.id, n))
                sync_account_age(conn, n, getattr(me, "bot_registered", None))
                arow = conn.execute(
                    "SELECT daily_budget, broadcast_budget, flood_until FROM accounts WHERE name=?",
                    (n,)).fetchone()
                if kind == "dm":
                    raw = arow["daily_budget"] if arow else None
                    budget = account_dm_cap(conn, n, raw)
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
                if c is not None and n not in players:
                    try:
                        await c.disconnect()
                    except Exception:
                        pass
                print(f"  пропуск «{n}»: {e}")
            except Exception as e:
                if c is not None and n not in players:
                    try:
                        await c.disconnect()
                    except Exception:
                        pass
                print(f"  пропуск «{n}»: {type(e).__name__}: {str(e)[:80]}")
        conn.commit()
        if not players:
            sys.exit("Ни один аккаунт не подключён — login --account NAME")

        if forward_ids_:
            await _attach_sources(players, args.from_source)
            if not players:
                sys.exit("ни один аккаунт не видит источник — подпишитесь на канал")
            print(f"  режим пересылки: {len(forward_ids_)} сообщ. из «{args.from_source}» "
                  f"(drop_author={bool(args.drop_author)})")

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
            rows = dm_targets(conn, group_id=args.group, repeat_ok=args.repeat_ok, shuffle=True)
            me_ids = {p["me"] for p in players.values()}
            rows = [r for r in rows if r["user_id"] not in me_ids]  # операторам не пишем
            rows = interleave_targets(rows)
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
            return 0

        if not any(who for who, _ in plan):
            sys.exit("Нет целей (всё отправлено/покрыто/в blacklist/бюджеты)")

        camp_text = f"forward:{args.from_source}:{forward_ids_[:3]}" if forward_ids_ else text
        cur = conn.execute("INSERT INTO campaigns(kind,text,created_at,account) VALUES(?,?,?,?)",
                           (kind, camp_text, now_iso(), "conductor"))
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
        handed_off = set()

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
            handed_off.add(other)
            print(f"  передаю группу {gid} «{row['title'][:40]}» {name} → {other}")
            return True

        started_at = now_iso()

        async def worker(name):
            p = players[name]
            skipped_groups = set()
            while _t.monotonic() < deadline and not stop_dm.is_set():
                if stop_requested(name, started_at) or stop_requested("all", started_at):
                    print(f"  остановка запрошена для «{name}»")
                    return
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
                    key = str(group_key(gid))
                    label = f"группа {gid} «{row['title'][:44]}»"
                    ctx = chat_context(row)
                else:
                    peer = dm_peer(row) or row["user_id"]
                    key = str(row["user_id"])
                    label = f"@{row['username'] or '-'} id={row['user_id']}"
                    ctx = dm_context(row)
                    # Резолвим получателя заранее: без ника аккаунт может не знать
                    # этот user_id, и send_file падал ValueError уже после списания
                    # бюджета. Незнакомых просто пропускаем — жалоб ноль, счёт цел.
                    if not await _dm_peer_known(p["client"], peer):
                        conn.execute("INSERT OR REPLACE INTO sent"
                                     "(campaign,target,status,ts,account) VALUES(?,?,?,?,?)",
                                     (camp, key, "skip:unknown_peer", now_iso(), name))
                        conn.commit()
                        print(f"  skip [{name}] {label}: аккаунт не знает получателя")
                        continue
                body = "" if forward_ids_ else prepare_message(
                    text, ctx, opt_out=(kind == "dm" and opt_out), media=bool(media))
                try:
                    if forward_ids_:
                        sent_msg = await forward_batch(p["client"], kind, peer, p["source"],
                                                       forward_ids_, args)
                    else:
                        sent_msg = await deliver_with_reconnect(
                            p["client"], media, peer, body,
                            no_preview=bool(getattr(args, "no_preview", False)),
                            on_retry=lambda: print(
                                f"  связь [{name}] оборвалась, поднимаю ту же сессию"))
                    conn.execute("INSERT OR REPLACE INTO sent(campaign,target,status,ts,account,msg_id) "
                                 "VALUES(?,?,?,?,?,?)",
                                 (camp, key, "ok", now_iso(), name, message_id(sent_msg)))
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
                        strikes = register_peerflood(conn, name)
                        p["spam_strikes"] = strikes
                        fu = peerflood_pause_until(strikes)
                        p["flood_until"] = fu
                        conn.commit()
                        print(f"  ⏸ «{name}»: {type(e).__name__} — пауза до {fu[11:16]} UTC "
                              f"(страйк {strikes}), очередь другим не передаётся")
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
                    verb = "skip" if disp == "skip" else "fail"
                    print(f"  {verb} [{name}] {label}: {type(e).__name__}: {str(e)[:80]}")
                await asyncio.sleep(random.uniform(dmin, dmax))

        queued = sum(len(q) for q in queues.values())
        print(f"Кампания #{camp} ({kind}, дирижёр): старт, аккаунтов {len(players)}, целей {queued}")
        batch = list(players)
        while batch and _t.monotonic() < deadline and not stop_dm.is_set():
            handed_off.clear()
            await asyncio.gather(*(worker(n) for n in batch))
            batch = [
                n for n in sorted(handed_off)
                if queues[n] and remaining_cap(players[n]) > 0
                and not (stop_requested(n, started_at) or stop_requested("all", started_at))
            ]
        leftover = sum(len(q) for q in queues.values()) + len(deferred)
        print(f"Итог: ok {stats['ok']}, fail {stats['fail']}, "
              f"не разобрано {leftover} (бюджеты/паузы/окно {args.window} ч)")
        if leftover:
            print("  повторите conduct позже — недобратое подхватится по состоянию аккаунтов")
        return stats["ok"]
    finally:
        for p in players.values():
            try:
                await p["client"].disconnect()
            except Exception:
                pass
        conn.close()


# ---------------------------------------------------------------- CLI

def _add_forward_flags(p):
    """Режим пересылки: сообщения живут в закрытом канале-источнике, а наружу
    уходит forward. Применяется к broadcast и dmsend."""
    p.add_argument("--from-source", dest="from_source", default=None,
                   help="@канал, id или ссылка t.me/... — откуда пересылать (вместо --text)")
    p.add_argument("--msg-id", dest="msg_id", type=int, nargs="+", default=None,
                   help="id сообщений в источнике (несколько — альбом/цепочка)")
    p.add_argument("--forward-from", dest="forward_from", choices=["raw", "self"], default="raw",
                   help="raw: скрыть автора в чате (по умолчанию); self: переслать со своим заголовком")
    p.add_argument("--keep-author", dest="drop_author", action="store_false", default=True,
                   help="не скрывать заголовок автора (по умолчанию скрыт в режиме raw)")
    p.add_argument("--no-preview", dest="no_preview", action="store_true",
                   help="не строить предпросмотр ссылок в текстовой рассылке")


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
    p.add_argument("--enable", type=int, nargs="+", default=None,
                   help="снять ручной стоп-лист: вернуть эти группы в рассылку")
    p.set_defaults(func=cmd_groups)

    p = sub.add_parser("watch", parents=[ap],
                       help="дежурный режим: новые группы добавляются автоматически")
    p.add_argument("--noreply", action="store_true", help="не отвечать на «стоп»")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("discover", parents=[ap],
                       help="найти новые каналы/группы по гео (Прага) и темам")
    p.add_argument("--theme", default="", choices=["", "rent", "jobs", "market", "news"],
                   help="тема аудитории: rent | jobs | market | news (пусто — все)")
    p.add_argument("--queries", nargs="*", help="свои поисковые фразы вместо готовых")
    p.add_argument("--verify", action="store_true",
                   help="проверить права записи (запрос по каждой группе, дороже по лимитам)")
    p.add_argument("--export", default="", help="куда выгрузить CSV-отчёт по найденному")
    p.add_argument("--delay-min", type=float, default=4.0)
    p.add_argument("--delay-max", type=float, default=9.0)
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("seed", parents=[ap],
                       help="влить проверенный список каналов из файла username;title;members;tag")
    p.add_argument("--file", required=True)
    p.add_argument("--delay-min", type=float, default=2.0)
    p.add_argument("--delay-max", type=float, default=3.5)
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.set_defaults(func=cmd_seed)

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
    _add_forward_flags(p)
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
    p.add_argument("--no-opt-out", dest="opt_out", action="store_false", default=True,
                   help="не добавлять в конец просьбу ответить «стоп» (по умолчанию добавляется)")
    _add_forward_flags(p)
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

    p = sub.add_parser("report", help="что реально работает: кампании, источники, вердикт")
    p.add_argument("--days", type=int, default=30, help="за сколько дней (по умолчанию 30)")
    p.add_argument("--min-sample", dest="min_sample", type=int, default=10,
                   help="минимум принятых отправок, чтобы считать кампанию показательной")
    p.set_defaults(func=cmd_report)

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
    p.add_argument("--what", choices=["dm", "broadcast", "both"], required=True)
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
    p.add_argument("--loop", action="store_true",
                   help="нон-стоп: повторять кампанию раундами, пока не придёт bot.py stop")
    p.add_argument("--loop-gap", type=float, default=300.0, dest="loop_gap",
                   help="минимальная пауза между раундами в секундах (по умолчанию 300)")
    p.add_argument("--loop-jitter", type=float, default=300.0, dest="loop_jitter",
                   help="случайная добавка к паузе, секунды (по умолчанию 300)")
    p.add_argument("--no-opt-out", dest="opt_out", action="store_false", default=True,
                   help="не добавлять в конец DM просьбу ответить «стоп»")
    _add_forward_flags(p)
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.set_defaults(func=cmd_conduct, max_runs=0)

    p = sub.add_parser("profile", parents=[ap],
                       help="profile status|apply — единое оформление всех аккаунтов")
    p.add_argument("op", choices=["status", "apply"])
    p.add_argument("--accounts", nargs="*", help="подмножество аккаунтов (по умолчанию все включённые)")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("join", parents=[ap],
                       help="join — все включённые аккаунты в один и тот же набор групп")
    p.add_argument("--accounts", nargs="*", help="подмножество аккаунтов (по умолчанию все включённые)")
    p.add_argument("--groups", type=int, nargs="*", help="только эти id групп")
    p.add_argument("--limit", type=int, default=10, help="вступлений на аккаунт за запуск")
    p.add_argument("--delay-min", type=float, default=20.0)
    p.add_argument("--delay-max", type=float, default=60.0)
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.set_defaults(func=cmd_join)

    p = sub.add_parser("gates", parents=[ap],
                       help="пройти проверки на бота в группах (нажимает только безопасные кнопки)")
    p.add_argument("--accounts", nargs="*", help="подмножество аккаунтов (по умолчанию все включённые)")
    p.add_argument("--groups", type=int, nargs="*", help="только эти id групп")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.set_defaults(func=cmd_gates)

    p = sub.add_parser("orchestra", parents=[ap],
                       help="матрица покрытия: кто в каких группах и где набор неполный")
    p.set_defaults(func=cmd_orchestra)

    p = sub.add_parser("media", parents=[ap],
                       help="медиа-библиотека через «Избранное»: add|pull|sync|del|list")
    p.add_argument("op", choices=["add", "pull", "sync", "del", "list"])
    p.add_argument("value", nargs="?", default="",
                   help="add: путь к файлу; sync/del: id из list")
    p.add_argument("--limit", type=int, default=30,
                   help="pull: сколько последних сообщений Избранного просмотреть")
    p.set_defaults(func=cmd_media)

    p = sub.add_parser("stop", help="остановить выполняемую рассылку аккаунта или всех аккаунтов")
    p.add_argument("--account", default="all")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("cloud", help="облачное зеркало Supabase: push|status|test")
    p.add_argument("op", choices=["push", "status", "test"])
    p.set_defaults(func=cmd_cloud)
    return pr


LOOP_EMPTY_CAP = 1800   # потолок паузы после пустых раундов, секунды


def loop_gap(round_no, base, jitter, empty_streak=0):
    """Пауза между раундами нон-стоп рассылки.

    base — минимальная пауза в секундах, jitter — случайная добавка сверху.
    Ровные интервалы сами по себе выглядят как бот: разброс делает ритм живым.

    empty_streak — сколько раундов подряд ничего не отправили. Пустой раунд
    значит, что все цели в дедупе на сутки; долбить Telegram каждые 15 минут
    бессмысленно и вредно. Пауза растёт ступенчато (base -> 2x -> 4x ...,
    не выше LOOP_EMPTY_CAP), а первый же раунд с отправками сбрасывает её.
    """
    base = max(0.0, float(base or 0))
    jitter = max(0.0, float(jitter or 0))
    streak = max(0, int(empty_streak or 0))
    if streak:
        base = min(base * (2 ** min(streak, 6)), max(base, float(LOOP_EMPTY_CAP)))
    return base + (random.uniform(0, jitter) if jitter else 0.0)


LOOP_STOP_POLL = 1.0    # шаг проверки stop во время паузы --loop, секунды


def _sleep_until_or_stop(wait, started_at, step=LOOP_STOP_POLL):
    """Спать wait секунд мелкими порциями, прерываясь по сигналу stop."""
    remaining = max(0.0, float(wait or 0))
    while remaining > 0:
        if stop_requested("all", started_at):
            return False
        chunk = min(remaining, max(0.05, float(step)))
        time.sleep(chunk)
        remaining -= chunk
    return not stop_requested("all", started_at)


def run_endless(args, run_once):
    """Крутить run_once() раундами, пока не попросят остановиться.

    run_once — async-функция, возвращающая число отправленных (ok) или None,
    если раунд не имел целей. Остановка двумя способами:
      * control-stop.json (bot.py stop) — тот же сигнал, что и у conduct;
      * Ctrl+C.
    Раунд, в котором нечего было отправлять (всё в дедупе на 24 ч, бюджеты
    исчерпаны, все аккаунты в паузе), не считается ошибкой: ждём и пробуем
    снова. Без этого «нон-стоп» умирал бы каждые несколько часов.
    """
    started_at = now_iso()
    round_no = 0
    total_ok = 0
    empty_streak = 0
    while True:
        if stop_requested("all", started_at):
            print(f"[loop] остановка по сигналу после {round_no} раундов, "
                  f"отправлено {total_ok}")
            return total_ok
        round_no += 1
        print(f"\n=== раунд {round_no} — {now_iso()} ===")
        try:
            ok = asyncio.run(run_once())
        except SystemExit as exc:
            # conduct выходит через sys.exit, когда целей нет: это штатно.
            print(f"[loop] раунд {round_no}: {exc}")
            ok = 0
        except KeyboardInterrupt:
            print(f"\n[loop] остановлено пользователем, отправлено {total_ok}")
            return total_ok
        except Exception as exc:  # сеть/Telegram: не роняем весь нон-стоп
            print(f"[loop] раунд {round_no} упал: {type(exc).__name__}: {str(exc)[:160]}")
            ok = 0
        total_ok += int(ok or 0)
        # Пустой раунд = все цели в дедупе на сутки: наращиваем паузу,
        # чтобы не молотить Telegram вхолостую. Есть отправки — ритм обычный.
        empty_streak = 0 if int(ok or 0) > 0 else empty_streak + 1
        wait = loop_gap(round_no, args.loop_gap, args.loop_jitter, empty_streak)
        note = f" (пустых раундов подряд: {empty_streak})" if empty_streak else ""
        print(f"[loop] всего отправлено {total_ok}; пауза {int(wait)} c{note}")
        try:
            if not _sleep_until_or_stop(wait, started_at):
                print(f"[loop] остановка по сигналу во время паузы после {round_no} раундов, "
                      f"отправлено {total_ok}")
                return total_ok
        except KeyboardInterrupt:
            print(f"\n[loop] остановлено пользователем, отправлено {total_ok}")
            return total_ok


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = build_parser().parse_args()
    # Одна миграция на процесс до любой работы: дальше connect() уже не
    # пытается писать схему под чужой незакоммиченной транзакцией.
    connect().close()
    if getattr(args, "loop", False):
        run_endless(args, lambda: args.func(args))
        return
    res = args.func(args)
    if asyncio.iscoroutine(res):
        try:
            asyncio.run(res)
        except KeyboardInterrupt:
            print("\nостановлено пользователем")
    maybe_cloud_push(args)


if __name__ == "__main__":
    main()
