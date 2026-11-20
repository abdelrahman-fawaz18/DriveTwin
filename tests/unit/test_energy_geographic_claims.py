"""Changed source/maps and consumed geographic claims cannot launch new work."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).resolve().parents[2] / "scripts/geographic/geographic_study.py"
spec = importlib.util.spec_from_file_location("geographic_study", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GeographicClaimTests(unittest.TestCase):
    def test_changed_source_is_rejected_before_input_reading(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "candidate.json").write_text(json.dumps({"source": {"worker": "old"}}))
            with patch.object(module, "source_files", return_value={"worker": "new"}):
                with self.assertRaisesRegex(ValueError, "source"):
                    module.verify(folder)

    def test_changed_map_is_rejected_before_any_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "evidence/energy-v2/candidate"
            inputs = root / "evidence/energy-v2/input"
            folder.mkdir(parents=True)
            inputs.mkdir()
            for name in module.INPUTS:
                (inputs / name).write_text("original")
            hashes = {name: module.digest(inputs / name) for name in module.INPUTS}
            (folder / "candidate.json").write_text(
                json.dumps(
                    {
                        "source": {},
                        "inputs": [
                            {"id": "route", "folder": "evidence/energy-v2/input", "files": hashes}
                        ],
                    }
                )
            )
            (inputs / "elevated-plant.net.xml").write_text("changed")
            with (
                patch.object(module, "ROOT", root),
                patch.object(module, "source_files", return_value={}),
            ):
                with self.assertRaisesRegex(ValueError, "inputs changed"):
                    module.verify(folder)

    def test_consumed_claim_never_runs_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "claims").mkdir()
            (folder / "claims/route.json").write_text("{}")
            record = {"inputs": [{"id": "route", "folder": "unused"}]}
            with (
                patch.object(module, "verify", return_value=record),
                patch.object(module.subprocess, "run") as run,
            ):
                with self.assertRaisesRegex(ValueError, "already claimed"):
                    module.evaluate(folder, [1])
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
