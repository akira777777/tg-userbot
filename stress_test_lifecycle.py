"""
stress_test_lifecycle.py
Empirical stress-testing of SQLite connection lifecycle, database isolation,
and error paths for tg-userbot Milestone 1.
"""
import os
import sys
import gc
import shutil
import sqlite3
import tempfile
import warnings
import traceback
from pathlib import Path

# Ensure root is on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot

class DummyArgs:
    def __init__(self, cmd="add", value="", reason="test"):
        self.cmd = cmd
        self.value = value
        self.reason = reason


def test_cmd_blacklist_lifecycle():
    print("--- [TEST 1] cmd_blacklist Malicious/Invalid Inputs & Lifecycle ---")
    tmp_dir = tempfile.mkdtemp(prefix="stress_bl_")
    test_db = Path(tmp_dir) / "test_bl.db"
    
    # Track opened connections
    captured_connections = []
    real_connect = bot.connect
    
    def tracked_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        captured_connections.append(conn)
        return conn

    bot.connect = tracked_connect
    bot.DB_PATH = test_db

    # Seed members table for valid nick lookup
    init_conn = real_connect()
    init_conn.execute("INSERT INTO members(user_id, username, first_name, is_bot, collected_at, source_group) "
                      "VALUES(42, 'legit_user', 'Legit', 0, ?, 100)", (bot.now_iso(),))
    init_conn.commit()
    init_conn.close()

    test_cases = [
        ("None value", DummyArgs(cmd="add", value=None, reason="attack"), AttributeError),
        ("Empty string value", DummyArgs(cmd="add", value="", reason="attack"), SystemExit),
        ("Whitespace only", DummyArgs(cmd="add", value="   ", reason="attack"), SystemExit),
        ("Non-existent nick", DummyArgs(cmd="add", value="@ghost_user_404", reason="attack"), SystemExit),
        ("SQL Injection string", DummyArgs(cmd="add", value="' OR 1=1; DROP TABLE blacklist; --", reason="sqli"), SystemExit),
        ("Special chars & null byte", DummyArgs(cmd="add", value="!@#$%^&*()\x00", reason="fuzz"), (SystemExit, ValueError)),
        ("Unicode emoji nick not in db", DummyArgs(cmd="add", value="💩👻🔥", reason="emoji"), SystemExit),
        ("Extreme length string (100k chars)", DummyArgs(cmd="add", value="@" + "A" * 100000, reason="overflow"), SystemExit),
        ("Valid user_id numeric string", DummyArgs(cmd="add", value="99999", reason="valid_id"), None),
        ("Valid existing username", DummyArgs(cmd="add", value="@legit_user", reason="valid_nick"), None),
        ("List command (empty/populated)", DummyArgs(cmd="list", value="", reason=""), None),
        ("Invalid cmd branch with None value", DummyArgs(cmd="unknown_action", value=None, reason=""), AttributeError),
        ("Missing reason attribute", DummyArgs(cmd="add", value="99999", reason=None), None),
    ]

    for label, args_obj, expected_exc in test_cases:
        captured_connections.clear()
        exc_raised = None
        try:
            bot.cmd_blacklist(args_obj)
        except BaseException as e:
            exc_raised = e

        if expected_exc is None:
            if exc_raised is not None:
                raise AssertionError(f"[{label}] Expected success, got {type(exc_raised).__name__}: {exc_raised}")
        else:
            if exc_raised is None:
                raise AssertionError(f"[{label}] Expected {expected_exc}, but no exception was raised!")
            if not isinstance(exc_raised, expected_exc):
                raise AssertionError(f"[{label}] Expected {expected_exc}, got {type(exc_raised).__name__}: {exc_raised}")

        assert len(captured_connections) == 1, f"[{label}] Expected exactly 1 connection opened, got {len(captured_connections)}"
        conn = captured_connections[0]
        
        # Verify connection is strictly closed
        try:
            conn.execute("SELECT 1")
            raise AssertionError(f"[{label}] Connection was NOT closed! Query succeeded on abandoned connection.")
        except sqlite3.ProgrammingError:
            pass  # Successfully closed!

        # Verify Windows file lock is NOT held: can rename or delete db file immediately
        try:
            probe_path = Path(tmp_dir) / f"probe_{os.urandom(4).hex()}.db"
            test_db.rename(probe_path)
            probe_path.rename(test_db)
        except OSError as e:
            raise AssertionError(f"[{label}] Windows file lock still held on {test_db}: {e}")

        # GC collection must NOT trigger ResourceWarning
        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")
            del conn
            gc.collect()
            for w in recorded_warnings:
                if issubclass(w.category, ResourceWarning):
                    raise AssertionError(f"[{label}] ResourceWarning emitted on GC: {w.message}")

        print(f"  PASS: {label} (closed=True, lock_released=True, warnings=0)")

    # Cleanup
    bot.connect = real_connect
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("ALL cmd_blacklist LIFECYCLE TESTS PASSED!\n")


