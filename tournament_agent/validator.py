"""Evidence-backed bilingual review through the real cost-controlled gateway."""

import hashlib
import json
from pathlib import Path
import re

from .models import ModelRequest, StatementSource
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

    source = StatementSource(question["id"], source_set["section_sha256"], "html", "ru",
                             question["text"], question["original_html"])
    ledger = read_verified_ledger(data_dir)
    verify_recorded_response(ledger, translation["call_id"], "translate", translation["translation"])
    report = {
        **review_statement(data_dir, source, translation["translation"]["english"], allow_paid=allow_paid),
        "translation_report_sha256": hashlib.sha256(translation_path.read_bytes()).hexdigest(),
        "translation_call_id": translation["call_id"],
        "qa_note": "One text-only problem reviewed against its original HTML. Model review is not a correctness guarantee; final PDF generation and visual inspection are pending.",
    }
    from .pipeline import _save
    _save(data_dir / "live_api_review.json", report)
    return report


def read_verified_ledger(data_dir: Path) -> dict:
    ModelGateway(data_dir).summary()
    return json.loads((data_dir / "model_calls.json").read_text(encoding="utf-8"))


def verify_recorded_response(ledger: dict, call_id: str, purpose: str, response: dict) -> None:
    call = next((item for item in ledger["calls"] if item["id"] == call_id), None)
    if (call is None or call["status"] != "completed" or call["purpose"] != purpose
            or json.loads(call["result"]["content"]) != response):
        raise ValueError(f"Saved {purpose} does not match its recorded model response")


def review_request(source: StatementSource, english: str) -> ModelRequest:
    prompts = Path(__file__).resolve().parent.parent / "prompts"
    prompt = (prompts / "review_probe.md").read_text(encoding="utf-8")
    version = "review-probe"
    evidence_field = "original_question_html"
    if source.kind == "pdf":
        prompt += "\n" + (prompts / "review_pdf.md").read_text(encoding="utf-8")
        version = "review-pdf"
        evidence_field = "pdf_text_extraction"
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    string = {"type": "string"}
    evidence = {"source_evidence": string, "translation_evidence": string, "explanation": string}
    check_schema = {"type": "object", "properties": {"name": {"type": "string", "enum": sorted(CHECKS)},
        "verdict": {"type": "string", "enum": ["pass", "fail", "uncertain"]}, **evidence},
        "required": ["name", "verdict", *evidence], "additionalProperties": False}
    issue_schema = {"type": "object", "properties": {"kind": string, **evidence, "suggested_fix": string},
                    "required": ["kind", *evidence, "suggested_fix"], "additionalProperties": False}
    schema = {"type": "object", "properties": {"problem_id": {"type": "string", "enum": [source.id]},
        "verdict": {"type": "string", "enum": ["pass", "fail", "uncertain"]},
        "checks": {"type": "array", "items": check_schema}, "issues": {"type": "array", "items": issue_schema}},
        "required": ["problem_id", "verdict", "checks", "issues"], "additionalProperties": False}
    original = {"problem_id": source.id, evidence_field: source.evidence,
                "extracted_source_statement": source.text, "english_translation": english}
    return ModelRequest("review", source.source_sha256, version + ":" + prompt_sha,
                        REVIEW_MODEL, ({"role": "system", "content": prompt},
                                       {"role": "user", "content": json.dumps(original, ensure_ascii=False)}), 2048, schema)


def review_statement(data_dir: Path, source: StatementSource, english: str, *, allow_paid: bool = False,
                     gateway: ModelGateway | None = None) -> dict:
    if not allow_paid:
        raise ValueError("Real bilingual review requires explicit paid-call approval")
    if not english.strip():
        raise ValueError("Bilingual review requires a nonempty English translation")
    request = review_request(source, english)
    if gateway is None:
        gateway = ModelGateway(data_dir, client=OpenRouterClient(allow_live=True))
    budget, model_info, bound = prepare_text_budget(gateway.client, request)
    result = gateway.call(request, budget)
    evidence_fields = ("source_evidence", "translation_evidence", "explanation")
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
                if check.get("verdict") not in {"pass", "fail", "uncertain"} or any(not isinstance(check.get(key), str) or not check[key].strip() for key in evidence_fields):
                    errors.append("A check has invalid verdict or missing source/translation evidence")
        if review["problem_id"] != source.id or review["verdict"] not in {"pass", "fail", "uncertain"}:
            errors.append("Review identity or verdict invalid")
        if not isinstance(review["issues"], list) or any(not isinstance(issue, dict) or any(not isinstance(issue.get(key), str) or not issue[key].strip() for key in ("kind", *evidence_fields, "suggested_fix")) for issue in review["issues"]):
            errors.append("Invalid discrepancy records")
        if review["verdict"] == "pass" and (review["issues"] or any(check.get("verdict") != "pass" for check in valid_checks)):
            errors.append("Pass verdict contradicts the detailed findings")
    numbers_match = sorted(re.findall(r"\d+", source.text)) == sorted(re.findall(r"\d+", english))
    if not numbers_match:
        errors.append("Local numeric-preservation check failed")
    status = "invalid_review" if errors else "review_passed" if review["verdict"] == "pass" else "needs_repair" if review["verdict"] == "fail" else "needs_review"
    return {"status": status, "problem_id": source.id, "source_sha256": source.source_sha256,
            "review": review, "validation_errors": errors,
            "local_numbers_match": numbers_match, "model": result.model, "call_id": result.call_id,
            "provider_id": result.provider_id, "new_cost_usd": str(result.cost_usd), "from_cache": result.from_cache,
            "reserved_bound_usd": str(bound), "pricing_model": model_info.get("canonical_slug")}
