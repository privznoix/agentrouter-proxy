#!/usr/bin/env node
"use strict";

const { run } = require("../lib/cli");

run(process.argv.slice(2)).then(
  (code) => process.exit(code),
  (err) => {
    console.error(`Unexpected error: ${err && err.stack ? err.stack : err}`);
    process.exit(1);
  }
);
