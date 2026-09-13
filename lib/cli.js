"use strict";

const path = require("path");
const paths = require("./paths");
const { loadEnv, envFileExists } = require("./env");
const {
  findPython,
  pythonVersionOk,
  ensureVenv,
  venvExists,
  depsOk,
  MIN_MAJOR,
  MIN_MINOR,
} = require("./venv");
const proc = require("./proc");

const HELP = `AgentRouter Direct Proxy (npm launcher)

Usage:
  agentrouter-proxy start  [-H host] [-p port] [--foreground]
  agentrouter-proxy stop   [-H host] [-p port]
  agentrouter-proxy restart [-H host] [-p port]
  agentrouter-proxy status [-H host] [-p port]
  agentrouter-proxy doctor
  agentrouter-proxy help

Environment:
  Read from process env, ./.env (cwd), then ~/.agentrouter-proxy/.env
  (first match wins). Required: AGENTROUTER_API_KEY.

Data directory:
  ${paths.getDataDir()}  (venv, logs, proxy.pid)
`;

function parseArgs(argv) {
  const opts = { host: null, port: null, foreground: false, help: false };
  const positional = [];

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];

    if (arg === "-H" || arg === "--host") {
      opts.host = argv[++i];
      if (!opts.host) {
        console.error("Missing value for --host");
        process.exit(2);
      }
    } else if (arg === "-p" || arg === "--port") {
      const value = parseInt(argv[++i], 10);
      if (!Number.isFinite(value) || value <= 0 || value > 65535) {
        console.error(`Invalid port: ${argv[i]}`);
        process.exit(2);
      }
      opts.port = value;
    } else if (arg === "-f" || arg === "--foreground") {
      opts.foreground = true;
    } else if (arg === "-h" || arg === "--help") {
      opts.help = true;
    } else {
      positional.push(arg);
    }
  }

  opts.command = positional[0] || "help";
  return opts;
}

function resolveHost(opts) {
  return (
    opts.host ||
    process.env.AGENTROUTER_PROXY_HOST ||
    "127.0.0.1"
  );
}

function resolvePort(opts, record) {
  if (opts.port) return opts.port;
  if (record && record.port) return record.port;

  const fromEnv = parseInt(process.env.AGENTROUTER_PROXY_PORT || "", 10);
  return Number.isFinite(fromEnv) && fromEnv > 0 ? fromEnv : 4020;
}

function banner(title) {
  console.log("");
  console.log("=========================================");
  console.log(` ${title}`);
  console.log("=========================================");
  console.log("");
}

async function cmdStart(opts) {
  loadEnv();

  const host = resolveHost(opts);
  const port = resolvePort(opts);

  banner("AgentRouter Direct Proxy (npm)");

  if (await proc.isPortOpen(host, port)) {
    console.log(`AgentRouter proxy already running at http://${host}:${port}/v1`);
    console.log("");
    return 0;
  }

  const python = findPython();

  if (!python) {
    console.error("No Python interpreter found (tried: python, python3, py -3).");
    console.error("Install Python 3.11+ and make sure it is on PATH.");
    return 1;
  }

  if (!pythonVersionOk(python)) {
    console.error(
      `Python ${python.version} found, but ${MIN_MAJOR}.${MIN_MINOR}+ is required.`
    );
    return 1;
  }

  try {
    ensureVenv(python, (msg) => console.log(msg));
  } catch (err) {
    console.error(String(err.message || err));
    return 1;
  }

  const apiKey = (process.env.AGENTROUTER_API_KEY || "").trim();

  if (!apiKey) {
    console.error("AGENTROUTER_API_KEY is not set.");
    console.error("");
    console.error("Create one of:");
    console.error(`  ${path.join(paths.getDataDir(), ".env")}`);
    console.error(`  ${path.join(process.cwd(), ".env")}`);
    console.error("");
    console.error("with AGENTROUTER_API_KEY=<your-agentrouter-key>");
    return 1;
  }

  process.env.AGENTROUTER_PROXY_HOST = String(host);
  process.env.AGENTROUTER_PROXY_PORT = String(port);
  process.env.AGENTROUTER_LOG_DIR = paths.getLogsDir();

  const child = proc.spawnProxy({
    host,
    port,
    foreground: opts.foreground,
    cwd: paths.PKG_ROOT,
  });

  proc.writePidRecord({ pid: child.pid, host, port });

  if (opts.foreground) {
    console.log(
      `AgentRouter proxy running in foreground at http://${host}:${port}/v1 (Ctrl+C to stop)`
    );
    const code = await new Promise((resolve) => {
      child.on("exit", (c) => resolve(c ?? 0));
    });
    proc.clearPid();
    return code;
  }

  console.log("Starting AgentRouter proxy...");

  const ready = await proc.waitForPort(host, port, 30000);

  if (!ready) {
    proc.clearPid();
    console.error("");
    console.error("Proxy failed to start. Check:");
    console.error(`  ${path.join(paths.getLogsDir(), "uvicorn.log")}`);
    console.error(`  ${path.join(paths.getLogsDir(), "uvicorn-error.log")}`);
    return 1;
  }

  console.log("");
  console.log("AgentRouter proxy is READY.");
  console.log("");
  console.log(`Endpoint:  http://${host}:${port}/v1`);
  console.log(`Logs:      ${paths.getLogsDir()}`);
  console.log("");
  return 0;
}

