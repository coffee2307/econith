import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const socialDir = path.join(root, "econith_social");

function freePort(port) {
  if (process.platform === "win32") {
    spawnSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        `Get-NetTCPConnection -LocalPort ${port} -ErrorAction SilentlyContinue | ` +
          "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }",
      ],
      { stdio: "ignore" },
    );
    return;
  }
  spawnSync("sh", ["-c", `lsof -ti:${port} | xargs kill -9 2>/dev/null || true`], {
    stdio: "ignore",
  });
}

if (!fs.existsSync(path.join(root, "node_modules", "concurrently"))) {
  console.log("Installing root dev dependencies...");
  const install = spawnSync("npm", ["install"], {
    cwd: root,
    stdio: "inherit",
    shell: true,
  });
  if (install.status !== 0) {
    process.exit(install.status ?? 1);
  }
}

console.log("Freeing social ports 3001 / 5001 if occupied...");
freePort(3001);
freePort(5001);

const { default: concurrently } = await import("concurrently");

const { result } = concurrently(
  [
    {
      command: "docker compose up --build",
      cwd: root,
      name: "docker",
      prefixColor: "blue",
    },
    {
      command: "npm run dev",
      cwd: socialDir,
      name: "social",
      prefixColor: "magenta",
    },
  ],
  {
    prefix: "name",
    restartTries: 0,
  },
);

result.catch(() => process.exit(1));
