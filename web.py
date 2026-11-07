#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Веб-панель управления tg-userbot (оркестр мультиаккаунтов) + живой лог.

    python web.py            # http://127.0.0.1:8642

Запускать интерпретатором из .venv. Слушает только 127.0.0.1.
Правило оркестра: одна одновременная задача НА АККАУНТ ( Telethon:
один session-файл = один клиент). Разные аккаунты работают параллельно.
DB-задачи (export/status/blacklist/accounts) идут в отдельный слот "db".
"""
import asyncio
import collections
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("USERBOT_DB", ROOT / "userbot.db"))
MEDIA_DIR = ROOT / "media_cache"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "web.log"
HOST = "127.0.0.1"
PORT = int(os.environ.get("WEBBOT_PORT", "8642"))
PY = sys.executable
MAX_TEXT = 3000
BOOT = uuid.uuid4().hex[:8]
BLACKLIST_RE = re.compile(r"@?[A-Za-z0-9_]{2,64}")
ACCOUNT_RE = re.compile(r"^[a-z0-9_\-]{1,32}$")
NAME_RE = re.compile(r"^[a-z0-9_\-]{1,32}$")
SESSION_ACTIONS = {"sync", "scrape", "watch", "broadcast", "dmsend", "probe", "privacy", "align"}
FANOUT_ACTIONS = {"sync", "scrape", "watch", "probe", "privacy", "align"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Bus:
    """Шина логов + словарь задач BUS.jobs (ключ = аккаунт или 'db')."""

    QUEUE_MAX = 2000

    def __init__(self):
        self.subs: set = set()
        self.buffer = collections.deque(maxlen=1500)
        self.jobs: dict = {}
        self.seq = 0

    def publish(self, ev: dict):
        self.seq += 1
        ev = {"seq": self.seq, "boot": BOOT,
              "ts": time.strftime("%H:%M:%S", time.gmtime()), **ev}
        self.buffer.append(ev)
        for q in list(self.subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                self.subs.discard(q)
        try:
            LOG_DIR.mkdir(exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{ev['ts']} [{ev.get('account','web')}] {ev.get('type','')} "
                        f"{ev.get('text','')}\n")
        except OSError:
            pass

    @staticmethod
    def public_job(job):
        if not job:
            return None
        j = dict(job)
        j.pop("proc", None)
        j.pop("kill", None)
        return j

    async def subscribe(self):
        q = asyncio.Queue(maxsize=self.QUEUE_MAX)
        self.subs.add(q)
        return q

    def unsubscribe(self, q):
        self.subs.discard(q)


BUS = Bus()
RUN_LOCK = asyncio.Lock()   # reserve: проверка+занятие слота атомарны


# ---------------------------------------------------------------- команды ---

def build_args(p: dict):
    """Белый список действий -> (argv, slot_key, account). ValueError при ошибке."""
    action = p.get("action")
    account = str(p.get("account") or "main")
    if (action in SESSION_ACTIONS
            or (action == "media" and str(p.get("op")) in ("add", "pull", "sync"))) \
            and not ACCOUNT_RE.fullmatch(account):
        raise ValueError("аккаунт: только a-z 0-9 _ - (до 32)")
    slot = account if action in SESSION_ACTIONS else "db"
    tail = ["--account", account] if action in SESSION_ACTIONS else []

    if action == "media":
        op = str(p.get("op") or "list")
        if op not in {"add", "pull", "sync", "del", "list"}:
            raise ValueError("media: add|pull|sync|del|list")
        val = str(p.get("value") or "").strip()
        atail = ["--account", account]
        if op == "add":
            if not val or len(val) > 400 or any(c in val for c in "\r\n"):
                raise ValueError("media add: корректный путь до файла")
            return ["media", "add", val] + atail, account, account
        if op == "pull":
            lim = min(200, max(5, int(p.get("limit") or 30)))
            return ["media", "pull", "--limit", str(lim)] + atail, account, account
        if op == "sync":
            if not val.isdigit():
                raise ValueError("media sync: нужен id")
            return ["media", "sync", val] + atail, account, account
        if op == "del":
            if not val.isdigit():
                raise ValueError("media del: нужен id")
            return ["media", "del", val], "db", account
        return ["media", "list"], "db", account
    if action == "sync":
        return ["groups", "--sync"] + tail, slot, account
    if action == "export":
        return ["export"], slot, account
    if action == "watch":
        return ["watch"] + tail, slot, account
    if action == "status":
        return ["status"], slot, account
    if action == "scrape":
        ids = [str(int(i)) for i in (p.get("ids") or [])][:200]
        return ["scrape"] + (["--groups"] + ids if ids else []) + tail, slot, account
    if action == "probe":
        args = ["groups", "--probe"] + tail
        if p.get("keep"):
            args.append("--keep")
        return args, slot, account
    if action == "privacy":
        return ["privacy"] + (["--all"] if p.get("all") else tail), \
            ("conductor" if p.get("all") else slot), account
    if action == "align":
        args = ["align"] + tail
        if p.get("limit"):
            args += ["--limit", str(min(50, int(p["limit"])))]
        if p.get("dry"):
            args.append("--dry-run")
        return args, slot, account
    if action == "compare":
        other = str(p.get("other") or "main")
        if not ACCOUNT_RE.fullmatch(other):
            raise ValueError("compare: некорректное имя второго аккаунта")
        return ["compare", "--other", other] + tail, slot, account
    if action == "accounts":
        op = str(p.get("op", "list"))
        if op not in {"list", "enable", "disable", "budget", "import", "remove"}:
            raise ValueError("accounts: неизвестная операция")
        argv = ["accounts", op]
        name = str(p.get("name") or "")
        if name:
            if not NAME_RE.fullmatch(name):
                raise ValueError("accounts: некорректное имя")
            argv.append(name)
        val = str(p.get("value") or "")
        if val:
            if len(val) > 200 or any(c in val for c in "\r\n"):
                raise ValueError("accounts: некорректное значение")
            argv.append(val)
        if op in ("enable", "disable", "budget", "import", "remove") and not name:
            raise ValueError("accounts: нужно имя")
        if op == "budget":
            if not val.isdigit() or int(val) > 500:
                raise ValueError("budget: число 0..500")
        return argv, slot, account
    if action == "blacklist_add":
        v = str(p.get("value", "")).strip()
        if not BLACKLIST_RE.fullmatch(v):
            raise ValueError("blacklist: только @ник (A-Za-z0-9_) или числовой id")
        reason = str(p.get("reason", "web")).strip()[:64]
        if reason.startswith("-"):
            reason = " " + reason
        return ["blacklist", "add", v, "--reason", reason or "web"], slot, account
    if action in ("broadcast", "dmsend"):
        text = str(p.get("text", "")).strip()[:MAX_TEXT]
        mid = str(p.get("media") or "")
        if not text and not mid.isdigit():
            raise ValueError("нужен текст или медиа (или и то, и другое)")
        args = [action]
        if text:
            args += ["--text", text]
        if mid.isdigit():
            args += ["--media", mid]
        if p.get("dry"):
            args.append("--dry-run")
        try:
            lo = max(0.0, float(p.get("delay_min", 0) or 0))
            hi = max(0.0, float(p.get("delay_max", 0) or 0))
        except (TypeError, ValueError):
            raise ValueError("некорректные задержки")
        if hi > 0:
            args += ["--delay-min", str(lo), "--delay-max", str(hi)]
        if p.get("max_runs"):
            args += ["--max-runs", str(int(p["max_runs"]))]
        if action == "dmsend":
            if p.get("group"):
                args += ["--group", str(int(p["group"]))]
            args += ["--limit", str(max(1, int(p.get("limit", 20) or 20)))]
            if p.get("repeat_ok"):
                args.append("--repeat-ok")
        else:
            ids = [str(int(i)) for i in (p.get("ids") or [])][:200]
            if ids:
                args += ["--ids"] + ids
            if p.get("repeat_ok"):
                args.append("--repeat-ok")
        return args + tail, slot, account
    if action == "conduct":
        kind = str(p.get("kind") or "")
        if kind not in ("dm", "broadcast"):
            raise ValueError("conduct: kind только dm|broadcast")
        text = str(p.get("text", "")).strip()[:MAX_TEXT]
        mid = str(p.get("media") or "")
        if not text and not mid.isdigit():
            raise ValueError("нужен текст или медиа (или и то, и другое)")
        args = ["conduct", "--what", kind]
        if text:
            args += ["--text", text]
        if mid.isdigit():
            args += ["--media", mid]
        if p.get("dry"):
            args.append("--dry-run")
        if p.get("limit"):
            args += ["--limit", str(int(p["limit"]))]
        if kind == "dm" and p.get("group"):
            args += ["--group", str(int(p["group"]))]
        if kind == "broadcast" and p.get("ids"):
            args += ["--ids"] + [str(int(i)) for i in p["ids"][:200]]
        if p.get("repeat_ok"):
            args.append("--repeat-ok")
        if p.get("accounts"):
            accs = [str(a) for a in p["accounts"]]
            for a in accs:
                if not ACCOUNT_RE.fullmatch(a):
                    raise ValueError(f"conduct: некорректное имя аккаунта {a!r}")
            args += ["--accounts"] + accs
        if p.get("window"):
            w = float(p["window"])
            if not (0.1 <= w <= 24):
                raise ValueError("conduct: окно 0.1..24 ч")
            args += ["--window", str(w)]
        try:
            lo = max(0.0, float(p.get("delay_min", 0) or 0))
            hi = max(0.0, float(p.get("delay_max", 0) or 0))
        except (TypeError, ValueError):
            raise ValueError("некорректные задержки")
        if hi > 0:
            args += ["--delay-min", str(lo), "--delay-max", str(hi)]
        return args, "conductor", "conductor"
    raise ValueError(f"неизвестное действие: {action}")


def account_status(name):
    """(enabled, flood_active) аккаунта по реестру; неизвестный считаем включённым."""
    if not DB.exists():
        return True, False
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute("SELECT enabled,flood_until FROM accounts WHERE name=?", (name,)).fetchone()
        if not r:
            return True, False
        flooded = bool(r["flood_until"] and r["flood_until"] > now_iso())
        return bool(r["enabled"]), flooded
    finally:
        con.close()


async def _kill_tree(pid):
    try:
        p = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL)
        await p.wait()
        return True
    except (OSError, ValueError):
        return False


async def run_job(args, label, account, slot):
    cmd = [PY, str(ROOT / "bot.py")] + args
    BUS.publish({"type": "job", "account": account,
                 "text": f"▶ запуск: bot.py {label}", "job": label})
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        job = BUS.jobs.get(slot)
        if not job or job["name"] != label:  # слот уже перешел другой задаче — не мешаем
            proc.kill()
            return
        job.update({"pid": proc.pid, "proc": proc})
        if job.get("kill"):
            await _kill_tree(proc.pid)
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            BUS.publish({"type": "log", "account": account, "job": label,
                         "text": line.decode("utf-8", "replace").rstrip()})
        code = await proc.wait()
        BUS.publish({"type": "done", "account": account, "job": label,
                     "text": f"■ завершено ({label} @ {account}), код {code}"})
    except Exception as e:
        BUS.publish({"type": "done", "account": account, "job": label,
                     "text": f"■ сбой запуска ({type(e).__name__}: {e})"})
    finally:
        if BUS.jobs.get(slot) and BUS.jobs[slot]["name"] == label:
            BUS.jobs.pop(slot, None)


async def try_start(p: dict):
    """Валидация + занятие слота. Возвращает (status_code, payload)."""
    try:
        args, slot, account = build_args(p)
    except (ValueError, TypeError) as e:
        return 400, {"error": str(e)}
    label = str(p.get("action"))
    async with RUN_LOCK:
        if slot in BUS.jobs:
            return 409, {"error": f"занято: идёт «{BUS.jobs[slot]['name']}» ({slot})"}
        if slot not in ("db", "conductor"):
            enabled, flooded = account_status(account)
            if not enabled:
                return 409, {"error": f"аккаунт «{account}» отключён"}
            if flooded:
                return 423, {"error": f"аккаунт «{account}» в cooldown (FloodWait)"}
        BUS.jobs[slot] = {"name": label, "args": args, "account": account,
                          "pid": None, "started": now_iso(), "proc": None, "kill": False}
    asyncio.create_task(run_job(args, label, account, slot))
    return 200, {"ok": True, "args": args, "slot": slot}


# ---------------------------------------------------------------- endpoints ---

async def page_index(request):
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


def read_state():
    if not DB.exists():
        return {"error": f"БД не найдена ({DB}) — сначала login + groups --sync"}
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        groups = [dict(r) for r in con.execute(
            "SELECT g.id,g.title,g.username,g.gtype,g.in_broadcast, "
            "(SELECT COUNT(*) FROM member_sources s JOIN members m ON m.user_id=s.user_id "
            " WHERE s.group_id=g.id) AS members "
            "FROM groups g ORDER BY members DESC, g.title")]
        c = lambda q, p=(): con.execute(q, p).fetchone()[0]
        today = now_iso()[:10] + "T00:00:00"
        sent_today = {r["account"]: r["c"] for r in con.execute(
            "SELECT s.account, COUNT(*) c FROM sent s JOIN campaigns cm ON cm.id=s.campaign "
            "WHERE s.status='ok' AND cm.kind='dm' AND s.ts>=? GROUP BY s.account", (today,))}
        accounts = []
        for a in con.execute("SELECT name,enabled,user_id,daily_budget,flood_until,session,"
                             " (SELECT COUNT(*) FROM account_groups ag WHERE ag.account=a.name)"
                             " AS edge_groups FROM accounts a ORDER BY name"):
            accounts.append({
                "name": a["name"], "enabled": a["enabled"], "user_id": a["user_id"],
                "daily_budget": a["daily_budget"],
                "flood_until": a["flood_until"],
                "flooded": bool(a["flood_until"] and a["flood_until"] > now_iso()),
                "session": os.path.basename(a["session"] or ""),
                "dm_today": sent_today.get(a["name"], 0),
                "groups_covered": a["edge_groups"],
                "job": Bus.public_job(BUS.jobs.get(a["name"])),
            })
        media = []
        for m in con.execute("SELECT * FROM media ORDER BY id DESC LIMIT 120"):
            media.append({"id": m["id"], "kind": m["kind"], "title": m["title"],
                          "mime": m["mime"], "size": m["size"], "msg_id": m["msg_id"],
                          "ok": bool(m["path"] and os.path.exists(m["path"])),
                          "account": m["account"]})
        return {
            "db": str(DB), "now": now_iso(), "boot": BOOT,
            "jobs": {k: Bus.public_job(v) for k, v in BUS.jobs.items()},
            "accounts": accounts,
            "media": media,
            "groups": len(groups),
            "active_groups": c("SELECT COUNT(*) FROM groups WHERE in_broadcast=1"),
            "covered_groups": c("SELECT COUNT(*) FROM groups g WHERE EXISTS "
                                "(SELECT 1 FROM account_groups ag WHERE ag.group_id=g.id)"),
            "writable_groups": c("SELECT COUNT(*) FROM groups g WHERE EXISTS "
                                 "(SELECT 1 FROM account_groups ag "
                                 " WHERE ag.group_id=g.id AND ag.can_write=1)"),
            "members": c("SELECT COUNT(*) FROM members"),
            "bots": c("SELECT COUNT(*) FROM members WHERE is_bot=1"),
            "blacklist": c("SELECT COUNT(*) FROM blacklist"),
            "campaigns": c("SELECT COUNT(*) FROM campaigns"),
            "dm_available": c(
                "SELECT COUNT(*) FROM members m WHERE m.is_bot=0 "
                "AND m.user_id NOT IN (SELECT user_id FROM blacklist) "
                "AND m.user_id NOT IN (SELECT CAST(s.target AS INTEGER) FROM sent s "
                " JOIN campaigns c ON c.id=s.campaign WHERE c.kind='dm' AND s.status='ok')"),
            "group_rows": groups,
            "last_campaigns": [dict(r) for r in con.execute(
                "SELECT c.id,c.kind,c.account,c.text,substr(c.created_at,1,16) created_at, "
                "(SELECT COUNT(*) FROM sent s WHERE s.campaign=c.id AND s.status='ok') ok, "
                "(SELECT COUNT(*) FROM sent s WHERE s.campaign=c.id AND s.status!='ok') fail "
                "FROM campaigns c ORDER BY c.id DESC LIMIT 8")],
        }
    finally:
        con.close()


async def api_state(request):
    st = read_state()
    if "error" in st:
        return web.json_response(st, status=400)
    return web.json_response(st)


async def api_run(request):
    try:
        p = await request.json()
    except (json.JSONDecodeError, Exception) as e:
        return web.json_response({"error": f"ошибка запроса: {type(e).__name__}"}, status=400)
    code, payload = await try_start(p)
    return web.json_response(payload, status=code)


async def api_fanout(request):
    """Запустить действие сразу на всех включённых аккаунтах (по слоту на аккаунт)."""
    try:
        p = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    action = p.get("action")
    if action not in FANOUT_ACTIONS:
        return web.json_response({"error": f"fanout только для {sorted(FANOUT_ACTIONS)}"}, status=400)
    if not DB.exists():
        return web.json_response({"error": "нет БД"}, status=400)
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        names = [r["name"] for r in con.execute(
            "SELECT name FROM accounts WHERE enabled=1 ORDER BY name")]
    finally:
        con.close()
    results = {}
    for name in names:
        code, payload = await try_start({**p, "action": action, "account": name})
        results[name] = "ok" if code == 200 else payload.get("error", str(code))
    return web.json_response({"results": results})


async def api_stop(request):
    try:
        p = await request.json()
    except Exception:
        p = {}
    target = p.get("account")
    stopped = []
    if target == "all" or not target:
        slots = list(BUS.jobs.keys())
    else:
        slots = [target]
    for slot in slots:
        job = BUS.jobs.get(slot)
        if not job:
            continue
        proc = job.get("proc")
        if proc is None:
            job["kill"] = True
            stopped.append(slot + " (отложено до старта)")
            continue
        if await _kill_tree(job["pid"]):
            stopped.append(slot)
    if stopped:
        BUS.publish({"type": "job", "account": "*",
                     "text": "⏹ остановка: " + ", ".join(stopped)})
    return web.json_response({"ok": bool(stopped), "stopped": stopped})


async def api_media_file(request):
    """Раздача кэшированного медиа с поддержкой Range — видео.play() работает
    как в обычном веб-плеере (перемотка без докачки всего файла)."""
    try:
        mid = int(request.match_info["mid"])
    except ValueError:
        return web.json_response({"error": "id должен быть числом"}, status=400)
    if not DB.exists():
        return web.json_response({"error": "нет БД"}, status=400)
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone()
    finally:
        con.close()
    if not row:
        return web.json_response({"error": f"медиа #{mid} нет"}, status=404)
    p = Path(row["path"] or "")
    try:
        rp = p.resolve()
        if MEDIA_DIR.resolve() not in rp.parents:
            return web.json_response({"error": "путь вне media_cache"}, status=400)
    except OSError:
        return web.json_response({"error": "путь не читается"}, status=400)
    if not rp.exists():
        return web.json_response(
            {"error": f"файл не в кэше — выполни media sync {mid} --account {row['account']}"},
            status=409)
    return web.FileResponse(str(rp), headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(os.path.basename(str(rp)))}",
        "Accept-Ranges": "bytes"})


async def api_media_upload(request):
    """multipart upload -> tmp в media_cache -> media add (в «Избранное» + кэш)."""
    data = await request.post()
    up = data.get("file")
    if up is None or not hasattr(up, "file"):
        return web.json_response({"error": "нужно поле 'file' (multipart)"}, status=400)
    account = str(data.get("account") or "main")
    if not ACCOUNT_RE.fullmatch(account):
        return web.json_response({"error": "аккаунт?"}, status=400)
    name = os.path.basename(str(up.filename or "upload.bin"))
    name = re.sub(r"[^A-Za-z0-9._\-]+", "_", name)[:80] or "upload.bin"
    MEDIA_DIR.mkdir(exist_ok=True)
    tmp = MEDIA_DIR / f"upload_{int(time.time()*1000)}_{name}"
    try:
        with open(tmp, "wb") as fh:
            while True:
                chunk = up.file.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
    except OSError as e:
        return web.json_response({"error": f"запись: {e}"}, status=500)
    finally:
        try:
            up.file.close()
        except Exception:
            pass
    code, payload = await try_start({"action": "media", "op": "add",
                                     "value": str(tmp), "account": account})
    if code != 200:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return web.json_response(payload, status=code)


async def api_stream(request):
    resp = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    q = await BUS.subscribe()
    try:
        for ev in list(BUS.buffer):
            await resp.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=20)
            except asyncio.TimeoutError:
                await resp.write(b": keep-alive\n\n")
                if q not in BUS.subs:
                    break
                continue
            await resp.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        BUS.unsubscribe(q)
    return resp


def main():
    # косметика CPython 3.14/Windows: при разрыве SSE-соединений proactor-цикл
    # печатает безобидный ConnectionResetError в exception handler — глушим только его
    import logging
    class _NoiseFilter(logging.Filter):
        def filter(self, rec):
            exc = rec.exc_info[1] if rec.exc_info and len(rec.exc_info) > 1 else None
            is_rst = type(exc).__name__ == "ConnectionResetError" if exc else \
                     "ConnectionResetError" in rec.getMessage()
            return not (is_rst and "_call_connection_lost" in str(rec.getMessage()))
    logging.getLogger("asyncio").addFilter(_NoiseFilter())

    app = web.Application(client_max_size=400 * 1024 * 1024)  # до 400 МБ на загрузку
    app.router.add_get("/", page_index)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/run", api_run)
    app.router.add_post("/api/fanout", api_fanout)
    app.router.add_post("/api/stop", api_stop)
    app.router.add_get("/api/media/{mid}/file", api_media_file)
    app.router.add_post("/api/media/upload", api_media_upload)
    app.router.add_get("/api/stream", api_stream)
    print(f"Панель: http://{HOST}:{PORT}  (Ctrl+C — остановить)")
    web.run_app(app, host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
