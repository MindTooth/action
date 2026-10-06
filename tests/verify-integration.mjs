// Validate successful builds and the intentional pnpm install failure in CI.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';

const project = path.resolve('work', 'test site');
const scenario = process.env.SCENARIO;
const installMarker = path.join(project, 'install-verified');
const buildOutput = path.join(project, 'dist', 'index.html');

if (scenario.startsWith('pnpm-override-')) {
  // Outside the fixture, the manifest cannot switch pnpm. This proves setup
  // installed the explicit input, even if the project later uses another version.
  const setupVersion = spawnSync('pnpm', ['--version'], { encoding: 'utf8' });
  assert.ifError(setupVersion.error);
  assert.equal(setupVersion.status, 0, setupVersion.stdout + setupVersion.stderr);
  assert.equal(setupVersion.stdout.trim(), '11.28.1');
}

if (scenario === 'pnpm-override-error') {
  assert.equal(process.env.ACTION_OUTCOME, 'failure', 'The manifest conflict must fail the action');
  assert.equal(existsSync(installMarker), false, 'Install must fail before postinstall');
  assert.equal(existsSync(buildOutput), false, 'Build must not run after failed install');
  // A generic failed composite step could conceal a setup/cache failure.
  // Check the real install diagnostic to verify the expected conflict instead.
  const install = spawnSync('pnpm', ['install'], { cwd: project, encoding: 'utf8' });
  assert.ifError(install.error);
  assert.notEqual(install.status, 0);
  assert.match(install.stdout + install.stderr,
    /This project is configured to use 11\.28\.2 of pnpm\. Your current pnpm is v11\.28\.1/);
} else {
  assert.equal(process.env.ACTION_OUTCOME, 'success');
  assert.ok(existsSync(installMarker), 'Install must verify the actual toolchain');
  assert.ok(existsSync(buildOutput), 'Build must verify the actual toolchain and produce output');
}
