"""Orchestra-скрипт раньше жил вне pytest — теперь один прогон."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_orchestra_script():
    script = ROOT / "test_orchestra.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ALL ORCHESTRA TESTS OK" in proc.stdout
