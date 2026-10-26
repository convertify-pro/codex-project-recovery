import contextlib
import io
import sys
import unittest
from unittest.mock import patch
import one_click
import project_recovery as recovery
import test_recovery


class OneClickTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery.RecoveryTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_waits_and_explains_then_continues(self):
        opened = recovery.RecoveryError("Fully quit ChatGPT/Codex")
        output = io.StringIO()
        with patch.object(recovery, "require_closed", side_effect=[opened, opened, None]) as check:
            with patch.object(one_click.time, "sleep"), contextlib.redirect_stdout(output):
                one_click.wait_until_closed()
        self.assertEqual(check.call_count, 3)
        self.assertIn("PLEASE CLOSE CODEX", output.getvalue())
        self.assertIn("continue automatically", output.getvalue())
        self.assertIn("Codex is closed", output.getvalue())

    def test_automatic_repair_needs_no_typed_confirmation(self):
        with patch.object(recovery, "require_closed", return_value=None):
            with patch("builtins.input", side_effect=AssertionError("Unexpected setup prompt")):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(one_click.run(self.fixture.home, self.fixture.data), 0)
        self.assertIn("SUCCESS - RECOVERY COMPLETE", output.getvalue())
        state = recovery.read_json(self.fixture.state_path)
        self.assertEqual(len(state["local-projects"]), 3)
        self.assertIn("folder", state["thread-project-assignments"])
        self.assertNotIn("standalone", state["thread-project-assignments"])
        self.assertTrue(list((self.fixture.data / "backups").glob("*/undo.json")))

    def test_repeat_run_is_noop_and_saves_snapshot(self):
        with patch.object(recovery, "require_closed", return_value=None):
            with contextlib.redirect_stdout(io.StringIO()):
                one_click.run(self.fixture.home, self.fixture.data)
            before = self.fixture.state_path.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                one_click.run(self.fixture.home, self.fixture.data)
        self.assertIn("NO REPAIR NEEDED", output.getvalue())
        self.assertEqual(before, self.fixture.state_path.read_bytes())
        self.assertEqual(len(list((self.fixture.data / "snapshots").glob("*.json"))), 2)

    def test_diagnosis_never_requires_app_shutdown(self):
        with patch.object(recovery, "require_closed", side_effect=AssertionError("Must not wait")):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(one_click.run(self.fixture.home, self.fixture.data, diagnose=True), 0)
        self.assertEqual(recovery.read_json(self.fixture.state_path), self.fixture.state)

    def test_bundled_runtime_is_isolated(self):
        self.assertEqual(sys.flags.isolated, 1)
        self.assertFalse(any("site-packages" in path for path in sys.path))


if __name__ == "__main__":
    unittest.main()
