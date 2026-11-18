"""One small REAL API test using an extracted source question.

There are no simulated responses here. This does not claim to complete
translation QA: it checks connection, accounting, JSON shape, identity and
numeric preservation. A bilingual review remains a later approved step.
"""

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .models import ModelRequest, RequestBudget
from .paths import saved_path
from .translator import ModelGateway, OpenRouterClient


MODEL = "google/gemini-2.5-flash-lite"
MAX_TEST_RESERVATION = Decimal("0.01")


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

    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "translate_probe.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    schema = {"type": "object", "properties": {
        "problem_id": {"type": "string", "enum": [question["id"]]},
        "source_language": {"type": "string"},
        "english": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}}},
        "required": ["problem_id", "source_language", "english", "issues"], "additionalProperties": False}
    messages = ({"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({"problem_id": question["id"], "source": question["text"]}, ensure_ascii=False)})
    max_output = 512
    # This text-only probe uses a conservative byte-based bound with overhead.
    # It is not an image-token estimator or a general tokenizer replacement.
    payload_bytes = len(json.dumps({"messages": messages, "schema": schema}, ensure_ascii=False).encode("utf-8"))
    input_bound = max(4096, 2 * payload_bytes + 1024)
    client = OpenRouterClient(allow_live=True)
    client.preflight()  # Verify the real key cap before preparing a paid call.
    info = client.model_info(MODEL)
    if not {"response_format", "max_tokens", "temperature"}.issubset(info.get("supported_parameters", [])):
        raise ValueError("Selected model does not advertise the required request parameters")
    if input_bound + max_output > info["context_length"]:
        raise ValueError("Request token bounds exceed the advertised context")
    max_completion = (info.get("top_provider") or {}).get("max_completion_tokens")
    if max_completion is not None and max_output > max_completion:
        raise ValueError("Output limit exceeds the advertised model limit")
    pricing = info["pricing"]
    if Decimal(str(pricing.get("request", "0"))) != 0 or pricing.get("overrides"):
        raise ValueError("This small test does not support fixed request fees or tiered pricing")
    input_price = Decimal(str(pricing["prompt"])) * Decimal("1000000")
    output_price = Decimal(str(pricing["completion"])) * Decimal("1000000")
    bound = (Decimal(input_bound) * input_price + Decimal(max_output) * output_price) / Decimal("1000000")
    if not bound.is_finite() or bound < 0 or bound > MAX_TEST_RESERVATION:
        raise ValueError("Calculated request reservation exceeds the $0.01 API-test ceiling")
    budget = RequestBudget(input_bound, input_price, output_price, "https://openrouter.ai/api/v1/models; " + str(info.get("canonical_slug", MODEL)))
    request = ModelRequest("translate", source_set["section_sha256"], "translate-probe:" + prompt_sha,
                           MODEL, messages, max_output, schema)
    result = ModelGateway(data_dir, client=client).call(request, budget)
    translated = json.loads(result.content)
    expected_fields = {"problem_id", "source_language", "english", "issues"}
    errors = []
    if not isinstance(translated, dict) or set(translated) != expected_fields:
        errors.append("Response fields do not match the requested schema")
    elif (translated["problem_id"] != question["id"] or translated["source_language"] != "ru"
          or not isinstance(translated["english"], str) or not translated["english"].strip()
          or not isinstance(translated["issues"], list) or any(not isinstance(issue, str) for issue in translated["issues"])):
        errors.append("Response identity, language or field types are invalid")
    else:
        if sorted(re.findall(r"\d+", question["text"])) != sorted(re.findall(r"\d+", translated["english"])):
            errors.append("Numeric values differ from the original question")
        if translated["issues"]:
            errors.append("Model reported source/translation uncertainties")
    report = {"test": "real_openrouter_translation", "status": "failed" if errors else "passed_basic_checks",
              "source_url": source_set["source"]["page_url"], "anchor": source_set["source"]["anchor"],
              "problem_id": question["id"], "source_text": question["text"], "translation": translated,
              "model": result.model, "call_id": result.call_id, "provider_id": result.provider_id,
              "new_cost_usd": str(result.cost_usd), "from_cache": result.from_cache,
              "reserved_bound_usd": str(bound), "errors": errors,
              "pricing": {"canonical_slug": info.get("canonical_slug"), "input_per_million_usd": str(input_price),
                          "output_per_million_usd": str(output_price)},
              "qa_note": "Real API/accounting and basic content checks only; bilingual and visual review not yet performed."}
    # Use the existing atomic writer, keeping the key out of all report data.
    from .pipeline import _save
    _save(data_dir / "live_api_test.json", report)
    return report
