import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webai2api import cli


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config.json"

    def test_init_and_check(self):
        self.assertEqual(cli.main(["init", "--config", str(self.config)]), 0)
        self.assertTrue(self.config.exists())
        data = json.loads(self.config.read_text())
        self.assertTrue(data["api_keys"])
        self.assertEqual(cli.main(["check", "--config", str(self.config)]), 0)

    def test_init_force_required(self):
        self.assertEqual(cli.main(["init", "--config", str(self.config)]), 0)
        self.assertEqual(cli.main(["init", "--config", str(self.config)]), 1)
        self.assertEqual(cli.main(["init", "--config", str(self.config), "--force"]), 0)

    def test_default_command_is_run(self):
        cli.main(["init", "--config", str(self.config)])
        calls = {}

        def fake_serve(path, host=None, port=None):
            calls["path"] = path
            calls["host"] = host
            calls["port"] = port

        with mock.patch("webai2api.server.serve", fake_serve):
            code = cli.main(
                ["--config", str(self.config), "--port", "18888", "--host", "127.0.0.1"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls["port"], 18888)
        self.assertEqual(calls["host"], "127.0.0.1")
        self.assertEqual(Path(calls["path"]).resolve(), self.config.resolve())

    def test_install_and_uninstall_service(self):
        with mock.patch.dict(os.environ, {"HOME": self.tmp.name}):
            self.assertEqual(cli.main(["init", "--config", str(self.config)]), 0)
            with mock.patch.object(
                subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ):
                code = cli.main(
                    [
                        "install-service",
                        "--config",
                        str(self.config),
                        "--no-linger",
                    ]
                )
            self.assertEqual(code, 0)
            unit = Path(self.tmp.name) / ".config" / "systemd" / "user" / "webai2api.service"
            self.assertTrue(unit.exists())
            content = unit.read_text()
            self.assertIn(str(self.config), content)
            self.assertIn("ExecStart=", content)
            self.assertIn("WantedBy=default.target", content)
            with mock.patch.object(
                subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ):
                code = cli.main(["uninstall-service"])
            self.assertEqual(code, 0)
            self.assertFalse(unit.exists())

    def test_uninstall_missing_service(self):
        with mock.patch.dict(os.environ, {"HOME": self.tmp.name}):
            self.assertEqual(cli.main(["uninstall-service"]), 0)


if __name__ == "__main__":
    unittest.main()
