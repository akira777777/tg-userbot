from pathlib import Path
import os
p = Path(os.environ["TEMP"]) / "ours-bot.py"
text = p.read_text(encoding="utf-8").splitlines()
needles = (
    "SECRETS_PATH",
    "STOP_PATH",
    "def load_account_secrets",
    "def save_account_secrets",
    "def persist_account_secret",
    "def migrate_plaintext_secret",
    "def stop_requested",
    "def request_stop",
    "def cmd_stop",
    "stop_requested(",
)
for i, line in enumerate(text, 1):
    if any(n in line for n in needles):
        print(f"{i}:{line}")
