"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const { getVenvDir, getRequirementsFile, venvPython } = require("./paths");

const MIN_MAJOR = 3;
const MIN_MINOR = 11;

const CANDIDATES =
  process.platform === "win32"
    ? ["python", "py -3", "python3"]
    : ["python3", "python"];

function runCapture(cmd, args, timeoutMs) {
  return spawnSync(cmd, args, {
    encoding: "utf8",
    windowsHide: true,
    timeout: timeoutMs,
  });
}

function tryPython(candidate) {
  const parts = candidate.split(/\s+/);
  const result = runCapture(
    parts[0],
    parts.slice(1).concat(["--version"]),
    10000
  );

  if (result.error || result.status !== 0) return null;

  const output = `${result.stdout || ""}${result.stderr || ""}`.trim();
  const match = output.match(/Python\s+(\d+)\.(\d+)(?:\.(\d+))?/);
  if (!match) return null;

  return {
    cmd: parts,
    major: parseInt(match[1], 10),
    minor: parseInt(match[2], 10),
    version: `${match[1]}.${match[2]}${match[3] ? "." + match[3] : ""}`,
  };
}

function findPython() {
  for (const candidate of CANDIDATES) {
    const found = tryPython(candidate);
    if (found) return found;
  }
  return null;
}

function pythonVersionOk(info) {
  return (
    info.major > MIN_MAJOR ||
    (info.major === MIN_MAJOR && info.minor >= MIN_MINOR)
  );
}

function venvExists() {
  return fs.existsSync(venvPython());
}

function depsOk() {
  const result = runCapture(
    venvPython(),
    ["-c", "import fastapi, uvicorn, httpx"],
    30000
  );
  return !result.error && result.status === 0;
}

// Returns true when the venv was created/repaired, false when already ready.
function ensureVenv(pythonInfo, log) {
  if (venvExists() && depsOk()) return false;

  if (!venvExists()) {
    log(`Creating virtual environment: ${getVenvDir()}`);
    const created = runCapture(
      pythonInfo.cmd[0],
      pythonInfo.cmd.slice(1).concat(["-m", "venv", getVenvDir()]),
      300000
    );

    if (created.error || created.status !== 0) {
      throw new Error(
        `Failed to create virtual environment: ${
          created.stderr || created.stdout || created.error
        }`
      );
    }
  } else {
    log("Virtual environment found but dependencies missing; installing ...");
  }

  log("Installing Python dependencies (fastapi, uvicorn, httpx) ...");
  const installed = runCapture(
    venvPython(),
    [
      "-m",
      "pip",
      "install",
      "--disable-pip-version-check",
      "-r",
      getRequirementsFile(),
    ],
    600000
  );

  if (installed.error || installed.status !== 0) {
    throw new Error(
      `pip install failed: ${
        installed.stderr || installed.stdout || installed.error
      }`
    );
  }

  return true;
}

module.exports = {
  findPython,
  pythonVersionOk,
  ensureVenv,
  venvExists,
  depsOk,
  MIN_MAJOR,
  MIN_MINOR,
};
