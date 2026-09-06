import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

function run(cmd, args, opts = {}) {
  const result = spawnSync(cmd, args, { stdio: "inherit", shell: true, ...opts });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

if (!fs.existsSync(path.join(root, "node_modules", "concurrently"))) {
  console.log("Installing root dev dependencies...");
  run("npm", ["install"], { cwd: root });
}

console.log("Setting up econith_social...");
run("npm", ["run", "setup:all"], { cwd: path.join(root, "econith_social") });

console.log("Setup complete. Start everything with: npm run dev");
