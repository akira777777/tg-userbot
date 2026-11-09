from pathlib import Path

needed = [
    "BOT_TOKEN",
    "MINI_APP_URL",
    "OWNER_TELEGRAM_IDS",
    "CONTROL_SESSION_SECRET",
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
]
path = Path(".env")
if not path.exists():
    print("NO_ENV")
    raise SystemExit(0)
vals = {}
for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    vals[key.strip()] = value.strip().strip('"').strip("'")
for key in needed:
    value = vals.get(key, "")
    if not value:
        print(f"{key}: EMPTY")
        continue
    if key == "MINI_APP_URL":
        scheme = value.split(":", 1)[0]
        print(f"{key}: set scheme={scheme} len={len(value)}")
    elif key == "OWNER_TELEGRAM_IDS":
        parts = [item.strip() for item in value.split(",") if item.strip()]
        numeric = all(item.lstrip("-").isdigit() for item in parts)
        print(f"{key}: set count={len(parts)} numeric={numeric}")
    elif key == "CONTROL_SESSION_SECRET":
        defaultish = value in {
            "change-me-in-production",
            "replace_with_a_long_random_secret_at_least_32_chars_long",
        }
        print(f"{key}: set len={len(value)} defaultish={defaultish}")
    else:
        print(f"{key}: set len={len(value)}")
