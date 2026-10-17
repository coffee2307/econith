// ECONITH controlled dev startup: no arbitrary process/port termination.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { planDevServices } from "./runtime_services.mjs";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const requireSocial = /^(1|true|yes)$/i.test(process.env.ECONITH_REQUIRE_SOCIAL ?? "");

let plan;
try {
  plan = planDevServices(root, { requireSocial });
} catch (error) {
  console.error(String(error));
  process.exit(1);
}

if (!fs.existsSync(path.join(root, "node_modules", "concurrently"))) {
  console.log("Installing root dependencies from package-lock.json...");
  const install = spawnSync(process.platform === "win32" ? "npm.cmd" : "npm", ["ci"], {
    cwd: root,
    stdio: "inherit",
    shell: process.platform === "win32",
  });
  if (install.status !== 0) process.exit(install.status ?? 1);
}

if (!plan.social.available) {
  console.warn(`[ECONITH] Social sidecar skipped: ${plan.social.reason}`);
  console.warn("[ECONITH] Core Docker stack will run without the optional Social UI.");
}

// Never kill unrelated processes merely because their port numbers overlap.
// Docker or the Social application will report a binding error explicitly.
const { default: concurrently } = await import("concurrently");
const { result } = concurrently(plan.services, {
  prefix: "name",
  restartTries: 0,
  killOthersOn: ["failure"],
});
result.catch(() => { process.exitCode = 1; });
