import json, urllib.request, time, io, sys
B = "http://127.0.0.1:8642"

def post(path, obj):
    d = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(B + path, data=d,
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")

st, body = post("/api/run", {"action": "conduct", "kind": "broadcast",
                             "text": "ТЕСТ план оркестра — кириллица ок", "dry": True})
print("run:", st, body[:200])
# ждём завершения задачи conductor
for _ in range(40):
    time.sleep(2)
    with urllib.request.urlopen(B + "/api/state", timeout=10) as r:
        s = json.loads(r.read().decode("utf-8"))
    if not (s.get("jobs") or {}).get("conductor"):
        break
print("jobs после:", s.get("jobs"))
print("покрытие:", s.get("covered_groups"), "/", s.get("groups"),
      "| дирижёр слот свободен:", "conductor" not in (s.get("jobs") or {}))

st, body = post("/api/run", {"action": "conduct", "kind": "wrong"})
print("bad kind:", st, body[:80])
st, body = post("/api/run", {"action": "conduct", "kind": "dm", "text": "", "dry": True})
print("empty text:", st, body[:100])
st, body = post("/api/run", {"action": "conduct", "kind": "dm", "text": "x", "window": 99})
print("bad window:", st, body[:100])
st, body = post("/api/run", {"action": "conduct", "kind": "dm", "text": "x",
                             "dry": True, "limit": 8, "accounts": ["main"]})
print("dm dry run:", st, body[:160])
for _ in range(30):
    time.sleep(2)
    with urllib.request.urlopen(B + "/api/state", timeout=10) as r:
        s = json.loads(r.read().decode("utf-8"))
    if not (s.get("jobs") or {}).get("conductor"):
        break
print("done. jobs:", s.get("jobs"))
