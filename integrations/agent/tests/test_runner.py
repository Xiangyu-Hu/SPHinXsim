"""Execution admission, not a GPU/solver acceptance test."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from sphinxsim_agent.runner import main

class ExecutionAdmissionTests(unittest.TestCase):
    def test_retired_template_request_is_rejected_before_solver_start(self):
        with tempfile.TemporaryDirectory() as root:
            request = Path(root) / "request.json"
            request.write_text(json.dumps({"config":{"scenario":"dambreak"},"kind":"run"}))
            with patch.object(sys, "argv", ["runner", str(request)]):
                with self.assertRaisesRegex(ValueError, "Only frozen custom projects"):
                    main()
            self.assertFalse((Path(root) / "solver").exists())

if __name__ == "__main__":
    unittest.main()
