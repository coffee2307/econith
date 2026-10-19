// Deterministic root setup; optional Social sidecar is explicit.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { inspectSocialSidecar } from "./runtime_services.mjs";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const requireSocial = /^(1|true|yes)$/i.test(process.env.ECONITH_REQUIRE_SOCIAL ?? "");

function run(cmd, args, cwd) {
  const command = process.platform === "win32" ? `${cmd}.cmd` : cmd;
  const result = spawnSync(command, args, {
    cwd, stdio: "inherit", shell: process.platform === "win32",
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

if (!fs.existsSync(path.join(root, "node_modules", "concurrently"))) {
  console.log("Installing root dev dependencies from package-lock.json...");
  run("npm", ["ci"], root);
}

const social = inspectSocialSidecar(root);
if (!social.available) {
  if (requireSocial) {
    console.error(`[ECONITH] Required Social sidecar unavailable: ${social.reason}`);
    process.exit(1);
  }
  console.warn(`[ECONITH] Optional Social setup skipped: ${social.reason}`);
} else if (social.setupAvailable) {
  console.log("Setting up ECONITH Social...");
  run("npm", ["run", "setup:all"], social.directory);
} else {
  console.log("[ECONITH] Social has no setup:all script; leaving it untouched.");
}
console.log("Setup complete. Start development with: npm run dev");
