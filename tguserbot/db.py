"""Слой доступа к SQLite tg-userbot: жизненный цикл соединения, схема, миграции.

Без Telethon и бизнес-логики. Путь БД и сессия главного аккаунта передаются
параметрами: bot.py связывает их со своими глобалями (DB_PATH, LEGACY_SESSION)
в момент вызова, потому что скрипты и тесты перезаписывают эти атрибуты после
импорта bot. Владелец транзакции — вызывающий: write-хелперы не коммитят
(см. ADR-0001, стадия 2).
"""

from __future__ import annotations

import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

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
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, text TEXT, created_at TEXT,
  account TEXT DEFAULT 'main'
);
CREATE TABLE IF NOT EXISTS sent(
  campaign INTEGER, target TEXT, status TEXT, ts TEXT,
  msg_id INTEGER,          -- id отправленного сообщения: основа ответа/доставки
  replied_at TEXT,         -- человек ответил — единственный твёрдый сигнал «дошло»
  blocked_at TEXT,         -- он нас заблокировал: сигнал «не надо»
  account TEXT DEFAULT 'main',
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

# Один раз на конкретный файл базы в пределах процесса: тесты и --db подменяют
# путь, каждой новой базе нужна своя схема.
_SCHEMA_READY: set = set()

__all__ = ["SCHEMA", "connect", "db_call_retry", "migrate", "now_iso"]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path, *, migrate_schema=None, main_session=None):
    """Соединение с базой.

    migrate_schema: True — гарантированно прогнать схему и миграцию. По
    умолчанию — один раз на каждый файл базы в пределах процесса. Раньше connect()
    делал migrate() при каждом вызове, а migrate() пишет в базу
    (INSERT INTO accounts, ALTER, CREATE INDEX). Внутри cmd_conduct
    get_client() открывает своё соединение, пока у вызывающего висит
    незакоммиченная транзакция, — второе соединение на запись получало
    «database is locked» и аккаунт молча выпадал из прогона.

    main_session — путь session-файла для сида аккаунта «main» в migrate().
    """
    base = Path(path)
    if migrate_schema is None:
        migrate_schema = base not in _SCHEMA_READY
    conn = sqlite3.connect(base, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # читаем из web.py параллельно с записью
    # NORMAL + WAL: процессный сбой не теряет закоммиченную строку, а каждый
    # commit отправки не делает полный fsync и не стопорит остальные аккаунты.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")
    if migrate_schema:
        try:
            conn.executescript(SCHEMA)
            migrate(conn, main_session)
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


def migrate(conn, main_session=None):
    """Идемпотентно: колонка account у кампаний/отправок + сид главного аккаунта.

    main_session — путь session-файла для сида «main»; bot.py передаёт свой
    LEGACY_SESSION в момент вызова. None означает «сессия неизвестна» (строка
    аккаунта всё равно создаётся — так работает прямое использование пакета).
    """
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
                 (main_session, now_iso()))
    conn.commit()
