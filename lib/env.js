"use strict";

const fs = require("fs");
const path = require("path");
const { getDataDir } = require("./paths");

function parseDotEnv(text) {
  const result = {};

  for (const rawLine of text.split(/\r?\n/)) {
    let line = rawLine.trim();

    if (!line || line.startsWith("#")) continue;

    if (line.startsWith("export ")) {
      line = line.slice("export ".length).trim();
    }

    const eq = line.indexOf("=");
    if (eq <= 0) continue;

    const key = line.slice(0, eq).trim();
    if (!key || key in result) continue;

    let value = line.slice(eq + 1).trim();

    if (
      value.length >= 2 &&
      ((value[0] === '"' && value.endsWith('"')) ||
        (value[0] === "'" && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }

    result[key] = value;
  }

  return result;
}

function readEnvFile(file) {
  try {
    return parseDotEnv(fs.readFileSync(file, "utf8"));
  } catch {
    return {};
  }
}

function envFileExists(file) {
  try {
    fs.accessSync(file);
    return true;
  } catch {
    return false;
  }
}

// Priority: existing process env > ./.env (cwd) > ~/.agentrouter-proxy/.env
function loadEnv() {
  const layered = Object.assign(
    {},
    readEnvFile(path.join(getDataDir(), ".env")),
    readEnvFile(path.join(process.cwd(), ".env"))
  );

  for (const key of Object.keys(layered)) {
    if (process.env[key] === undefined) {
      process.env[key] = layered[key];
    }
  }

  return layered;
}

module.exports = { parseDotEnv, readEnvFile, loadEnv, envFileExists };
