"""Агрегаты и отчёты по отправкам: дневные лимиты, здоровье аккаунта, эффективность.

Только SELECT-запросы и чистые свёртки; коммитов нет. Отчётные функции принимают
`now=` для детерминизма в тестах. См. ADR-0001, стадия 2c.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tguserbot.budget import COHORT_RU, account_cohort, cohort_budget
from tguserbot.repo import account_age_days

__all__ = [
    "account_dm_cap", "account_health", "campaign_report", "campaign_stats",
    "daily_cap", "delivery_report", "experiment_verdict", "message_id",
    "recent_ok_targets", "source_report", "today_broadcast_sent", "today_dm_sent",
    "today_sent",
]


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


def today_sent(conn, account, kind, now=None):
    """Успешные отправки аккаунта за текущие UTC-сутки (с полуночи).

    now= — для тестов и детерминированных отчётов; по умолчанию стена часов.
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    day_start = stamp[:10] + "T00:00:00"
    return conn.execute(
        "SELECT COUNT(*) AS n FROM sent s JOIN campaigns camp ON camp.id=s.campaign "
        "WHERE s.account=? AND s.status='ok' AND camp.kind=? AND s.ts>=?",
        (account, kind, day_start)).fetchone()["n"]


def today_dm_sent(conn, account, now=None):
    return today_sent(conn, account, "dm", now=now)


def today_broadcast_sent(conn, account, now=None):
    return today_sent(conn, account, "broadcast", now=now)


def daily_cap(conn, account, kind):
    col = "daily_budget" if kind == "dm" else "broadcast_budget"
    bud = conn.execute(f"SELECT {col} AS c FROM accounts WHERE name=?", (account,)).fetchone()  # nosec B608
    if kind == "dm":
        return account_dm_cap(conn, account, bud["c"] if bud else None)
    if not bud or bud["c"] is None:
        return 15
    return bud["c"]


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


def recent_ok_targets(conn, kind, hours=24, now=None):
    """Цели, которым успешно писали этого вида за последние N часов (все аккаунты)."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    return {str(r["target"]) for r in conn.execute(
        "SELECT DISTINCT s.target FROM sent s JOIN campaigns c ON c.id=s.campaign "
        "WHERE c.kind=? AND s.status='ok' AND s.ts>=?", (kind, cutoff))}


def campaign_stats(conn, hours=168, now=None):
    """Итог за период по видам: ok / fail / skip / total. Основа для оценки."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT c.kind AS kind,"
        " SUM(CASE WHEN s.status='ok' THEN 1 ELSE 0 END) AS ok,"
        " SUM(CASE WHEN s.status LIKE 'fail:%' THEN 1 ELSE 0 END) AS fail,"
        " SUM(CASE WHEN s.status LIKE 'skip:%' THEN 1 ELSE 0 END) AS skip,"
        " COUNT(*) AS total"
        " FROM sent s JOIN campaigns c ON c.id=s.campaign"
        " WHERE s.ts>=? GROUP BY c.kind ORDER BY c.kind", (cutoff,)).fetchall()
    return [dict(row) for row in rows]


def _campaign_ok_count(conn, camp):
    if camp is None:
        return 0
    row = conn.execute("SELECT COUNT(*) c FROM sent WHERE campaign=? AND status='ok'",
                       (camp,)).fetchone()
    return int(row["c"]) if row else 0


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
