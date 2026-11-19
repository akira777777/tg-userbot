import "server-only";

import path from "node:path";
import { spawn } from "node:child_process";

/**
 * Локальный исполнитель: запускает bot.py с тем же allowlist argv, что и
 * Python-воркер (control/cli_argv.py), и пишет результат в локальную очередь.
 * Только для локального режима панели.
 */

const MAX_OUTPUT = 4000;

/** argv для bot.py по тем же правилам, что build_control_cli в control/cli_argv.py. */
export function buildLocalArgv(kind, mode, account, payload = {}) {
  const data = payload || {};
  const dry = mode !== "LIVE";
  const args = [];
  switch (kind) {
    case "STATUS":
      return ["status"];
    case "GROUPS":
      return ["groups", "--list", "--account", account];
    case "CAMPAIGNS":
      return ["report"];
    case "MEDIA_LIST":
      return ["media", "list", "--account", account];
    case "BLACKLIST_ADD": {
      const value = String(data.value || "").trim();
      if (!value) return null;
      const reason = String(data.reason || "вручную");
      args.push("blacklist", "add");
      if (value.startsWith("-")) {
        pushOpt(args, "--reason", reason);
        args.push("--", value);
      } else {
        args.push(value);
        pushOpt(args, "--reason", reason);
      }
      return args;
    }
    case "STOP": {
      const target = data.account || account;
      if (target) args.push("stop", "--account", String(target));
      else args.push("stop");
      return args;
    }
    case "BROADCAST": {
      args.push("broadcast", "--account", account);
      pushDelivery(args, data);
      if (data.repeat_ok) args.push("--repeat-ok");
      if (dry) args.push("--dry-run");
      return args;
    }
    case "DMSEND": {
      args.push("dmsend", "--account", account, "--limit", String(data.limit || 20));
      pushDelivery(args, data);
      if (data.group_id) args.push("--group", String(data.group_id));
      if (data.repeat_ok) args.push("--repeat-ok");
      if (dry) args.push("--dry-run");
      return args;
    }
    case "CONDUCT": {
      if (!["broadcast", "dm", "both"].includes(data.kind)) return null;
      args.push("conduct", "--what", data.kind);
      pushDelivery(args, data);
      if (data.group_id) args.push("--group", String(data.group_id));
      if (data.limit) args.push("--limit", String(data.limit));
      if (data.window_hours && data.kind !== "both") args.push("--window", String(data.window_hours));
      if (data.repeat_ok) args.push("--repeat-ok");
      if (account && account !== "all") args.push("--accounts", account);
      if (dry) args.push("--dry-run");
      return args;
    }
    default:
      return null;
  }
}

function pushDelivery(args, data) {
  if (data.text) pushOpt(args, "--text", String(data.text));
  if (data.media_id) pushOpt(args, "--media", String(data.media_id));
  if (Array.isArray(data.group_ids) && data.group_ids.length) {
    args.push("--ids", ...data.group_ids.map((id) => String(id)));
  }
  if (data.delay_min) args.push("--delay-min", String(data.delay_min));
  if (data.delay_max) args.push("--delay-max", String(data.delay_max));
  if (data.max_runs) args.push("--max-runs", String(data.max_runs));
}

/** Значения на '-' передаются через '=' (как _opt в control/cli_argv.py). */
function pushOpt(args, flag, value) {
  if (value.startsWith("-")) args.push(`${flag}=${value}`);
  else args.push(flag, value);
}

function pythonCommand() {
  return process.env.LOCAL_PYTHON?.trim() || "python";
}

/** Активные процессы по commandId — для отмены. */
const running = new Map();

export function localRunCommand({ id, kind, mode, account, payload }) {
  const argv = buildLocalArgv(kind, mode, account, payload);
  if (!argv) {
    return Promise.resolve({ ok: false, output: `неподдерживаемая команда: ${kind}`, errorCode: "failed" });
  }
  const root = path.resolve(process.cwd(), "..");
  const proc = spawn(pythonCommand(), ["bot.py", ...argv], {
    cwd: root,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  running.set(id, proc);

  let stdout = "";
  let stderr = "";
  proc.stdout.on("data", (chunk) => {
    stdout += chunk.toString("utf8");
  });
  proc.stderr.on("data", (chunk) => {
    stderr += chunk.toString("utf8");
  });

  return new Promise((resolve) => {
    proc.on("error", (error) => {
      running.delete(id);
      resolve({ ok: false, output: String(error?.message || error), errorCode: "failed" });
    });
    proc.on("close", (code) => {
      running.delete(id);
      const tail = (stdout + (stderr ? `\n${stderr}` : "")).slice(-MAX_OUTPUT);
      resolve({
        ok: code === 0,
        output: tail,
        errorCode: code === 0 ? null : classifyLocalError(stderr || stdout, code),
      });
    });
  });
}

export function localCancelRun(id) {
  const proc = running.get(id);
  if (!proc) return false;
  try {
    proc.kill();
  } catch {
    return false;
  }
  return true;
}

function classifyLocalError(text, code) {
  const lowered = String(text || "").toLowerCase();
  const checks = [
    ["floodwait", "flood_wait"],
    ["flood_wait", "flood_wait"],
    ["database is locked", "db_locked"],
    ["session file is locked", "session_locked"],
    ["the process cannot access the file", "session_locked"],
    ["unauthorized", "unauthorized"],
    ["api_id", "auth_config"],
    ["api_hash", "auth_config"],
  ];
  for (const [needle, label] of checks) {
    if (lowered.includes(needle)) return label;
  }
  return code != null && code !== 0 ? `exit_${code}` : "failed";
}
