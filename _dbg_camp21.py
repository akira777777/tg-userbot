import json
import sqlite3
import time
from pathlib import Path

import bot

db = Path(__file__).resolve().parent / "userbot.db"
log = Path(__file__).resolve().parent / ".cursor" / "debug-94ecd8.log"
conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
conn.row_factory = sqlite3.Row
ids = (
    1158822750, 1347412748, 1509827856, 1523713797, 1606877504,
    1653150086, 1662805983, 1722977233, 1725034269, 1837533449,
    1859895315, 1861708147, 1869904593, 2067009574,
)
sent = [dict(r) for r in conn.execute(
    "SELECT target, status, account FROM sent WHERE campaign=21 ORDER BY target")]
q = "SELECT id, in_broadcast, COALESCE(skip_reason,'') skip_reason FROM groups WHERE id IN (%s)" % ",".join("?" * len(ids))
flags = [dict(r) for r in conn.execute(q, ids)]
edges = [dict(r) for r in conn.execute(
    "SELECT account, group_id, can_write FROM account_groups WHERE group_id IN (%s) ORDER BY group_id, account" % ",".join("?" * len(ids)),
    ids)]
names = [
    "ChatRestrictedError", "ForbiddenError", "ChatSendPlainForbiddenError",
    "SlowModeWaitError", "BadRequestError", "TopicClosedError",
]
dispositions = {n: bot.target_disposition(n, "broadcast") for n in names}
reasons = {n: bot.permanent_reason(n, "broadcast") for n in names}
payload = {
    "sessionId": "94ecd8",
    "runId": "pre-fix",
    "hypothesisId": "C",
    "location": "userbot.db:campaign21",
    "message": "stored send status and broadcast flags",
    "data": {"sent": sent, "flags": flags, "edges": edges, "dispositions": dispositions, "reasons": reasons},
    "timestamp": int(time.time() * 1000),
}
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("a", encoding="utf-8") as f:
    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
print("sent", len(sent), "flags", len(flags))
