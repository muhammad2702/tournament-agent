"""Prepare and validate real translations, including the saved one-question probe."""

import hashlib
import json
from pathlib import Path
import re

from .models import ModelRequest, StatementSource
from .paths import saved_path
from .translator import ModelGateway, OpenRouterClient, prepare_text_budget


MODEL = "google/gemini-2.5-flash-lite"


def run_live_api_test(data_dir: Path, *, allow_paid: bool = False) -> dict:
    if not allow_paid:
        raise ValueError("A real API test requires explicit paid-call approval")
    data_dir = Path(data_dir)
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    extraction_path = saved_path(manifest["html_extraction"]["path"])
    if hashlib.sha256(extraction_path.read_bytes()).hexdigest() != manifest["html_extraction"]["sha256"]:
        raise ValueError("HTML extraction report hash mismatch")
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    selected = next(((item, question) for item in extraction["sets"] if item["status"] == "extracted"
                     for question in item["questions"] if not question["issues"] and not question["images"]), None)
    if selected is None:
        raise ValueError("No clean text-only extracted question is available for this API test")
    source_set, question = selected
    section = saved_path(source_set["section_html_path"]).read_bytes()
    if hashlib.sha256(section).hexdigest() != source_set["section_sha256"]:
        raise ValueError("Source section changed since extraction")

    source = StatementSource(question["id"], source_set["section_sha256"], "html", "ru",
                             question["text"], question["original_html"])
    report = {
        "test": "real_openrouter_translation",
        "source_url": source_set["source"]["page_url"],
        "anchor": source_set["source"]["anchor"],
        **translate_statement(data_dir, source, allow_paid=allow_paid, probe=True),
        "qa_note": "Real API/accounting and basic content checks only; bilingual and visual review not yet performed.",
    }
    from .pipeline import _save
    _save(data_dir / "live_api_test.json", report)
    return report


def translation_request(source: StatementSource, *, probe: bool = False) -> ModelRequest:
    prompts = Path(__file__).resolve().parent.parent / "prompts"
    prompt = (prompts / "translate_probe.md").read_text(encoding="utf-8")
    version = "translate-probe"
    if not probe:
        prompt += "\n" + (prompts / "translate_set.md").read_text(encoding="utf-8")
        version = "translate-set"
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    schema = {"type": "object", "properties": {
        "problem_id": {"type": "string", "enum": [source.id]},
        "source_language": {"type": "string"},
        "english": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}}},
        "required": ["problem_id", "source_language", "english", "issues"], "additionalProperties": False}
    messages = ({"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({"problem_id": source.id, "source": source.text}, ensure_ascii=False)})
    return ModelRequest("translate", source.source_sha256, version + ":" + prompt_sha,
                        MODEL, messages, 512, schema)


def translate_statement(data_dir: Path, source: StatementSource, *, allow_paid: bool = False,
                        probe: bool = False, gateway: ModelGateway | None = None) -> dict:
    if not allow_paid:
        raise ValueError("Translation requires explicit paid-call approval")
    request = translation_request(source, probe=probe)
    if gateway is None:
        gateway = ModelGateway(data_dir, client=OpenRouterClient(allow_live=True))
    budget, info, bound = prepare_text_budget(gateway.client, request)
    result = gateway.call(request, budget)
    translated = json.loads(result.content)
    expected_fields = {"problem_id", "source_language", "english", "issues"}
    errors = []
    if not isinstance(translated, dict) or set(translated) != expected_fields:
        errors.append("Response fields do not match the requested schema")
    elif (translated["problem_id"] != source.id or translated["source_language"] != source.language
          or not isinstance(translated["english"], str) or not translated["english"].strip()
          or not isinstance(translated["issues"], list) or any(not isinstance(issue, str) for issue in translated["issues"])):
        errors.append("Response identity, language or field types are invalid")
    else:
        if sorted(re.findall(r"\d+", source.text)) != sorted(re.findall(r"\d+", translated["english"])):
            errors.append("Numeric values differ from the original question")
        if translated["issues"]:
            errors.append("Model reported source/translation uncertainties")
    return {"status": "failed" if errors else "passed_basic_checks",
            "source_sha256": source.source_sha256,
            "problem_id": source.id, "source_text": source.text, "translation": translated,
            "model": result.model, "call_id": result.call_id, "provider_id": result.provider_id,
            "new_cost_usd": str(result.cost_usd), "from_cache": result.from_cache,
            "reserved_bound_usd": str(bound), "errors": errors,
            "pricing": {"canonical_slug": info.get("canonical_slug"),
                        "input_per_million_usd": str(budget.input_usd_per_million),
                        "output_per_million_usd": str(budget.output_usd_per_million)}}
