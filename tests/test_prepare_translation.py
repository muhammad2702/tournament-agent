"""Offline checks against the real saved handoff and probe requests."""

import json
from decimal import Decimal
from pathlib import Path
import unittest

from tournament_agent.model_call import translation_request
from tournament_agent.models import RequestBudget, StatementSource
from tournament_agent.model_call import prepare_translation
from tournament_agent.model_call import ModelGateway
from tournament_agent.model_call import review_request


class TranslationPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Path("data")
        cls.ledger = (cls.data / "model_calls.json").read_bytes()
        cls.report = prepare_translation(cls.data)

    def test_prepares_all_four_sets_with_verified_pdf_corrections(self):
        self.assertEqual(self.report["errors"], [])
        self.assertEqual(self.report["model_calls"], 0)
        sets = {(item["age_group"], item["difficulty"]): item for item in self.report["sets"]}
        self.assertEqual({key: item["question_count"] for key, item in sets.items()}, {
            ("junior", "basic"): 5, ("senior", "basic"): 5,
            ("junior", "advanced"): 7, ("senior", "advanced"): 7,
        })
        rows = [row for item in sets.values() for row in item["statements"]]
        self.assertEqual(sum("translation_request" in row for row in rows), 24)
        self.assertEqual({row["source"]["id"] for row in rows if row["status"] == "review_passed"}, {"turnir31otm:q1"})
        note = sets["senior", "basic"]["statements"][-1]
        self.assertEqual(note["source"]["id"], "turnir31ots:note")
        self.assertIsNone(note["number"])
        junior_q5 = sets["junior", "advanced"]["statements"][4]
        self.assertEqual([(score["subpart"], score["value"]) for score in junior_q5["scores"]], [("а)", 2), ("б)", 7)])
        self.assertIn("\nа) это можно сделать", junior_q5["source"]["text"])
        senior_q4 = StatementSource(**sets["senior", "advanced"]["statements"][3]["source"])
        self.assertIn(r"\underbrace{11\ldots 11}_{n\text{ единиц}}", senior_q4.text)
        review_payload = json.loads(review_request(senior_q4, "").messages[1]["content"])
        self.assertEqual(review_payload["pdf_text_extraction"], senior_q4.evidence)
        self.assertEqual(review_payload["extracted_source_statement"], senior_q4.text)
        self.assertEqual((self.data / "model_calls.json").read_bytes(), self.ledger)

    def test_probe_request_cache_keys_match_saved_calls(self):
        row = next(row for item in self.report["sets"] for row in item["statements"] if row["status"] == "review_passed")
        source = StatementSource(**row["source"])
        requests = (translation_request(source, probe=True), review_request(source, row["english"]))
        calls = {call["id"]: call for call in json.loads(self.ledger)["calls"]}
        for request, call_id in zip(requests, (row["translation_call_id"], row["review_call_id"]), strict=True):
            call = calls[call_id]
            budget = RequestBudget(call["input_token_bound"], Decimal(call["input_usd_per_million"]),
                                   Decimal(call["output_usd_per_million"]), call["price_reference"],
                                   Decimal(call["extra_cost_usd"]))
            _, cache_key, _ = ModelGateway._request(request, budget, "live")
            self.assertEqual(cache_key, call["cache_key"])
