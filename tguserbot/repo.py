"""Репозиторий SQLite: CRUD-записи, участники и отбор очереди DM.

Только conn-функции без Telethon-сущностей: ent-логика (upsert_mailing_group,
persist_group_event, classify_*) остаётся в bot.py до стадии команд.
Владелец транзакции — вызывающий, если в докстринге не указано иное
(persist_stop_request и register_media коммитят сами). См. ADR-0001, стадия 2b.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from tguserbot.db import db_call_retry, now_iso
from tguserbot.pacing import peerflood_pause_until

__all__ = [
    "BLOCKED_REASONS", "DM_ELIGIBLE_WHERE", "NOT_YET_DMED", "SCRAPE_CHUNK",
    "account_age_days", "apply_write_edge", "candidate_ids", "dm_eligible_where",
    "dm_targets", "is_manually_blocked", "member_is_bot", "parse_registered_at",
    "record_block", "record_edge", "record_reply", "refresh_group_flags",
    "register_media", "register_peerflood", "remember_permanent", "save_member",
    "save_members", "sync_account_age", "username_is_bot",
]

# Причины, при которых группу выключили ОСОЗНАННО: watch/--sync их не
# отменяют. Иначе дежурный режим добавляет чат по первому же сообщению и
# возвращает в рассылку то, что уже признали непригодным (отклоняет
# перефорвард, платная, закрытая тема). Право записи тут ни при чём.
BLOCKED_REASONS = ("forward_blocked", "payment", "topic_closed", "restricted",
                   "client_chat")


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


def parse_registered_at(value):
    """Нормализовать дату регистрации аккаунта (YYYY-MM-DD / ISO / unix ts) в UTC ISO."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("нужна дата в формате YYYY-MM-DD")
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), timezone.utc).isoformat(timespec="seconds")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="seconds")


def sync_account_age(conn, account, registration_ts=None, now=None, *, overwrite=False):
    """Сохранить дату регистрации аккаунта (unix ts или ISO-дату) и вернуть возраст в днях.

    Telethon User не отдаёт дату регистрации обычного аккаунта, поэтому значение
    задаётся явно (`accounts registered <name> YYYY-MM-DD`) или внешним источником.
    При overwrite=False существующая дата не перезаписывается.
    """
    if not registration_ts:
        return None
    now = now or datetime.now(timezone.utc)
    iso = parse_registered_at(registration_ts)
    if overwrite:
        conn.execute("UPDATE accounts SET registered_at=? WHERE name=?", (iso, account))
    else:
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


def persist_stop_request(conn, user_id, username, *, attempts=8, first_delay=0.5,
                         max_delay=8.0, sleep=time.sleep):
    """«стоп» в DM -> blacklist. True = записан, False = база так и не освободилась."""
    def _write():
        conn.execute("INSERT OR IGNORE INTO blacklist(user_id,username,reason,ts) "
                     "VALUES(?,?,?,?)",
                     (user_id, username or "", "самоотписка в DM", now_iso()))
        conn.commit()
    try:
        db_call_retry(_write, attempts=attempts, first_delay=first_delay,
                      max_delay=max_delay, sleep=sleep)
        return True
    except sqlite3.OperationalError as exc:
        conn.rollback()   # сброс частичной записи: не держим лок и не даём ей закоммититься позже
        text = str(exc)
        kind = "база была занята" if ("locked" in text.lower() or "busy" in text.lower()) else f"ошибка БД: {text}"
        print(f"[blacklist] {user_id}: не записан ({kind})")
        return False


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


def apply_write_edge(conn, account, gid, can, why):
    """Записать право писать и заново решить, остаётся ли группа в рассылке."""
    record_edge(conn, account, gid, can_write=1 if can else 0)
    refresh_group_flags(conn, gid, "" if can else (why or "no_send"))


def username_is_bot(username):
    """Ник Telegram-бота обязан заканчиваться на bot. Имена вроде Subbotina и Rabota — нет."""
    return bool(username) and str(username).lower().endswith("bot")


def member_is_bot(u):
    return int(bool(getattr(u, "bot", False)) or username_is_bot(getattr(u, "username", None)))


_MEMBER_INSERT = (
    "INSERT OR IGNORE INTO members(user_id,username,first_name,last_name,is_bot,collected_at,"
    "source_group,deleted,access_hash) VALUES(?,?,?,?,?,?,?,?,?)"
)

# Строка members в том виде, в каком её отдают план и dm_targets: тот же набор
# полей и тот же доступ по ключу, что у sqlite3.Row из SELECT m.*.
_MEMBER_COLUMNS = ("user_id", "username", "first_name", "last_name", "is_bot",
                   "collected_at", "source_group", "deleted", "access_hash", "dm_block")

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


def register_media(conn, account, msg_id, path_str, kind, title, mime, size):
    cur = conn.execute(
        "INSERT INTO media(account,msg_id,kind,title,mime,size,path,added_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (account, msg_id, kind, title, mime or "", size, path_str, now_iso()))
    conn.commit()
    return cur.lastrowid
