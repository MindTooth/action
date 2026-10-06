// Create an isolated project for exercising the full composite action in CI.
import { mkdirSync, unlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const project = path.join('work', 'test site');
mkdirSync(project, { recursive: true });
const scenario = process.env.SCENARIO;
const runtime = { name: 'node', version: '22.x', onFail: 'warn' };
const manifest = {
  name: 'toolchain-integration',
  private: true,
  scripts: { postinstall: 'node verify-toolchain.cjs', build: 'node build.cjs' },
  devEngines: {
    runtime,
    packageManager: { name: 'pnpm', version: '11.28.2' },
  },
};
if (scenario === 'runtime-array') {
  manifest.devEngines.runtime = [{ name: 'bun', version: '1' }, runtime];
} else if (scenario === 'pnpm-precedence') {
  manifest.packageManager = 'pnpm@9.15.0';
} else if (scenario === 'pnpm-range') {
  // Bound the range so the expected version cannot drift with future releases.
  manifest.devEngines.packageManager.version = '>=11.28.1 <=11.28.2';
} else if (scenario === 'pnpm-override-warn') {
  manifest.devEngines.packageManager.onFail = 'warn';
} else if (scenario === 'pnpm-override-error') {
  manifest.devEngines.packageManager.onFail = 'error';
} else if (['defaults', 'empty-fields', 'inherited-node', 'inherited-empty', 'npm-no-jq'].includes(scenario)) {
  delete manifest.devEngines.runtime;
}

if (scenario === 'empty-fields') {
  manifest.volta = { node: '' };
  manifest.engines = { node: '  ' };
  manifest.devEngines.runtime = { name: 'node', version: '' };
} else if (['inherited-node', 'inherited-empty'].includes(scenario)) {
  manifest.volta = { extends: '../package.json' };
  const parent = scenario === 'inherited-node'
    ? { volta: { node: '22.x' } } : { volta: { yarn: '1.22.22' } };
  writeFileSync(path.join(project, '..', 'package.json'), JSON.stringify(parent));
} else if (scenario.startsWith('npm-')) {
  manifest.devEngines.packageManager = { name: 'npm' };
  if (scenario === 'npm-no-lockfile') {
    // setup-node auto-detects npm from the repository root unless disabled.
    writeFileSync('package.json', JSON.stringify({ packageManager: 'npm@11.6.2' }));
  }
}

writeFileSync(path.join(project, 'package.json'), JSON.stringify(manifest));
writeFileSync(path.join(project, 'pnpm-lock.yaml'),
  "lockfileVersion: '9.0'\nsettings:\n  autoInstallPeers: true\n" +
  '  excludeLinksFromLockfile: false\nimporters:\n  .: {}\n');
if (scenario.startsWith('npm-')) {
  unlinkSync(path.join(project, 'pnpm-lock.yaml'));
  if (scenario !== 'npm-no-lockfile') {
    const lockfile = scenario === 'npm-shrinkwrap' ? 'npm-shrinkwrap.json' : 'package-lock.json';
    writeFileSync(path.join(project, lockfile), JSON.stringify({
      name: 'toolchain-integration', lockfileVersion: 3, requires: true,
      packages: { '': { name: 'toolchain-integration' } },
    }));
  }
} else if (scenario === 'pnpm-no-lockfile') {
  unlinkSync(path.join(project, 'pnpm-lock.yaml'));
}

writeFileSync(path.join(project, 'verify-toolchain.cjs'), `
const assert = require('node:assert/strict');
const fs = require('node:fs');
assert.equal(process.versions.node.split('.')[0], process.env.EXPECT_NODE);
if (!process.env.SCENARIO.startsWith('npm-')) {
  // Inspect the manager running this lifecycle, rather than launching a CLI
  // that could switch versions and conceal which manager ran install/build.
  assert.equal(process.env.npm_config_user_agent.split(' ')[0], 'pnpm/' + process.env.EXPECT_PNPM);
}
if (process.env.npm_lifecycle_event === 'postinstall') fs.writeFileSync('install-verified', 'ok');
`);
writeFileSync(path.join(project, 'build.cjs'), `
require('./verify-toolchain.cjs');
const fs = require('node:fs');
fs.mkdirSync('dist', { recursive: true });
fs.writeFileSync('dist/index.html', '<h1>Toolchain verified</h1>');
`);