async function cmdStop(opts) {
  loadEnv();

  const record = proc.readPidRecord();
  const host = resolveHost(opts);
  const port = resolvePort(opts, record);

  banner("Stop AgentRouter Direct Proxy");

  if (!record) {
    if (await proc.isPortOpen(host, port)) {
      console.log(`Port ${host}:${port} is in use, but no PID file was found.`);
      console.log("The proxy may have been started by other means (e.g. PowerShell scripts).");
      console.log("");
      return 1;
    }

    console.log("AgentRouter proxy is not running.");
    console.log("");
    return 0;
  }

  console.log(`Stopping PID ${record.pid} ...`);
  await proc.stopByRecord(record);
  console.log("AgentRouter proxy stopped.");
  console.log("");
  return 0;
}

async function cmdRestart(opts) {
  const stopCode = await cmdStop(opts);
  if (stopCode !== 0 && proc.readPidRecord()) return stopCode;
  return cmdStart(opts);
}

async function cmdStatus(opts) {
  loadEnv();

  const record = proc.readPidRecord();
  const host = resolveHost(opts);
  const port = resolvePort(opts, record);
  const open = await proc.isPortOpen(host, port);
  const alive = record ? proc.isPidAlive(record.pid) : false;

  console.log(
    `PID file : ${
      record
        ? `${record.pid} (${alive ? "alive" : "stale"})`
        : "none"
    }`
  );
  console.log(
    `Endpoint : http://${host}:${port}/v1 (${open ? "running" : "not running"})`
  );
  return 0;
}

async function cmdDoctor() {
  loadEnv();

  const checks = [];
  let failures = 0;

  const nodeMajor = parseInt(process.versions.node.split(".")[0], 10);
  checks.push([
    nodeMajor >= 18,
    `Node.js          ${process.versions.node} ${
      nodeMajor >= 18 ? "(ok)" : "(FAIL: 18+ required)"
    }`,
  ]);

  const python = findPython();

  if (!python) {
    checks.push([false, "Python           NOT FOUND (need 3.11+ on PATH)"]);
  } else {
    const ok = pythonVersionOk(python);
    checks.push([
      ok,
      `Python           ${python.version} via "${python.cmd.join(" ")}" ${
        ok ? "(ok)" : `(FAIL: ${MIN_MAJOR}.${MIN_MINOR}+ required)`
      }`,
    ]);
  }

  if (venvExists()) {
    const ok = depsOk();
    checks.push([
      ok,
      `Venv             ${paths.getVenvDir()} ${
        ok ? "(ok, dependencies installed)" : "(dependencies missing, run: agentrouter-proxy start)"
      }`,
    ]);
  } else {
    checks.push([true, `Venv             not created yet (created on first start)`]);
  }

  const dataEnv = path.join(paths.getDataDir(), ".env");
  const cwdEnv = path.join(process.cwd(), ".env");
  const dataEnvExists = envFileExists(dataEnv);
  const cwdEnvExists = envFileExists(cwdEnv);

  checks.push([
    true,
    `.env             ${
      cwdEnvExists ? `${cwdEnv}` : ""
    }${cwdEnvExists && dataEnvExists ? " + " : ""}${
      dataEnvExists ? dataEnv : ""
    }${!cwdEnvExists && !dataEnvExists ? "none found (optional)" : ""}`,
  ]);

  const apiKey = (process.env.AGENTROUTER_API_KEY || "").trim();
  checks.push([
    !!apiKey,
    `AGENTROUTER_API_KEY   ${apiKey ? "set (ok)" : "NOT SET (required)"}`,
  ]);

  const host = resolveHost({});
  const port = resolvePort({});
  const open = await proc.isPortOpen(host, port);
  checks.push([
    true,
    `Port             ${host}:${port} ${open ? "in use (proxy running?)" : "free"}`,
  ]);

  const base = (
    process.env.AGENTROUTER_BASE_URL ||
    "https://agentrouter.org/v1"
  ).replace(/\/+$/, "");

  let upstreamLabel;

  try {
    const headers = {};

    if (apiKey) {
      headers.Authorization = `Bearer ${apiKey}`;
    }

    const res = await fetch(`${base}/models`, {
      headers,
      signal: AbortSignal.timeout(15000),
    });

    if (res.status === 200) {
      upstreamLabel = "HTTP 200 (ok)";
    } else if (res.status === 401) {
      upstreamLabel = `HTTP ${res.status} (auth failed - check AGENTROUTER_API_KEY)`;
    } else {
      upstreamLabel = `HTTP ${res.status}`;
    }
  } catch (err) {
    const reason = (err && err.cause && err.cause.code) || err.message;
    upstreamLabel = `unreachable (${reason})`;
  }

  checks.push([true, `Upstream         ${base} -> ${upstreamLabel}`]);

  banner("AgentRouter Direct Proxy - doctor");

  for (const [ok, label] of checks) {
    if (!ok) failures++;
    console.log(`${ok ? "  [ok]  " : "  [FAIL]"} ${label}`);
  }

  console.log("");
  console.log(
    failures === 0
      ? "All checks passed."
      : `${failures} check(s) failed.`
  );
  console.log("");

  return failures === 0 ? 0 : 1;
}

async function run(argv) {
  const opts = parseArgs(argv);

  switch (opts.command) {
    case "start":
      return cmdStart(opts);
    case "stop":
      return cmdStop(opts);
    case "restart":
      return cmdRestart(opts);
    case "status":
      return cmdStatus(opts);
    case "doctor":
      return cmdDoctor();
    default:
      console.log(HELP);
      return 0;
  }
}

module.exports = { run };
