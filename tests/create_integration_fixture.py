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
        "packageManager": {"name": "pnpm", "version": "10.11.0"},
    },
}
if scenario == "runtime-array":
    manifest["devEngines"]["runtime"] = [{"name": "bun", "version": "1"}, runtime]
elif scenario == "pnpm-precedence":
    manifest["packageManager"] = "pnpm@9.15.0"
elif scenario == "defaults":
    del manifest["devEngines"]["runtime"]

(project / "package.json").write_text(json.dumps(manifest))
(project / "pnpm-lock.yaml").write_text(
    "lockfileVersion: '9.0'\nsettings:\n  autoInstallPeers: true\n"
    "  excludeLinksFromLockfile: false\nimporters:\n  .: {}\n"
)
(project / "build.cjs").write_text("""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
assert.equal(process.versions.node.split('.')[0], process.env.EXPECT_NODE);
assert.equal(execFileSync('pnpm', ['--version'], { encoding: 'utf8' }).trim(), '10.11.0');
fs.mkdirSync('dist', { recursive: true });
fs.writeFileSync('dist/index.html', '<h1>Toolchain verified</h1>');
""")
