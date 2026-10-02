"""Execute the action's actual resolver. Run: python -m unittest discover -s tests."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ACTION = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text())
RESOLVER = next(s for s in ACTION["runs"]["steps"] if s.get("id") == "toolchain")


class ToolchainTests(unittest.TestCase):
    def resolve(self, manifest=None, files=("pnpm-lock.yaml",), pm="", node="", path="."):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / path
            project.mkdir(parents=True, exist_ok=True)
            if manifest is not None:
                (project / "package.json").write_text(json.dumps(manifest))
            for name in files:
                (project / name).write_text("")
            env_file = Path(root) / "env"
            output_file = Path(root) / "output"
            env_file.touch()
            output_file.touch()
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", RESOLVER["run"]],
                cwd=project,
                env={**os.environ, "INPUT_PM": pm, "INPUT_NODE": node, "PROJECT_PATH": path,
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

    def test_node_declarations_delegate_to_setup_node(self):
        manifests = [
            {"devEngines": {"runtime": {"name": "node", "version": "22.x"}}},
            {"devEngines": {"runtime": [{"name": "bun", "version": "1"},
                                       {"name": "NODE", "version": ">=22 <23"}]}},
            {"engines": {"node": "22.x"}},
            {"volta": {"node": "22.14.0"}},
            {"volta": {"extends": "../package.json"}},
        ]
        for manifest in manifests:
            with self.subTest(manifest=manifest):
                _, values = self.successful(manifest=manifest, path="sites/my site")
                self.assertEqual(values["node-version"], "")
                self.assertEqual(values["node-version-file"], "sites/my site/package.json")

    def test_node_fallback(self):
        for manifest in [None, {}, {"devEngines": {}},
                         {"devEngines": {"runtime": {"name": "node"}}},
                         {"devEngines": {"runtime": {"name": "bun", "version": "1"}}},
                         {"devEngines": {"runtime": []}}]:
            with self.subTest(manifest=manifest):
                _, values = self.successful(manifest=manifest)
                self.assertEqual(values["node-version"], "24")
                self.assertEqual(values["node-version-file"], "")

    def test_node_override(self):
        _, values = self.successful(node="24.1.0", manifest={
            "devEngines": {"runtime": {"name": "node", "version": "22.x"}}})
        self.assertEqual(values["node-version"], "24.1.0")
        self.assertEqual(values["node-version-file"], "")

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

    def test_explicit_manager_without_lockfile(self):
        _, values = self.successful(files=[], pm="pnpm@11.28.2")
        self.assertEqual(values["PACKAGE_MANAGER"], "pnpm")

    def test_missing_lockfile_and_invalid_manager_fail(self):
        for kwargs in [{"files": []}, {"pm": "invalid"}]:
            with self.subTest(kwargs=kwargs):
                result, _ = self.resolve(**kwargs)
                self.assertNotEqual(result.returncode, 0)

    def test_malformed_manifest_fails(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "package.json").write_text("{broken")
            result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", RESOLVER["run"]],
                                    cwd=root, env={**os.environ, "INPUT_NODE": "", "INPUT_PM": ""},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("parse error", result.stderr)

    def test_setup_actions_receive_resolved_inputs(self):
        self.assertEqual(ACTION["inputs"]["node-version"]["default"], "")
        for step in ACTION["runs"]["steps"]:
            if step.get("uses", "").startswith("actions/setup-node@"):
                self.assertEqual(step["with"]["node-version"], "${{ steps.toolchain.outputs.node-version }}")
                self.assertEqual(step["with"]["node-version-file"], "${{ steps.toolchain.outputs.node-version-file }}")
            if step.get("uses", "").startswith("pnpm/action-setup@"):
                self.assertEqual(step["with"]["version"], "${{ env.VERSION }}")
                self.assertEqual(step["with"]["package_json_file"], "${{ inputs.path }}/package.json")


if __name__ == "__main__":
    unittest.main()
