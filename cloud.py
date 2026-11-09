"""Облачное зеркало tg-userbot -> Supabase (PostgREST, без зависимостей).

Зеркалит ТОЛЬКО агрегаты: аккаунты, список групп и их покрытие/права,
кампании и журнал статусов, blacklist, общие счётчики. Собранные ники
людей (members) в облако НЕ уходят — это личная data.

Секреты в .env: SUPABASE_URL, SUPABASE_SECRET_KEY (или SUPABASE_SERVICE_ROLE_KEY).
Запуск:  cloud.py push | status | test
"""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("USERBOT_DB", ROOT / "userbot.db"))
MARKER = ROOT / ".cloud_last"
TABLES = ["tb_accounts", "tb_groups", "tb_campaigns", "tb_sent",
          "tb_blacklist", "tb_stats"]
BATCH = 500
SENT_LIMIT = 20000


def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""


def api(method, table, params="", body=None, prefer=None):
    if not URL or not KEY:
        raise SystemExit("SUPABASE_URL / SUPABASE_SECRET_KEY не заданы в .env")
    req = urllib.request.Request(
        f"{URL}/rest/v1/{table}{params}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json",
                 "Prefer": prefer or "resolution=merge-duplicates"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300], dict(e.headers)
    except OSError as e:
        return 0, f"сеть: {e}", {}


