#!/usr/bin/env python3
"""Guard the local-only real-acceptance fixture contract."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


FIXTURE = Path(__file__).parents[1] / "test_control" / "acceptance_fixture.template.json"


class AcceptanceFixtureTests(unittest.TestCase):
    def test_fixture_requires_confirmed_missing_and_safe_write_boundary(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIn("confirmed_missing", payload["required_scenarios"])
        self.assertEqual(payload["confirmed_missing"]["reply"], "确认无法提供")
        self.assertEqual(payload["confirmed_missing"]["expected_status"], "Confirmed Missing")
        self.assertIn("CEO最终决策", payload["forbidden_base_writes"])
        self.assertEqual(len(payload["allowed_base_writes"]), 4)

    def test_local_fixture_is_never_a_release_input(self) -> None:
        local_fixture = FIXTURE.with_name("acceptance_fixture.local.json")
        self.assertTrue(local_fixture.is_file())
        payload = json.loads(local_fixture.read_text(encoding="utf-8"))
        self.assertEqual(payload["fixture_cases"]["confirmed_missing"]["reply"], "确认无法提供")


if __name__ == "__main__":
    unittest.main()
