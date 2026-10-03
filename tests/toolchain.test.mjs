// Execute the action's actual resolver: npm ci --prefix tests && npm test --prefix tests.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { parse } from 'yaml';

const action = parse(readFileSync(new URL('../action.yml', import.meta.url), 'utf8'));
const resolver = action.runs.steps.find((step) => step.id === 'toolchain');
const scriptRunner = String.raw`
const fs = require('node:fs');
const core = {
  setOutput: (name, value) => fs.appendFileSync(process.env.GITHUB_OUTPUT, name + '=' + value + '\n'),
  exportVariable: (name, value) => fs.appendFileSync(process.env.GITHUB_ENV, name + '=' + value + '\n'),
  warning: (message) => console.log('::warning::' + message),
};
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
new AsyncFunction('core', 'require', fs.readFileSync(0, 'utf8'))(core, require)
  .catch((error) => { console.error(error.message); process.exitCode = 1; });
`;

function resolve({ manifest, files = ['pnpm-lock.yaml'], pm = '', node = '', projectPath = '.', inherited = {}, rawManifest } = {}) {
  const root = mkdtempSync(path.join(tmpdir(), 'astro-action-'));
  try {
    const project = path.join(root, projectPath);
    mkdirSync(project, { recursive: true });
    if (manifest !== undefined || rawManifest !== undefined) {
      writeFileSync(path.join(project, 'package.json'), rawManifest ?? JSON.stringify(manifest));
    }
    for (const [name, data] of Object.entries(inherited)) {
      const filename = path.join(root, name);
      mkdirSync(path.dirname(filename), { recursive: true });
      writeFileSync(filename, JSON.stringify(data));
    }
    for (const name of files) writeFileSync(path.join(project, name), '');
    const envFile = path.join(root, 'env');
    const outputFile = path.join(root, 'output');
    writeFileSync(envFile, '');
    writeFileSync(outputFile, '');
    const result = spawnSync(process.execPath, ['-e', scriptRunner], {
      cwd: root,
      input: resolver.with.script,
      encoding: 'utf8',
      env: { ...process.env, PATH: '', GITHUB_WORKSPACE: root,
        INPUT_PM: pm, INPUT_NODE: node, PROJECT_PATH: projectPath,
        GITHUB_ENV: envFile, GITHUB_OUTPUT: outputFile },
    });
    assert.ifError(result.error);
    const values = Object.fromEntries(
      (readFileSync(envFile, 'utf8') + readFileSync(outputFile, 'utf8'))
        .split('\n').filter(Boolean).map((line) => {
          const separator = line.indexOf('=');
          return [line.slice(0, separator), line.slice(separator + 1)];
        }),
    );
    return { ...result, values };
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function successful(options) {
  const result = resolve(options);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  return result;
}

test('Node declarations resolve', () => {
  for (const [manifest, expected] of [
    [{ devEngines: { runtime: { name: 'node', version: '22.x' } } }, '22.x'],
    [{ devEngines: { runtime: [{ name: 'bun', version: '1' }, { name: 'NODE', version: '>=22 <23' }] } }, '>=22 <23'],
    [{ engines: { node: '22.x' } }, '22.x'],
    [{ volta: { node: '22.14.0' } }, '22.14.0'],
  ]) {
    assert.equal(successful({ manifest, projectPath: 'sites/my site' }).values['node-version'], expected);
  }
});

test('Node falls back to 24 for missing and empty declarations', () => {
  for (const manifest of [undefined, {}, { devEngines: {} },
    { devEngines: { runtime: { name: 'node' } } },
    { devEngines: { runtime: { name: 'bun', version: '1' } } },
    { devEngines: { runtime: [] } }, { engines: { node: '' } },
    { engines: { node: '  ' } }, { volta: { node: '' } }, { volta: { extends: '' } },
    { devEngines: { runtime: { name: 'node', version: '' } } },
  ]) {
    assert.equal(successful({ manifest }).values['node-version'], '24');
  }
});

test('explicit Node version overrides the manifest', () => {
  assert.equal(successful({ node: '24.1.0', manifest: {
    devEngines: { runtime: { name: 'node', version: '22.x' } },
  } }).values['node-version'], '24.1.0');
});

test('pnpm manifest versions delegate to action-setup', () => {
  for (const manifest of [
    { packageManager: 'pnpm@11.28.2' },
    { packageManager: 'pnpm@11.28.2+sha512.example' },
    { devEngines: { packageManager: { name: 'pnpm', version: '^11.28.2' } } },
    { packageManager: 'pnpm@9.0.0', devEngines: { packageManager: { name: 'pnpm', version: '11.28.2' } } },
  ]) {
    for (const pm of ['', 'pnpm']) {
      const result = successful({ manifest, pm });
      assert.equal(result.values.VERSION, '');
      assert.ok(!result.stdout.includes('::warning'));
      assert.equal(result.values.LOCKFILE, 'pnpm-lock.yaml');
    }
  }
});

test('pnpm falls back to latest with a warning', () => {
  for (const manifest of [undefined, {}, { packageManager: 'npm@11' },
    { devEngines: { packageManager: { name: 'pnpm' } } },
    { devEngines: { packageManager: { name: 'npm', version: '11' } } },
  ]) {
    const result = successful({ manifest });
    assert.equal(result.values.VERSION, 'latest');
    assert.match(result.stdout, /::warning/);
  }
});

test('explicit pnpm version overrides the manifest', () => {
  assert.equal(successful({ pm: 'pnpm@11.28.2', manifest: {
    devEngines: { packageManager: { name: 'pnpm', version: '9.0.0' } },
  } }).values.VERSION, '11.28.2');
});

test('other package managers resolve their lockfiles and cache support', () => {
  for (const [pm, lockfile, version] of [
    ['npm', 'package-lock.json', 'latest'], ['yarn', 'yarn.lock', ''],
    ['bun', 'bun.lock', 'latest'], ['bun', 'bun.lockb', 'latest'],
    ['deno', 'deno.lock', 'vx.x.x'],
  ]) {
    for (const override of ['', pm]) {
      const { values } = successful({ files: [lockfile], pm: override });
      assert.equal(values.PACKAGE_MANAGER, pm);
      assert.equal(values.LOCKFILE, lockfile);
      assert.equal(values.VERSION, version);
      const cached = ['npm', 'pnpm', 'yarn'].includes(pm);
      assert.equal(values['dependency-cache'], cached ? pm : '');
      if (cached) assert.ok(values['dependency-cache-path'].endsWith(path.sep + lockfile));
      else assert.equal(values['dependency-cache-path'], '');
    }
  }
});

test('explicit managers without lockfiles disable dependency caching', () => {
  for (const pm of ['pnpm@11.28.2', 'npm', 'yarn', 'bun', 'deno']) {
    const { values } = successful({ files: [], pm });
    assert.equal(values.PACKAGE_MANAGER, pm.split('@')[0]);
    assert.equal(values.LOCKFILE, '');
    assert.equal(values['dependency-cache'], '');
    assert.equal(values['dependency-cache-path'], '');
  }
});

test('npm shrinkwrap is detected and takes precedence', () => {
  for (const files of [['npm-shrinkwrap.json'], ['package-lock.json', 'npm-shrinkwrap.json']]) {
    for (const pm of ['', 'npm']) {
      const { values } = successful({ files, pm });
      assert.equal(values.PACKAGE_MANAGER, 'npm');
      assert.equal(values.LOCKFILE, 'npm-shrinkwrap.json');
      assert.equal(values['dependency-cache'], 'npm');
      assert.ok(values['dependency-cache-path'].endsWith(path.sep + 'npm-shrinkwrap.json'));
    }
  }
});

test('cache only uses the selected manager’s lockfile', () => {
  const { values } = successful({ files: ['pnpm-lock.yaml'], pm: 'npm' });
  assert.equal(values['dependency-cache'], '');
  assert.equal(values['dependency-cache-path'], '');
});

test('missing lockfile and invalid manager fail', () => {
  for (const options of [{ files: [] }, { pm: 'invalid' }]) {
    assert.notEqual(resolve(options).status, 0);
  }
});

test('malformed manifest fails', () => {
  const result = resolve({ rawManifest: '{broken' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /JSON/);
});

test('inherited Node resolves or falls back to 24', () => {
  for (const [parent, expected] of [
    [{ volta: { node: '22.x' } }, '22.x'], [{ engines: { node: '22.x' } }, '22.x'],
    [{ volta: { yarn: '1.22.22' } }, '24'], [{ engines: { node: '' } }, '24'],
  ]) {
    const { values } = successful({ projectPath: 'sites/my site',
      manifest: { volta: { extends: '../../package.json' } },
      inherited: { 'package.json': parent } });
    assert.equal(values['node-version'], expected);
  }
});

test('inheritance chains resolve and local declarations take precedence', () => {
  assert.equal(successful({ projectPath: 'sites/my site',
    manifest: { volta: { extends: '../package.json' } },
    inherited: { 'sites/package.json': { volta: { extends: '../package.json' } },
      'package.json': { engines: { node: '22.x' } } },
  }).values['node-version'], '22.x');
  for (const [manifest, expected] of [
    [{ volta: { node: '24', extends: 'missing.json' },
      devEngines: { runtime: { name: 'node', version: '22.x' } } }, '24'],
    [{ engines: { node: '24' }, devEngines: { runtime: [
      { name: 'node', version: '22.x' }, { name: 'node', version: '24' }] } }, '22.x'],
    [{ engines: { node: '22.x' }, volta: { extends: 'missing.json' } }, '22.x'],
  ]) {
    assert.equal(successful({ manifest }).values['node-version'], expected);
  }
});

test('broken and circular inheritance fail', () => {
  for (const [target, expected] of [['missing.json', /ENOENT/], ['package.json', /Circular/]]) {
    const result = resolve({ manifest: { volta: { extends: target } } });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, expected);
  }
});

test('explicit Node version skips inheritance', () => {
  assert.equal(successful({ node: '24', manifest: {
    volta: { extends: 'missing.json' },
  } }).values['node-version'], '24');
});

test('npm resolves without executables on PATH', () => {
  // Run Node by absolute path with an empty PATH to catch external parser dependencies.
  for (const node of ['', '24']) {
    const { values } = successful({ manifest: {}, files: ['package-lock.json'], pm: 'npm', node });
    assert.equal(values['node-version'], '24');
    assert.equal(values.PACKAGE_MANAGER, 'npm');
  }
});

test('setup actions receive resolved inputs', () => {
  assert.equal(action.inputs['node-version'].default, '');
  for (const step of action.runs.steps) {
    if (step.uses?.startsWith('actions/setup-node@')) {
      assert.equal(step.with['node-version'], '${{ steps.toolchain.outputs.node-version }}');
      assert.equal(step.with['package-manager-cache'], false);
      if (step.name === 'Setup Node') {
        assert.equal(step.with.cache, '${{ steps.toolchain.outputs.dependency-cache }}');
        assert.equal(step.with['cache-dependency-path'], '${{ steps.toolchain.outputs.dependency-cache-path }}');
      }
    }
    if (step.uses?.startsWith('pnpm/action-setup@')) {
      assert.equal(step.with.version, '${{ env.VERSION }}');
      assert.equal(step.with.package_json_file, '${{ inputs.path }}/package.json');
    }
  }
});
