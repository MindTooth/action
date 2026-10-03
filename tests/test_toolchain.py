"""Execute the action's actual resolver. Run: python -m unittest discover -s tests."""

import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

import yaml


ACTION = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text())
RESOLVER = next(s for s in ACTION["runs"]["steps"] if s.get("id") == "toolchain")


NODE = shutil.which("node")
SCRIPT_RUNNER = """
const fs = require('node:fs');
const core = {
  setOutput: (name, value) => fs.appendFileSync(process.env.GITHUB_OUTPUT, `${name}=${value}\n`),
  exportVariable: (name, value) => fs.appendFileSync(process.env.GITHUB_ENV, `${name}=${value}\n`),
  warning: (message) => console.log(`::warning::${message}`),
};
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
new AsyncFunction('core', 'require', fs.readFileSync(0, 'utf8'))(core, require)
  .catch((error) => { console.error(error.message); process.exitCode = 1; });
"""


class ToolchainTests(unittest.TestCase):
    def resolve(self, manifest=None, files=("pnpm-lock.yaml",), pm="", node="", path=".", inherited=None):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / path
            project.mkdir(parents=True, exist_ok=True)
            if manifest is not None:
                (project / "package.json").write_text(json.dumps(manifest))
            for name, data in (inherited or {}).items():
                filename = Path(root) / name
                filename.parent.mkdir(parents=True, exist_ok=True)
                filename.write_text(json.dumps(data))
            for name in files:
                (project / name).write_text("")
            env_file = Path(root) / "env"
            output_file = Path(root) / "output"
            env_file.touch()
            output_file.touch()
            result = subprocess.run(
                [NODE, "-e", SCRIPT_RUNNER],
                cwd=root,
                input=RESOLVER["with"]["script"],
                env={**os.environ, "PATH": "", "GITHUB_WORKSPACE": root,
                     "INPUT_PM": pm, "INPUT_NODE": node, "PROJECT_PATH": path,
                     "GITHUB_ENV": str(env_file), "GITHUB_OUTPUT": str(output_file)},
                capture_output=True, text=True,
            )
            values = dict(line.split("=", 1) for line in
                          (env_file.read_text() + output_file.read_text()).splitlines())
            return result, values

    def successful(self, **kwargs):
        result, values = self.resolve(**kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result, values

    def test_node_declarations_resolve(self):
        manifests = [
            {"devEngines": {"runtime": {"name": "node", "version": "22.x"}}},
            {"devEngines": {"runtime": [{"name": "bun", "version": "1"},
                                       {"name": "NODE", "version": ">=22 <23"}]}},
            {"engines": {"node": "22.x"}},
            {"volta": {"node": "22.14.0"}},
        ]
        for manifest, expected in zip(manifests, ["22.x", ">=22 <23", "22.x", "22.14.0"]):
            with self.subTest(manifest=manifest):
                _, values = self.successful(manifest=manifest, path="sites/my site")
                self.assertEqual(values["node-version"], expected)

    def test_node_fallback(self):
        for manifest in [None, {}, {"devEngines": {}},
                         {"devEngines": {"runtime": {"name": "node"}}},
                         {"devEngines": {"runtime": {"name": "bun", "version": "1"}}},
                         {"devEngines": {"runtime": []}},
                         {"engines": {"node": ""}}, {"engines": {"node": "  "}},
                         {"volta": {"node": ""}}, {"volta": {"extends": ""}},
                         {"devEngines": {"runtime": {"name": "node", "version": ""}}}]:
            with self.subTest(manifest=manifest):
                _, values = self.successful(manifest=manifest)
                self.assertEqual(values["node-version"], "24")

    def test_node_override(self):
        _, values = self.successful(node="24.1.0", manifest={
            "devEngines": {"runtime": {"name": "node", "version": "22.x"}}})
        self.assertEqual(values["node-version"], "24.1.0")

    def test_pnpm_manifest_versions_delegate(self):
        for manifest in [
            {"packageManager": "pnpm@11.28.2"},
            {"packageManager": "pnpm@11.28.2+sha512.example"},
            {"devEngines": {"packageManager": {"name": "pnpm", "version": "^11.28.2"}}},
            {"packageManager": "pnpm@9.0.0", "devEngines": {
                "packageManager": {"name": "pnpm", "version": "11.28.2"}}},
        ]:
            for pm in ["", "pnpm"]:
                with self.subTest(manifest=manifest, pm=pm):
                    result, values = self.successful(manifest=manifest, pm=pm)
                    self.assertEqual(values["VERSION"], "")
                    self.assertNotIn("::warning", result.stdout)
                    self.assertEqual(values["LOCKFILE"], "pnpm-lock.yaml")

    def test_pnpm_fallback_and_warning(self):
        for manifest in [None, {}, {"packageManager": "npm@11"},
                         {"devEngines": {"packageManager": {"name": "pnpm"}}},
                         {"devEngines": {"packageManager": {"name": "npm", "version": "11"}}}]:
            with self.subTest(manifest=manifest):
                result, values = self.successful(manifest=manifest)
                self.assertEqual(values["VERSION"], "latest")
                self.assertIn("::warning", result.stdout)

    def test_pnpm_explicit_version(self):
        _, values = self.successful(pm="pnpm@11.28.2", manifest={
            "devEngines": {"packageManager": {"name": "pnpm", "version": "9.0.0"}}})
        self.assertEqual(values["VERSION"], "11.28.2")

    def test_other_package_managers(self):
        for pm, lockfile, version in [
            ("npm", "package-lock.json", "latest"), ("yarn", "yarn.lock", ""),
            ("bun", "bun.lock", "latest"), ("bun", "bun.lockb", "latest"),
            ("deno", "deno.lock", "vx.x.x"),
        ]:
            for override in ["", pm]:
                with self.subTest(pm=pm, override=override, lockfile=lockfile):
                    _, values = self.successful(files=[lockfile], pm=override)
                    self.assertEqual(values["PACKAGE_MANAGER"], pm)
                    self.assertEqual(values["LOCKFILE"], lockfile)
                    self.assertEqual(values["VERSION"], version)
                    cached = pm in ["npm", "pnpm", "yarn"]
                    self.assertEqual(values["dependency-cache"], pm if cached else "")
                    if cached:
                        self.assertTrue(values["dependency-cache-path"].endswith("/" + lockfile))
                    else:
                        self.assertEqual(values["dependency-cache-path"], "")

    def test_explicit_manager_without_lockfile(self):
        for pm in ["pnpm@11.28.2", "npm", "yarn", "bun", "deno"]:
            with self.subTest(pm=pm):
                _, values = self.successful(files=[], pm=pm)
                self.assertEqual(values["PACKAGE_MANAGER"], pm.split("@")[0])
                self.assertEqual(values["LOCKFILE"], "")
                self.assertEqual(values["dependency-cache"], "")
                self.assertEqual(values["dependency-cache-path"], "")

    def test_npm_shrinkwrap_and_precedence(self):
        for files in [["npm-shrinkwrap.json"], ["package-lock.json", "npm-shrinkwrap.json"]]:
            for pm in ["", "npm"]:
                with self.subTest(files=files, pm=pm):
                    _, values = self.successful(files=files, pm=pm)
                    self.assertEqual(values["PACKAGE_MANAGER"], "npm")
                    self.assertEqual(values["LOCKFILE"], "npm-shrinkwrap.json")
                    self.assertEqual(values["dependency-cache"], "npm")
                    self.assertTrue(values["dependency-cache-path"].endswith("/npm-shrinkwrap.json"))

    def test_cache_only_uses_selected_managers_lockfile(self):
        _, values = self.successful(files=["pnpm-lock.yaml"], pm="npm")
        self.assertEqual(values["dependency-cache"], "")
        self.assertEqual(values["dependency-cache-path"], "")

    def test_missing_lockfile_and_invalid_manager_fail(self):
        for kwargs in [{"files": []}, {"pm": "invalid"}]:
            with self.subTest(kwargs=kwargs):
                result, _ = self.resolve(**kwargs)
                self.assertNotEqual(result.returncode, 0)

    def test_malformed_manifest_fails(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "package.json").write_text("{broken")
            result = subprocess.run([NODE, "-e", SCRIPT_RUNNER],
                                    input=RESOLVER["with"]["script"], cwd=root,
                                    env={**os.environ, "GITHUB_WORKSPACE": root, "PROJECT_PATH": ".",
                                         "INPUT_NODE": "", "INPUT_PM": ""},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("JSON", result.stderr)

    def test_inherited_node_and_fallback(self):
        for parent, expected in [({"volta": {"node": "22.x"}}, "22.x"),
                                 ({"engines": {"node": "22.x"}}, "22.x"),
                                 ({"volta": {"yarn": "1.22.22"}}, "24"),
                                 ({"engines": {"node": ""}}, "24")]:
            with self.subTest(parent=parent):
                _, values = self.successful(
                    path="sites/my site", manifest={"volta": {"extends": "../../package.json"}},
                    inherited={"package.json": parent})
                self.assertEqual(values["node-version"], expected)

    def test_inheritance_chain_and_precedence(self):
        _, values = self.successful(
            path="sites/my site", manifest={"volta": {"extends": "../package.json"}},
            inherited={"sites/package.json": {"volta": {"extends": "../package.json"}},
                       "package.json": {"engines": {"node": "22.x"}}})
        self.assertEqual(values["node-version"], "22.x")
        # An explicit local declaration takes precedence over inherited config.
        for manifest, expected in [
            ({"volta": {"node": "24", "extends": "missing.json"},
              "devEngines": {"runtime": {"name": "node", "version": "22.x"}}}, "24"),
            ({"engines": {"node": "24"}, "devEngines": {
                "runtime": [{"name": "node", "version": "22.x"},
                            {"name": "node", "version": "24"}]}}, "22.x"),
            ({"engines": {"node": "22.x"}, "volta": {"extends": "missing.json"}}, "22.x"),
        ]:
            with self.subTest(manifest=manifest):
                _, values = self.successful(manifest=manifest)
                self.assertEqual(values["node-version"], expected)

    def test_broken_and_circular_inheritance_fail(self):
        for target, expected in [("missing.json", "ENOENT"), ("package.json", "Circular")]:
            with self.subTest(target=target):
                result, _ = self.resolve(manifest={"volta": {"extends": target}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_explicit_node_skips_inheritance(self):
        _, values = self.successful(node="24", manifest={"volta": {"extends": "missing.json"}})
        self.assertEqual(values["node-version"], "24")

    def test_npm_without_path_tools(self):
        # resolve() runs Node by absolute path with an empty PATH: neither jq nor
        # a system Node installation is needed by the github-script action body.
        for node in ["", "24"]:
            with self.subTest(node=node):
                _, values = self.successful(manifest={}, files=["package-lock.json"], pm="npm", node=node)
                self.assertEqual(values["node-version"], "24")
                self.assertEqual(values["PACKAGE_MANAGER"], "npm")

    def test_setup_actions_receive_resolved_inputs(self):
        self.assertEqual(ACTION["inputs"]["node-version"]["default"], "")
        for step in ACTION["runs"]["steps"]:
            if step.get("uses", "").startswith("actions/setup-node@"):
                self.assertEqual(step["with"]["node-version"], "${{ steps.toolchain.outputs.node-version }}")
                self.assertIs(step["with"]["package-manager-cache"], False)
                if step["name"] == "Setup Node":
                    self.assertEqual(step["with"]["cache"], "${{ steps.toolchain.outputs.dependency-cache }}")
                    self.assertEqual(step["with"]["cache-dependency-path"], "${{ steps.toolchain.outputs.dependency-cache-path }}")
            if step.get("uses", "").startswith("pnpm/action-setup@"):
                self.assertEqual(step["with"]["version"], "${{ env.VERSION }}")
                self.assertEqual(step["with"]["package_json_file"], "${{ inputs.path }}/package.json")


if __name__ == "__main__":
    unittest.main()
