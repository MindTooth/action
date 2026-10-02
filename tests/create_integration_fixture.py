"""Create an isolated project for exercising the full composite action in CI."""

import json
import os
from pathlib import Path


project = Path("work/test site")
project.mkdir(parents=True, exist_ok=True)
scenario = os.environ["SCENARIO"]
runtime = {"name": "node", "version": "22.x", "onFail": "warn"}
manifest = {
    "name": "toolchain-integration",
    "private": True,
    "scripts": {"build": "node build.cjs"},
    "devEngines": {
        "runtime": runtime,
        "packageManager": {"name": "pnpm", "version": "11.28.2"},
    },
}
if scenario == "runtime-array":
    manifest["devEngines"]["runtime"] = [{"name": "bun", "version": "1"}, runtime]
elif scenario == "pnpm-precedence":
    manifest["packageManager"] = "pnpm@9.15.0"
elif scenario in ["defaults", "empty-fields", "inherited-node", "inherited-empty", "npm-no-jq"]:
    del manifest["devEngines"]["runtime"]

if scenario == "empty-fields":
    manifest["volta"] = {"node": ""}
    manifest["engines"] = {"node": "  "}
    manifest["devEngines"]["runtime"] = {"name": "node", "version": ""}
elif scenario in ["inherited-node", "inherited-empty"]:
    manifest["volta"] = {"extends": "../package.json"}
    parent = {"volta": {"node": "22.x"}} if scenario == "inherited-node" else {"volta": {"yarn": "1.22.22"}}
    (project.parent / "package.json").write_text(json.dumps(parent))
elif scenario == "npm-no-jq":
    del manifest["devEngines"]

(project / "package.json").write_text(json.dumps(manifest))
(project / "pnpm-lock.yaml").write_text(
    "lockfileVersion: '9.0'\nsettings:\n  autoInstallPeers: true\n"
    "  excludeLinksFromLockfile: false\nimporters:\n  .: {}\n"
)
if scenario == "npm-no-jq":
    (project / "pnpm-lock.yaml").unlink()
    (project / "package-lock.json").write_text(json.dumps({
        "name": "toolchain-integration", "lockfileVersion": 3, "requires": True,
        "packages": {"": {"name": "toolchain-integration"}},
    }))

(project / "build.cjs").write_text("""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
assert.equal(process.versions.node.split('.')[0], process.env.EXPECT_NODE);
if (process.env.SCENARIO !== 'npm-no-jq') {
  assert.equal(execFileSync('pnpm', ['--version'], { encoding: 'utf8' }).trim(), '11.28.2');
}
fs.mkdirSync('dist', { recursive: true });
fs.writeFileSync('dist/index.html', '<h1>Toolchain verified</h1>');
""")
