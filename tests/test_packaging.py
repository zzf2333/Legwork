"""验证资源随包分发、非源码目录可运行，以及示例配置可用。"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib import resources
from pathlib import Path

import support

REQUIRED_RESOURCES = (
    "resources/schema/model_result.schema.json",
    "resources/js/ego_page_state.js",
    "resources/js/ego_click_guard.js",
    "resources/js/ego_collect.js",
    "resources/js/ego_search.js",
    "resources/js/ego_chat_locate.js",
    "resources/skills/legwork-configure/SKILL.md",
    "resources/skills/legwork-run/SKILL.md",
)


class ResourceTest(unittest.TestCase):
    def test_required_resources_load_from_package(self):
        root = resources.files("legwork")
        for relative in REQUIRED_RESOURCES:
            text = root.joinpath(relative).read_text(encoding="utf-8")
            self.assertTrue(text.strip(), relative)
        schema = json.loads(root.joinpath(REQUIRED_RESOURCES[0]).read_text(encoding="utf-8"))
        self.assertEqual(schema["title"], "BossAssistantMatchBatch")

    def test_pyproject_declares_resource_globs(self):
        text = (support.REPO / "pyproject.toml").read_text(encoding="utf-8")
        for pattern in ("resources/schema/*.json", "resources/js/*.js", "resources/skills/*/SKILL.md"):
            self.assertIn(pattern, text)
        self.assertIn("legwork = \"legwork.cli:main\"", text)

    def test_js_resources_parse(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("缺少 node，跳过 JS 语法检查")
        root = resources.files("legwork")
        with tempfile.TemporaryDirectory() as tmp:
            for relative in REQUIRED_RESOURCES:
                if not relative.endswith(".js"):
                    continue
                target = Path(tmp) / Path(relative).name
                target.write_text(root.joinpath(relative).read_text(encoding="utf-8"), encoding="utf-8")
                proc = subprocess.run(
                    [node, "--check", str(target)], capture_output=True, text=True, check=False
                )
                self.assertEqual(proc.returncode, 0, f"{relative}: {proc.stderr}")


class InstalledLayoutTest(unittest.TestCase):
    def test_cli_runs_from_copied_package_outside_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(
                support.REPO / "legwork",
                root / "site" / "legwork",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            cwd = root / "elsewhere"
            cwd.mkdir()
            env = dict(support.cli_env())
            env["PYTHONPATH"] = str(root / "site")
            assets = subprocess.run(
                [sys.executable, "-m", "legwork", "assets"],
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(assets.returncode, 0, assets.stderr)
            payload = json.loads(assets.stdout)
            for path in [payload["schema"], *payload["js"], *payload["skills"].values()]:
                self.assertTrue(Path(path).is_file(), path)
                self.assertIn(str(root / "site"), path)
            checked = subprocess.run(
                [sys.executable, "-m", "legwork", "config", "check", "--config",
                 str(support.REPO / "examples" / "config.example.json")],
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_example_config_is_valid(self):
        proc = support.run_cli(
            support.REPO, support.REPO / ".legwork-test",
            "config", "check", "--config", str(support.REPO / "examples" / "config.example.json"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["live"])


if __name__ == "__main__":
    unittest.main()
