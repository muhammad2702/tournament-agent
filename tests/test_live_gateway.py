"""Opt-in integration test. Uses the REAL API and the production cost ledger.

Set RUN_LIVE_API_TEST=1 and OPENROUTER_API_KEY to run. This can incur a real
charge bounded by the probe's $0.01 reservation ceiling and the $5 key cap.
The normal test run skips it. There are no fake clients or responses.
"""

import os
from pathlib import Path
import unittest

from tournament_agent.live_test import run_live_api_test


@unittest.skipUnless(os.environ.get("RUN_LIVE_API_TEST") == "1", "Real API test requires explicit opt-in")
class LiveGatewayTests(unittest.TestCase):
    def test_real_translation_and_accounting(self):
        result = run_live_api_test(Path("data"), allow_paid=True)
        self.assertEqual(result["status"], "passed_basic_checks", result["errors"])
        self.assertTrue(result["provider_id"])
        self.assertTrue((Path("data") / "model_calls.json").exists())


if __name__ == "__main__":
    unittest.main()
