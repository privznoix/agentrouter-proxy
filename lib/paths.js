"use strict";

const os = require("os");
const path = require("path");
const fs = require("fs");

const PKG_ROOT = path.resolve(__dirname, "..");

function getDataDir() {
  return path.join(os.homedir(), ".agentrouter-proxy");
}

function getVenvDir() {
  return path.join(getDataDir(), "venv");
}

function getLogsDir() {
  return path.join(getDataDir(), "logs");
}

function getPidFile() {
  return path.join(getDataDir(), "proxy.pid");
}

function getProxyScript() {
  return path.join(PKG_ROOT, "agentrouter-proxy.py");
}

function getRequirementsFile() {
  return path.join(PKG_ROOT, "requirements.txt");
}

function venvPython() {
  const binDir = process.platform === "win32" ? "Scripts" : "bin";
  const exe = process.platform === "win32" ? "python.exe" : "python";
  return path.join(getVenvDir(), binDir, exe);
}

function ensureDataDirs() {
  fs.mkdirSync(getDataDir(), { recursive: true });
  fs.mkdirSync(getLogsDir(), { recursive: true });
}

module.exports = {
  PKG_ROOT,
  getDataDir,
  getVenvDir,
  getLogsDir,
  getPidFile,
  getProxyScript,
  getRequirementsFile,
  venvPython,
  ensureDataDirs,
};