def connect_local():
    if not DB.exists():
        raise SystemExit(f"локальная БД не найдена: {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    return con


def _rows_by(con, sql, key, params=()):
    return {row[key]: row for row in con.execute(sql, params)}


def gather(con):
    """sqlite -> payload'ы для 6 таблиц зеркала."""
    now = datetime.now(timezone.utc)
    today = now.isoformat(timespec="seconds")[:10]
    synced = now.isoformat(timespec="seconds")
    out = {}

    coverage = _rows_by(
        con,
        "SELECT account, COUNT(*) covered, "
        "COALESCE(SUM(CASE WHEN can_write=1 THEN 1 ELSE 0 END), 0) writable "
        "FROM account_groups GROUP BY account",
        "account",
    )
    dm_by_account = _rows_by(
        con,
        "SELECT s.account account, COUNT(*) c FROM sent s "
        "JOIN campaigns c ON c.id=s.campaign "
        "WHERE c.kind='dm' AND s.status='ok' AND substr(s.ts,1,10)=? "
        "GROUP BY s.account",
        "account",
        (today,),
    )
    accs = []
    for a in con.execute("SELECT * FROM accounts"):
        name = a["name"]
        cov = coverage.get(name)
        accs.append({"name": name, "user_id": a["user_id"], "api_id": a["api_id"],
                     "enabled": bool(a["enabled"]), "daily_budget": a["daily_budget"],
                     "flood_until": a["flood_until"], "note": a["note"],
                     "groups_covered": cov["covered"] if cov else 0,
                     "writable_groups": cov["writable"] if cov else 0,
                     "dm_today": dm_by_account[name]["c"] if name in dm_by_account else 0,
                     "synced_at": synced})
    out["tb_accounts"] = (accs, "name")

    group_stats = _rows_by(
        con,
        "SELECT group_id, COALESCE(SUM(can_write), 0) w, COUNT(*) t "
        "FROM account_groups GROUP BY group_id",
        "group_id",
    )
    collected_by_group = _rows_by(
        con,
        "SELECT group_id, COUNT(DISTINCT user_id) c FROM member_sources GROUP BY group_id",
        "group_id",
    )
    grp = []
    for g in con.execute("SELECT * FROM groups"):
        cw = group_stats.get(g["id"])
        writable = cw["w"] if cw else 0
        total = cw["t"] if cw else 0
        if writable and writable == total:
            agg = "yes"
        elif writable:
            agg = "mixed"
        else:
            agg = "no"
        collected = collected_by_group.get(g["id"])
        grp.append({"id": g["id"], "title": g["title"], "username": g["username"],
                    "gtype": g["gtype"], "in_broadcast": bool(g["in_broadcast"]),
                    "active": bool(g["active"]), "can_write_agg": agg,
                    "members_collected": collected["c"] if collected else 0,
                    "synced_at": synced})
    out["tb_groups"] = (grp, "id")

    camp_stats = _rows_by(
        con,
        "SELECT campaign, "
        "SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) ok, "
        "SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) fail "
        "FROM sent GROUP BY campaign",
        "campaign",
    )
    camps = []
    for c in con.execute("SELECT * FROM campaigns ORDER BY id DESC LIMIT 500"):
        stats = camp_stats.get(c["id"])
        camps.append({"id": c["id"], "kind": c["kind"], "account": c["account"],
                      "text": (c["text"] or "")[:500], "created_at": c["created_at"],
                      "ok": stats["ok"] if stats else 0, "fail": stats["fail"] if stats else 0,
                      "synced_at": synced})
    out["tb_campaigns"] = (camps, "id")

    sent = [{"campaign": s["campaign"], "target": s["target"], "account": s["account"],
             "status": s["status"], "ts": s["ts"]}
            for s in con.execute("SELECT * FROM sent ORDER BY ts DESC LIMIT ?",
                                 (SENT_LIMIT,))]
    out["tb_sent"] = (sent, "campaign,target,account")

    bl = [{"user_id": b["user_id"], "username": b["username"], "reason": b["reason"],
           "ts": b["ts"], "synced_at": now.isoformat(timespec="seconds")}
          for b in con.execute("SELECT * FROM blacklist")]
    out["tb_blacklist"] = (bl, "user_id")

    def one(q, *a):
        return con.execute(q, a).fetchone()[0] if a else con.execute(q).fetchone()[0]
    stats = [
        {"key": "members_total", "value": str(one("SELECT COUNT(*) FROM members"))},
        {"key": "groups_total", "value": str(one("SELECT COUNT(*) FROM groups"))},
        {"key": "groups_broadcast", "value": str(one("SELECT COUNT(*) FROM groups WHERE in_broadcast=1 AND active=1"))},
        {"key": "sent_ok_total", "value": str(one("SELECT COUNT(*) FROM sent WHERE status='ok'"))},
        {"key": "sent_fail_total", "value": str(one("SELECT COUNT(*) FROM sent WHERE status!='ok'"))},
        {"key": "blacklist_n", "value": str(one("SELECT COUNT(*) FROM blacklist"))},
        {"key": "media_n", "value": str(one("SELECT COUNT(*) FROM media"))},
        {"key": "db_bytes", "value": str(DB.stat().st_size)},
        {"key": "pushed_at", "value": now.isoformat(timespec="seconds")},
    ]
    for s in stats:
        s["synced_at"] = now.isoformat(timespec="seconds")
    out["tb_stats"] = (stats, "key")
    return out


def cmd_test():
    code, body, _ = api("GET", "tb_stats", "?limit=1")
    if code == 200:
        print("схема на месте, зеркало готово к push")
    elif code == 404:
        print("ключи валидны, но таблиц нет — выполни supabase/schema.sql в SQL Editor")
    else:
        print(f"проблемы с доступом: HTTP {code}: {body}")
        sys.exit(1)


def cmd_push():
    con = connect_local()
    data = gather(con)
    summary, had_err = {}, False
    for t in TABLES:
        rows, pkey = data[t]
        if not rows:
            summary[t] = 0
            continue
        pushed = 0
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            code, body, _ = api("POST", t, f"?on_conflict={pkey}", chunk)
            if code in (200, 201, 204):
                pushed += len(chunk)
            elif code == 404:
                print(f"{t}: таблицы нет в Supabase — выполни supabase/schema.sql в SQL Editor")
                had_err = True
                break
            else:
                print(f"{t}: HTTP {code}: {body}")
                had_err = True
                break
        summary[t] = pushed
        print(f"  {t:<14} {pushed:>6}")
    MARKER.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "rows": summary, "error": had_err}), encoding="utf-8")
    if had_err:
        sys.exit(1)
    print("push ok ->", URL.split("//")[-1])


def cmd_status():
    data = {}
    try:
        con = connect_local()
        data = gather(con)
    except SystemExit:
        pass
    print(f"{'table':<14} {'cloud':>8}  local")
    for t in TABLES:
        code, _, hdr = api("HEAD", t, "?select=*", prefer="count=exact")
        cr = hdr.get("Content-Range", "")
        rows = cr.split("/")[-1] if "/" in cr else ("-" if code else code)
        local = len(data[t][0]) if t in data else "-"
        print(f"{t:<14} {rows:>8}  {local}")


if __name__ == "__main__":
    op = sys.argv[1] if len(sys.argv) > 1 else "push"
    if op == "push":
        cmd_push()
    elif op == "test":
        cmd_test()
    elif op == "status":
        cmd_status()
    else:
        raise SystemExit("использование: cloud.py push|test|status")
