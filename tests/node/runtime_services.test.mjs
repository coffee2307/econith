import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { inspectSocialSidecar, planDevServices } from "../../scripts/runtime_services.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function temporaryProject(action) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "econith-services-"));
  try {
    return action(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test("missing Social gitlink stays optional; Docker remains launchable", () =>
  temporaryProject((tmp) => {
    const plan = planDevServices(tmp);
    assert.equal(plan.social.available, false);
    assert.deepEqual(plan.services.map((s) => s.name), ["docker"]);
    assert.throws(
      () => planDevServices(tmp, { requireSocial: true }),
      /Social.*missing/i,
    );
  }),
);

test("truncated Social checkout does not masquerade as a runnable service", () =>
  temporaryProject((tmp) => {
    const subdir = path.join(tmp, "econith_social");
    fs.mkdirSync(subdir);
    fs.writeFileSync(path.join(subdir, "package.json"), '{"scripts":{"test":"true"}}');
    assert.equal(inspectSocialSidecar(tmp).available, false);
    fs.writeFileSync(path.join(subdir, "package.json"), "this is not JSON");
    assert.equal(inspectSocialSidecar(tmp).available, false);
  }),
);

test("valid Social sidecar runs with Docker and can be required", () =>
  temporaryProject((tmp) => {
    const sidecar = path.join(tmp, "econith_social");
    fs.mkdirSync(sidecar);
    fs.writeFileSync(
      path.join(sidecar, "package.json"),
      JSON.stringify({ scripts: { dev: "node app.js", "setup:all": "node setup.js" } }),
    );
    const plan = planDevServices(tmp, { requireSocial: true });
    assert.equal(plan.social.setupAvailable, true);
    assert.deepEqual(plan.services.map((s) => s.name), ["docker", "social"]);
    assert.equal(plan.services[1].cwd, sidecar);
  }),
);

test("startup never kills arbitrary processes by occupied port number", () => {
  const script = fs.readFileSync(path.join(root, "scripts", "dev.mjs"), "utf8");
  assert.doesNotMatch(script, /Stop-Process|kill -9|freePort\(/);
  assert.match(script, /planDevServices/);
  assert.match(script, /\["ci"\]/);
});

test("root setup is pinned and does not assume Social checkout exists", () => {
  const script = fs.readFileSync(path.join(root, "scripts", "setup.mjs"), "utf8");
  assert.match(script, /inspectSocialSidecar/);
  assert.match(script, /\["ci"\]/);
  assert.match(script, /SOCIAL_REQUIRE|ECONITH_REQUIRE_SOCIAL/);
});
