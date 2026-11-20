"""Small real Linux integration suite, executed through the development launcher."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv


class LinuxEnvironmentTests(unittest.TestCase):
    def test_regular_linux_account(self):
        self.assertEqual(sys.platform, "linux")
        self.assertNotEqual(os.getuid(), 0)

    def test_native_node_and_python(self):
        self.assertFalse(str(Path(sys.executable).resolve()).startswith("/mnt/"))
        node = shutil.which("node")
        self.assertIsNotNone(node)
        self.assertFalse(str(Path(node).resolve()).startswith("/mnt/"))
        result = subprocess.run(
            [node, "--print", "process.platform"], capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout.strip(), "linux")

    def test_python_dependency_isolation(self):
        cache = Path.home() / ".cache" / "ev-dt" / "p0"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="launcher-venv-", dir=cache) as directory:
            venv.EnvBuilder(with_pip=True).create(directory)
            result = subprocess.run(
                [str(Path(directory) / "bin/python"), "-c",
                 "import sys; assert sys.prefix != sys.base_prefix; import pip"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
