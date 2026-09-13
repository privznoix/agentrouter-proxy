"use strict";

const net = require("net");
const fs = require("fs");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const {
  getLogsDir,
  getPidFile,
  ensureDataDirs,
  venvPython,
} = require("./paths");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function isPortOpen(host, port, timeoutMs = 1000) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;

    const finish = (value) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(value);
    };

    socket.setTimeout(timeoutMs);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
    socket.connect(port, host);
  });
}

async function waitForPort(host, port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    if (await isPortOpen(host, port)) return true;
    await sleep(1000);
  }

  return false;
}

async function waitForPortClosed(host, port, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    if (!(await isPortOpen(host, port))) return true;
    await sleep(500);
  }

  return false;
}

function readPidRecord() {
  try {
    const raw = fs.readFileSync(getPidFile(), "utf8").trim();
    const parsed = JSON.parse(raw);
    if (parsed && Number.isFinite(parsed.pid)) return parsed;
  } catch {}
  return null;
}

function writePidRecord(record) {
  ensureDataDirs();
  fs.writeFileSync(getPidFile(), JSON.stringify(record), "utf8");
}

function clearPid() {
  try {
    fs.unlinkSync(getPidFile());
  } catch {}
}

function isPidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === "EPERM";
  }
}

function spawnProxy({ host, port, foreground, cwd }) {
  ensureDataDirs();

  const args = [
    "-m",
    "uvicorn",
    "agentrouter-proxy:app",
    "--host",
    String(host),
    "--port",
    String(port),
    "--workers",
    "1",
    "--no-access-log",
  ];

  let stdio;

  if (foreground) {
    stdio = "inherit";
  } else {
    const outFile = fs.openSync(
      path.join(getLogsDir(), "uvicorn.log"),
      "a"
    );
    const errFile = fs.openSync(
      path.join(getLogsDir(), "uvicorn-error.log"),
      "a"
    );
    stdio = ["ignore", outFile, errFile];
  }

  const child = spawn(venvPython(), args, {
    cwd,
    env: process.env,
    detached: !foreground,
    stdio,
    windowsHide: true,
  });

  if (!foreground) {
    child.unref();

    if (Array.isArray(stdio)) {
      if (typeof stdio[1] === "number") {
        try { fs.closeSync(stdio[1]); } catch {}
      }
      if (typeof stdio[2] === "number") {
        try { fs.closeSync(stdio[2]); } catch {}
      }
    }
  }

  return child;
}

async function stopByRecord(record) {
  const { pid, host, port } = record;
  let killed = false;

  if (isPidAlive(pid)) {
    if (process.platform === "win32") {
      const result = spawnSync(
        "taskkill",
        ["/PID", String(pid), "/T", "/F"],
        { windowsHide: true }
      );
      killed = result.status === 0;
    } else {
      try {
        process.kill(pid, "SIGTERM");
        killed = true;
      } catch {}

      const deadline = Date.now() + 5000;
      while (Date.now() < deadline && isPidAlive(pid)) {
        await sleep(250);
      }

      if (isPidAlive(pid)) {
        try {
          process.kill(pid, "SIGKILL");
        } catch {}
      }
    }
  } else {
    killed = true;
  }

  clearPid();

  if (host && port) {
    await waitForPortClosed(host, port, 10000);
  }

  return killed;
}

module.exports = {
  isPortOpen,
  waitForPort,
  waitForPortClosed,
  readPidRecord,
  writePidRecord,
  clearPid,
  isPidAlive,
  spawnProxy,
  stopByRecord,
};
