// Pure runtime startup planning. Never modifies processes, ports, or Git state.
import fs from "node:fs";
import path from "node:path";

export function inspectSocialSidecar(root) {
  const directory = path.join(root, "econith_social");
  const manifest = path.join(directory, "package.json");
  if (!fs.existsSync(manifest)) {
    return {
      available: false,
      directory,
      reason: "econith_social/package.json missing (optional checkout unresolved)",
      setupAvailable: false,
    };
  }
  try {
    const pkg = JSON.parse(fs.readFileSync(manifest, "utf8"));
    if (typeof pkg?.scripts?.dev !== "string" || !pkg.scripts.dev.trim()) {
      return {
        available: false,
        directory,
        reason: "econith_social does not declare an npm dev script",
        setupAvailable: false,
      };
    }
    return {
      available: true,
      directory,
      reason: null,
      setupAvailable: typeof pkg.scripts["setup:all"] === "string",
    };
  } catch {
    return {
      available: false,
      directory,
      reason: "econith_social package.json is unreadable or invalid JSON",
      setupAvailable: false,
    };
  }
}

export function planDevServices(root, { requireSocial = false } = {}) {
  const social = inspectSocialSidecar(root);
  if (requireSocial && !social.available) {
    throw new Error(`ECONITH_REQUIRE_SOCIAL enabled: ${social.reason}`);
  }
  const services = [
    {
      command: "docker compose up --build",
      cwd: root,
      name: "docker",
      prefixColor: "blue",
    },
  ];
  if (social.available) {
    services.push({
      command: "npm run dev",
      cwd: social.directory,
      name: "social",
      prefixColor: "magenta",
    });
  }
  return { services, social };
}