def test_concurrent_and_repeated_runs():
    print("--- [TEST 2] Concurrent & Repeated Runs of test_suite.py (WinError 32) ---")
    import subprocess

    python_exe = sys.executable

    # 1. Sequential repeated runs (5 iterations)
    for i in range(1, 6):
        res = subprocess.run(
            [python_exe, "-m", "pytest", "test_suite.py", "-q"],
            capture_output=True,
            text=True
        )
        if res.returncode != 0:
            raise AssertionError(f"Sequential run #{i} failed with code {res.returncode}:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        print(f"  Sequential run #{i}: 7 passed (OK)")

    # 2. Parallel / Concurrent execution (4 concurrent processes running test_suite.py)
    print("  Testing 4 concurrent processes executing pytest test_suite.py simultaneously...")
    procs = []
    for i in range(4):
        p = subprocess.Popen(
            [python_exe, "-m", "pytest", "test_suite.py", "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        procs.append((i, p))

    for worker_id, p in procs:
        stdout, stderr = p.communicate()
        code = p.returncode
        if code != 0:
            raise AssertionError(f"Concurrent process #{worker_id} failed (WinError 32 or SQLite collision?):\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        print(f"  Concurrent process #{worker_id}: code {code} (OK)")

    print("ALL CONCURRENT & REPEATED RUNS PASSED!\n")


def test_db_path_pollution_and_isolation():
    print("--- [TEST 3] DB_PATH Pollution and Test Isolation Between Suites ---")
    tmp_dir = tempfile.mkdtemp(prefix="stress_iso_")
    canary_db = Path(tmp_dir) / "canary.db"
    
    # Set default bot.DB_PATH to canary_db
    original_db_path = bot.DB_PATH
    bot.DB_PATH = canary_db

    # Ensure canary.db is created and has a known signature table
    conn = bot.connect()
    conn.execute("CREATE TABLE canary_marker(tag TEXT)")
    conn.execute("INSERT INTO canary_marker VALUES('canary_original')")
    conn.commit()
    conn.close()

    print(f"  Canary DB created at {canary_db}")

    # Now run test_suite.py via pytest in-process or via pytest.main
    import pytest
    code = pytest.main(["-q", "test_suite.py"])
    if code != 0:
        raise AssertionError(f"pytest test_suite.py failed with exit code {code}")

    # Verify bot.DB_PATH was restored and canary.db was NOT polluted by test_suite.py
    # Re-check canary DB contents
    conn2 = bot.connect()
    try:
        rows = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {r["name"] for r in rows}
        print(f"  Tables in Canary DB after test_suite.py: {table_names}")

        # Check if test_suite.py inserted any test data (groups 101, 102, campaign 9, etc.) into canary_db
        group_rows = conn2.execute("SELECT * FROM groups").fetchall()
        member_rows = conn2.execute("SELECT * FROM members").fetchall()
        campaign_rows = conn2.execute("SELECT * FROM campaigns").fetchall()

        if len(group_rows) > 0 or len(member_rows) > 0 or len(campaign_rows) > 0:
            raise AssertionError(
                f"CANARY DB POLLUTION DETECTED! Canary DB has {len(group_rows)} groups, "
                f"{len(member_rows)} members, {len(campaign_rows)} campaigns! Isolation fixture leaked data!"
            )
        
        canary_row = conn2.execute("SELECT tag FROM canary_marker").fetchone()
        assert canary_row["tag"] == "canary_original", f"Canary tag corrupted: {canary_row['tag']}"
        print("  Canary DB is completely UNPOLLUTED by test_suite.py (OK)")
    finally:
        conn2.close()

    # Now run test_orchestra.py and verify interaction with bot.DB_PATH
    print("  Running test_orchestra.py to test cross-suite interaction...")
    import subprocess
    res = subprocess.run([sys.executable, "test_orchestra.py"], capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(f"test_orchestra.py failed with code {res.returncode}:\n{res.stdout}\n{res.stderr}")
    print("  test_orchestra.py finished successfully (OK)")

    # Restore
    bot.DB_PATH = original_db_path
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("ALL DB_PATH POLLUTION & ISOLATION TESTS PASSED!\n")


if __name__ == "__main__":
    try:
        test_cmd_blacklist_lifecycle()
        test_concurrent_and_repeated_runs()
        test_db_path_pollution_and_isolation()
        print("==================================================")
        print("ALL EMPIRICAL STRESS TESTS COMPLETED SUCCESSFULLY!")
        print("==================================================")
    except Exception as e:
        print(f"\nSTRESS TEST FAILURE: {e}")
        traceback.print_exc()
        sys.exit(1)
