"""Общий allowlist argv и ключей слота для панели и Mini App worker."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from control.models import CommandKind, CommandMode

ACCOUNT_RE = re.compile(r"^[a-z0-9_\-]{1,32}$")
NAME_RE = re.compile(r"^[a-z0-9_\-]{1,32}$")
BLACKLIST_RE = re.compile(r"@?[A-Za-z0-9_]{2,64}")
MAX_TEXT = 3000
SESSION_ACTIONS = {
    "sync",
    "scrape",
    "watch",
    "broadcast",
    "dmsend",
    "probe",
    "privacy",
    "align",
}
SESSION_KINDS = frozenset({CommandKind.BROADCAST, CommandKind.DMSEND})


@dataclass(frozen=True, slots=True)
class CliSpec:
    argv: list[str]
    slot_key: str
    account: str


def slot_key_for_kind(kind: CommandKind, account: str) -> str:
    if kind in SESSION_KINDS:
        return account
    if kind == CommandKind.CONDUCT:
        return "conductor"
    return "db"


def slot_key_for_action(action: str | None, account: str) -> str:
    if action in SESSION_ACTIONS:
        return account
    if action == "conduct":
        return "conductor"
    if action == "cloud":
        return "cloud"
    return "db"


def build_control_cli(
    kind: CommandKind,
    payload: Any,
    account: str,
    mode: CommandMode = CommandMode.DRY_RUN,
) -> CliSpec | None:
    data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else dict(payload or {})
    argv = _control_argv(kind, data, account, mode)
    if argv is None:
        return None
    return CliSpec(argv=argv, slot_key=slot_key_for_kind(kind, account), account=account)


def build_web_args(p: dict) -> tuple[list[str], str, str]:
    """Белый список действий панели -> (argv, slot_key, account)."""
    action = p.get("action")
    account = str(p.get("account") or "main")
    needs_account = action in SESSION_ACTIONS or (
        action == "media" and str(p.get("op")) in ("add", "pull", "sync")
    )
    if needs_account and not ACCOUNT_RE.fullmatch(account):
        raise ValueError("аккаунт: только a-z 0-9 _ - (до 32)")
    slot = slot_key_for_action(action, account)
    tail = ["--account", account] if action in SESSION_ACTIONS else []
    return _web_argv(p, action, account, slot, tail)


def _control_argv(kind: CommandKind, data: dict, account: str, mode: CommandMode) -> list[str] | None:
    if kind == CommandKind.STATUS:
        return ["status"]
    if kind == CommandKind.GROUPS:
        return ["groups", "--list", "--account", account]
    if kind == CommandKind.CAMPAIGNS:
        return ["status"]
    if kind == CommandKind.MEDIA_LIST:
        return ["media", "list", "--account", account]
    if kind == CommandKind.BLACKLIST_ADD:
        value = str(data.get("value") or "").strip()
        if not value:
            return None
        return ["blacklist", "add", value, "--reason", str(data.get("reason") or "вручную")]
    if kind == CommandKind.STOP:
        args = ["stop"]
        target = data.get("account") or account
        if target:
            args += ["--account", str(target)]
        return args
    if kind == CommandKind.BROADCAST:
        args = ["broadcast", "--account", account]
        _append_delivery_args(args, data)
        if data.get("repeat_ok"):
            args += ["--repeat-ok"]
        return _with_mode(args, mode)
    if kind == CommandKind.DMSEND:
        args = ["dmsend", "--account", account, "--limit", str(data.get("limit") or 20)]
        _append_delivery_args(args, data)
        if data.get("group_id"):
            args += ["--group", str(data["group_id"])]
        if data.get("repeat_ok"):
            args += ["--repeat-ok"]
        return _with_mode(args, mode)
    if kind == CommandKind.CONDUCT:
        if data.get("kind") not in {"broadcast", "dm"}:
            return None
        args = ["conduct", "--what", data["kind"]]
        _append_delivery_args(args, data)
        if data.get("group_id"):
            args += ["--group", str(data["group_id"])]
        if data.get("limit"):
            args += ["--limit", str(data["limit"])]
        if data.get("window_hours"):
            args += ["--window", str(data["window_hours"])]
        if data.get("repeat_ok"):
            args += ["--repeat-ok"]
        if account and account != "all":
            args += ["--accounts", account]
        return _with_mode(args, mode)
    return None


def _append_delivery_args(args: list[str], data: dict) -> None:
    if data.get("text"):
        args += ["--text", str(data["text"])]
    if data.get("media_id"):
        args += ["--media", str(data["media_id"])]
    if data.get("group_ids"):
        args += ["--ids", *[str(group_id) for group_id in data["group_ids"]]]
    if data.get("delay_min"):
        args += ["--delay-min", str(data["delay_min"])]
    if data.get("delay_max"):
        args += ["--delay-max", str(data["delay_max"])]
    if data.get("max_runs"):
        args += ["--max-runs", str(data["max_runs"])]


def _with_mode(args: list[str], mode: CommandMode) -> list[str]:
    if mode != CommandMode.LIVE:
        args += ["--dry-run"]
    return args


def _web_argv(p: dict, action, account: str, slot: str, tail: list[str]) -> tuple[list[str], str, str]:
    if action == "media":
        return _web_media(p, account)
    builders = {
        "sync": lambda: (["groups", "--sync"] + tail, slot, account),
        "export": lambda: (["export"], slot, account),
        "watch": lambda: (["watch"] + tail, slot, account),
        "status": lambda: (["status"], slot, account),
        "scrape": lambda: _web_scrape(p, tail, slot, account),
        "probe": lambda: _web_probe(p, tail, slot, account),
        "privacy": lambda: _web_privacy(p, tail, slot, account),
        "align": lambda: _web_align(p, tail, slot, account),
        "compare": lambda: _web_compare(p, tail, slot, account),
        "cloud": lambda: _web_cloud(p),
        "accounts": lambda: (_web_accounts(p), slot, account),
        "blacklist_add": lambda: (_web_blacklist(p), slot, account),
        "broadcast": lambda: _web_delivery(p, "broadcast", tail, slot, account),
        "dmsend": lambda: _web_delivery(p, "dmsend", tail, slot, account),
        "conduct": lambda: _web_conduct(p),
    }
    builder = builders.get(action)
    if builder is None:
        raise ValueError(f"неизвестное действие: {action}")
    return builder()


def _web_media(p: dict, account: str) -> tuple[list[str], str, str]:
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


def _web_scrape(p: dict, tail: list[str], slot: str, account: str) -> tuple[list[str], str, str]:
    ids = [str(int(i)) for i in (p.get("ids") or [])][:200]
    return ["scrape"] + (["--groups"] + ids if ids else []) + tail, slot, account


def _web_probe(p: dict, tail: list[str], slot: str, account: str) -> tuple[list[str], str, str]:
    args = ["groups", "--probe"] + tail
    if p.get("keep"):
        args.append("--keep")
    return args, slot, account


def _web_privacy(p: dict, tail: list[str], slot: str, account: str) -> tuple[list[str], str, str]:
    privacy_slot = "conductor" if p.get("all") else slot
    return ["privacy"] + (["--all"] if p.get("all") else tail), privacy_slot, account


def _web_align(p: dict, tail: list[str], slot: str, account: str) -> tuple[list[str], str, str]:
    args = ["align"] + tail
    if p.get("limit"):
        args += ["--limit", str(min(50, int(p["limit"])))]
    if p.get("dry"):
        args.append("--dry-run")
    return args, slot, account


def _web_compare(p: dict, tail: list[str], slot: str, account: str) -> tuple[list[str], str, str]:
    other = str(p.get("other") or "main")
    if not ACCOUNT_RE.fullmatch(other):
        raise ValueError("compare: некорректное имя второго аккаунта")
    return ["compare", "--other", other] + tail, slot, account


def _web_cloud(p: dict) -> tuple[list[str], str, str]:
    op = str(p.get("op") or "push")
    if op not in {"push", "status", "test"}:
        raise ValueError("cloud: push|status|test")
    return ["cloud", op], "cloud", "cloud"


def _web_accounts(p: dict) -> list[str]:
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
    if op == "budget" and (not val.isdigit() or int(val) > 500):
        raise ValueError("budget: число 0..500")
    return argv


def _web_blacklist(p: dict) -> list[str]:
    v = str(p.get("value", "")).strip()
    if not BLACKLIST_RE.fullmatch(v):
        raise ValueError("blacklist: только @ник (A-Za-z0-9_) или числовой id")
    reason = str(p.get("reason", "web")).strip()[:64]
    if reason.startswith("-"):
        reason = " " + reason
    return ["blacklist", "add", v, "--reason", reason or "web"]


def _web_delivery(p: dict, action: str, tail: list[str], slot: str, account: str) -> tuple[list[str], str, str]:
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
    lo, hi = _delays(p)
    if hi > 0:
        args += ["--delay-min", str(lo), "--delay-max", str(hi)]
    if p.get("max_runs"):
        args += ["--max-runs", str(int(p["max_runs"]))]
    if action == "dmsend":
        if p.get("group"):
            args += ["--group", str(int(p["group"]))]
        args += ["--limit", str(max(1, int(p.get("limit", 20) or 20)))]
    else:
        ids = [str(int(i)) for i in (p.get("ids") or [])][:200]
        if ids:
            args += ["--ids"] + ids
    if p.get("repeat_ok"):
        args.append("--repeat-ok")
    return args + tail, slot, account


def _web_conduct(p: dict) -> tuple[list[str], str, str]:
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
    lo, hi = _delays(p)
    if hi > 0:
        args += ["--delay-min", str(lo), "--delay-max", str(hi)]
    return args, "conductor", "conductor"


def _delays(p: dict) -> tuple[float, float]:
    try:
        lo = max(0.0, float(p.get("delay_min", 0) or 0))
        hi = max(0.0, float(p.get("delay_max", 0) or 0))
    except (TypeError, ValueError) as error:
        raise ValueError("некорректные задержки") from error
    return lo, hi
