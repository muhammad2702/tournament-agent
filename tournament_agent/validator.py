"""Evidence-backed bilingual review through the real cost-controlled gateway.

This first review covers the saved text-only translation probe. It does not
mark a full set/PDF accepted or perform visual layout validation.
"""

import hashlib
import json
from pathlib import Path
import re

from .models import ModelRequest
from .paths import saved_path
from .translator import ModelGateway, OpenRouterClient, prepare_text_budget


REVIEW_MODEL = "google/gemini-2.5-flash"
CHECKS = {"task", "conditions", "quantifiers", "numbers", "notation", "completeness"}


def review_saved_translation(data_dir: Path, *, allow_paid: bool = False) -> dict:
    if not allow_paid:
        raise ValueError("Real bilingual review requires explicit paid-call approval")
    data_dir = Path(data_dir)
    translation_path = data_dir / "live_api_test.json"
    translation = json.loads(translation_path.read_text(encoding="utf-8"))
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    extraction_path = saved_path(manifest["html_extraction"]["path"])
    if hashlib.sha256(extraction_path.read_bytes()).hexdigest() != manifest["html_extraction"]["sha256"]:
        raise ValueError("Extraction report hash mismatch")
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    matches = [(item, question) for item in extraction["sets"] for question in item["questions"]
               if question["id"] == translation["problem_id"]
               and item["source"]["page_url"] == translation["source_url"]
               and item["source"]["anchor"] == translation["anchor"]]
    if len(matches) != 1:
        raise ValueError("Translation does not identify exactly one source question")
    source_set, question = matches[0]
    fragment = saved_path(source_set["section_html_path"]).read_bytes().decode("utf-8")
    if hashlib.sha256(fragment.encode()).hexdigest() != source_set["section_sha256"]:
        raise ValueError("Source section hash mismatch")
    if fragment[question["source_start"]:question["source_end"]] != question["original_html"]:
        raise ValueError("Question no longer matches the source section")
    if question["text"] != translation["source_text"]:
        raise ValueError("Translation report contains a different source statement")

    client = OpenRouterClient(allow_live=True)
    gateway = ModelGateway(data_dir, client=client)
    gateway.summary()  # Validate ledger and response-cache integrity locally.
    ledger = json.loads((data_dir / "model_calls.json").read_text(encoding="utf-8"))
    call = next((item for item in ledger["calls"] if item["id"] == translation["call_id"]), None)
    if call is None or call["status"] != "completed" or json.loads(call["result"]["content"]) != translation["translation"]:
        raise ValueError("Saved translation does not match its recorded model response")
    prompt = (Path(__file__).resolve().parent.parent / "prompts" / "review_probe.md").read_text(encoding="utf-8")
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    string = {"type": "string"}
    evidence = {"source_evidence": string, "translation_evidence": string, "explanation": string}
    check_schema = {"type": "object", "properties": {"name": {"type": "string", "enum": sorted(CHECKS)},
        "verdict": {"type": "string", "enum": ["pass", "fail", "uncertain"]}, **evidence},
        "required": ["name", "verdict", *evidence], "additionalProperties": False}
    issue_schema = {"type": "object", "properties": {"kind": string, **evidence, "suggested_fix": string},
                    "required": ["kind", *evidence, "suggested_fix"], "additionalProperties": False}
    schema = {"type": "object", "properties": {"problem_id": {"type": "string", "enum": [question["id"]]},
        "verdict": {"type": "string", "enum": ["pass", "fail", "uncertain"]},
        "checks": {"type": "array", "items": check_schema}, "issues": {"type": "array", "items": issue_schema}},
        "required": ["problem_id", "verdict", "checks", "issues"], "additionalProperties": False}
    original = {"problem_id": question["id"], "original_question_html": question["original_html"],
                "extracted_source_statement": question["text"], "english_translation": translation["translation"]["english"]}
    request = ModelRequest("review", source_set["section_sha256"], "review-probe:" + prompt_sha,
                           REVIEW_MODEL, ({"role": "system", "content": prompt},
                                          {"role": "user", "content": json.dumps(original, ensure_ascii=False)}), 2048, schema)
    budget, model_info, bound = prepare_text_budget(client, request)
    result = gateway.call(request, budget)
    review = json.loads(result.content)
    errors = []
    if not isinstance(review, dict) or set(review) != {"problem_id", "verdict", "checks", "issues"}:
        errors.append("Reviewer response fields do not match schema")
    else:
        checks = review["checks"]
        valid_checks = checks if isinstance(checks, list) and all(isinstance(check, dict) for check in checks) else []
        if not isinstance(checks, list) or len(checks) != len(CHECKS) or any(not isinstance(check, dict) for check in checks):
            errors.append("Expected six check objects")
        elif {check.get("name") for check in checks} != CHECKS:
            errors.append("Missing or duplicate review check names")
        else:
            for check in checks:
                if check.get("verdict") not in {"pass", "fail", "uncertain"} or any(not isinstance(check.get(key), str) or not check[key].strip() for key in evidence):
                    errors.append("A check has invalid verdict or missing source/translation evidence")
        if review["problem_id"] != question["id"] or review["verdict"] not in {"pass", "fail", "uncertain"}:
            errors.append("Review identity or verdict invalid")
        if not isinstance(review["issues"], list) or any(not isinstance(issue, dict) or any(not isinstance(issue.get(key), str) or not issue[key].strip() for key in ("kind", *evidence, "suggested_fix")) for issue in review["issues"]):
            errors.append("Invalid discrepancy records")
        if review["verdict"] == "pass" and (review["issues"] or any(check.get("verdict") != "pass" for check in valid_checks)):
            errors.append("Pass verdict contradicts the detailed findings")
    numbers_match = sorted(re.findall(r"\d+", question["text"])) == sorted(re.findall(r"\d+", translation["translation"]["english"]))
    if not numbers_match:
        errors.append("Local numeric-preservation check failed")
    status = "invalid_review" if errors else "review_passed" if review["verdict"] == "pass" else "needs_repair" if review["verdict"] == "fail" else "needs_review"
    report = {"status": status, "problem_id": question["id"], "source_sha256": source_set["section_sha256"],
              "translation_report_sha256": hashlib.sha256(translation_path.read_bytes()).hexdigest(),
              "translation_call_id": translation["call_id"], "review": review, "validation_errors": errors,
              "local_numbers_match": numbers_match, "model": result.model, "call_id": result.call_id,
              "provider_id": result.provider_id, "new_cost_usd": str(result.cost_usd), "from_cache": result.from_cache,
              "reserved_bound_usd": str(bound), "pricing_model": model_info.get("canonical_slug"),
              "qa_note": "One text-only problem reviewed against its original HTML. Model review is not a correctness guarantee; final PDF generation and visual inspection are pending."}
    from .pipeline import _save
    _save(data_dir / "live_api_review.json", report)
    return report
