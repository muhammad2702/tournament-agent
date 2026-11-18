"""One controlled gateway for translation, review, transcription and repair.

No requests happen on import or construction. Live access is disabled by
default. This module has no automatic retries and no translation prompts yet.
One JSON file is both the cost ledger and the complete-response cache, making
settlement/cache writes atomic. Only real OpenRouter clients are supported.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4
from .models import ModelRequest, ModelResult, RequestBudget
from .models import ModelRequest, StatementSource
from .models import saved_path
from dataclasses import asdict
from hashlib import sha256
from .webpage_extractor import _statement_text
from .models import StatementSource
import subprocess
from .archive_builder import _save





HARD_CAP = Decimal("5.00")
LOCAL_CEILING = Decimal("4.50")
SMALL_TEXT_REQUEST_CEILING = Decimal("0.01")
UNRESOLVED = {"pending", "uncertain"}


class GatewayError(RuntimeError):
    pass


class BudgetExceeded(GatewayError):
    pass


class LiveNotApproved(GatewayError):
    pass


class AccountingUncertain(GatewayError):
    pass


class InvalidModelResponse(GatewayError):
    pass


class LedgerError(GatewayError):
    pass


class GatewayBusy(GatewayError):
    pass


def _money(value) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing or invalid monetary value")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid monetary value") from None
    if not result.is_finite() or result < 0:
        raise ValueError("Money must be finite and nonnegative")
    return result


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class OpenRouterClient:
    """Official HTTPS endpoints only; secrets are never persisted or printed."""
    mode = "live"

    def __init__(self, *, allow_live: bool = False, timeout: float = 60):
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        self.allow_live = allow_live
        self.timeout = timeout

    def assert_ready(self):
        if not self.allow_live:
            raise LiveNotApproved("Live model access is disabled; explicit approval is required")
        if not os.environ.get("OPENROUTER_API_KEY", "").strip():
            raise LiveNotApproved("Set OPENROUTER_API_KEY in the process environment before a live call")

    def _json(self, path: str, payload: dict | None = None) -> dict:
        self.assert_ready()
        key = os.environ["OPENROUTER_API_KEY"].strip()
        data = _canonical(payload).encode("utf-8") if payload is not None else None
        if data is not None and key.encode() in data:
            raise GatewayError("Request contains the API secret; refused")
        request = Request("https://openrouter.ai/api/v1/" + path, data=data,
                          headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read(), parse_float=Decimal)
            if not isinstance(result, dict):
                raise ValueError("Expected a JSON object")
            return result
        except HTTPError as exc:
            # Do not retain the provider body, authorization header or raw exception.
            raise GatewayError(f"OpenRouter HTTP {exc.code}") from None
        except Exception:
            raise GatewayError("OpenRouter request failed or returned invalid JSON") from None

    def preflight(self) -> dict:
        data = self._json("key").get("data", {})
        limit = _money(data.get("limit"))
        remaining = _money(data.get("limit_remaining"))
        usage = _money(data.get("usage"))
        if limit <= 0 or limit > HARD_CAP or "limit_reset" not in data or data["limit_reset"] is not None:
            raise BudgetExceeded("A non-resetting API-key limit of at most $5 is required")
        key_id = hashlib.sha256(os.environ["OPENROUTER_API_KEY"].strip().encode()).hexdigest()
        return {"key_id": key_id, "usage_usd": str(usage), "remaining_usd": str(remaining)}

    def send(self, payload: dict) -> dict:
        return self._json("chat/completions", payload)

    def generation(self, generation_id: str) -> dict:
        return self._json("generation?" + urlencode({"id": generation_id}))

    def model_info(self, model: str) -> dict:
        for item in self._json("models").get("data", []):
            if item.get("id") == model:
                return item
        raise GatewayError("Requested model is not in the current OpenRouter catalogue")


def prepare_text_budget(client: OpenRouterClient, request: ModelRequest,
                        ceiling: Decimal = SMALL_TEXT_REQUEST_CEILING) -> tuple[RequestBudget, dict, Decimal]:
    """Fetch real current pricing and bound a small text-only request.

    This intentionally refuses multimodal input. Image token estimation must
    be implemented and checked separately before source-image model calls.
    """
    if any(not isinstance(message.get("content"), str) for message in request.messages):
        raise ValueError("This budget helper supports text-only messages")
    client.preflight()
    info = client.model_info(request.model)
    if not {"response_format", "max_tokens", "temperature"}.issubset(info.get("supported_parameters", [])):
        raise ValueError("Model does not advertise the required parameters")
    count = len(json.dumps({"messages": request.messages, "schema": request.response_schema}, ensure_ascii=False).encode("utf-8"))
    input_bound = max(4096, count * 2 + 1024)
    if input_bound + request.max_output_tokens > info["context_length"]:
        raise ValueError("Request bounds exceed model context")
    max_completion = (info.get("top_provider") or {}).get("max_completion_tokens")
    if max_completion is not None and request.max_output_tokens > max_completion:
        raise ValueError("Output limit exceeds model limit")
    pricing = info["pricing"]
    if Decimal(str(pricing.get("request", "0"))) != 0 or pricing.get("overrides"):
        raise ValueError("Fixed request fees or tiered pricing require a separate cost calculation")
    prompt_price = _money(pricing["prompt"]) * Decimal("1000000")
    completion_price = _money(pricing["completion"]) * Decimal("1000000")
    bound = (input_bound * prompt_price + request.max_output_tokens * completion_price) / Decimal("1000000")
    if bound > _money(ceiling):
        raise BudgetExceeded("Request exceeds its approved reservation ceiling")
    budget = RequestBudget(input_bound, prompt_price, completion_price,
                           "https://openrouter.ai/api/v1/models; " + str(info.get("canonical_slug", request.model)))
    return budget, info, bound


class ModelGateway:
    def __init__(self, directory: Path, *, client=None, limit_usd=LOCAL_CEILING, checkpoint=None):
        self.directory = Path(directory)
        self.client = client if client is not None else OpenRouterClient()
        self.limit = _money(limit_usd)
        if not 0 < self.limit <= LOCAL_CEILING:
            raise ValueError("Local ceiling must be positive and no more than $4.50")
        if not isinstance(self.client, OpenRouterClient) or self.client.mode != "live":
            raise ValueError("Only the real OpenRouter client is supported")
        self.path = self.directory / "model_calls.json"
        self.checkpoint = checkpoint

    def _save(self, state: dict) -> None:
        _write(self.path, state)
        if self.checkpoint is not None:
            self.checkpoint(self.path)

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".model_gateway.lock").open("a+b") as lock:
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise GatewayBusy("Another gateway call is using this ledger") from None
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "mode": self.client.mode, "limit_usd": str(self.limit),
                    "key_id": None, "provider_usage_usd": "0", "blocked_reason": None, "calls": []}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if (state["version"] != 1 or state["mode"] != self.client.mode
                    or _money(state["limit_usd"]) != self.limit):
                raise ValueError("Ledger mode, version or configured ceiling differs")
            _money(state["provider_usage_usd"])
            if not isinstance(state["calls"], list):
                raise ValueError("Invalid call ledger")
            for call in state["calls"]:
                _money(call["reserved_usd"])
                if call["status"] not in {"pending", "uncertain", "completed", "failed"}:
                    raise ValueError("Unknown call state")
                if call["actual_usd"] is not None:
                    _money(call["actual_usd"])
                elif call["status"] not in UNRESOLVED:
                    raise ValueError("Settled call has no cost")
                if call["status"] == "completed" and _hash(call["result"]) != call["result_sha256"]:
                    raise ValueError("Cached result hash mismatch")
            return state
        except (OSError, ValueError, KeyError, TypeError):
            raise LedgerError("Invalid ledger or response cache; refusing to reset spending or retry") from None

    def _summary(self, state: dict) -> dict:
        spent = sum((_money(call["actual_usd"]) for call in state["calls"] if call["actual_usd"] is not None), Decimal("0"))
        reserved = sum((_money(call["reserved_usd"]) for call in state["calls"] if call["status"] in UNRESOLVED), Decimal("0"))
        effective = max(spent, _money(state["provider_usage_usd"]))
        return {"mode": state["mode"], "limit_usd": str(self.limit), "spent_usd": str(spent),
                "effective_spent_usd": str(effective), "reserved_usd": str(reserved),
                "available_usd": str(max(Decimal("0"), self.limit - effective - reserved)),
                "attempted_calls": len(state["calls"]), "completed_calls": sum(call["status"] == "completed" for call in state["calls"]),
                "unresolved_call_ids": [call["id"] for call in state["calls"] if call["status"] in UNRESOLVED],
                "blocked_reason": state["blocked_reason"]}

    def summary(self) -> dict:
        with self._locked():
            return self._summary(self._load())

    @staticmethod
    def _request(request: ModelRequest, budget: RequestBudget, mode: str) -> tuple[dict, str, Decimal]:
        if request.purpose not in {"translate", "review", "repair", "transcribe"}:
            raise ValueError("Unknown model-call purpose")
        if not re.fullmatch(r"[0-9a-f]{64}", request.source_sha256):
            raise ValueError("A source SHA-256 is required")
        if not request.prompt_version or not request.model or request.model == "openrouter/auto":
            raise ValueError("Specify a prompt version and a model; automatic model selection is not allowed")
        if type(request.max_output_tokens) is not int or request.max_output_tokens <= 0:
            raise ValueError("A positive output-token limit is required")
        if type(budget.input_token_bound) is not int or budget.input_token_bound <= 0 or not budget.price_reference.strip():
            raise ValueError("Provide a conservative input-token bound and its pricing reference")
        if not request.messages or any(message.get("role") not in {"system", "user", "assistant"} for message in request.messages):
            raise ValueError("Invalid messages")
        payload = {"model": request.model, "messages": list(request.messages), "max_tokens": request.max_output_tokens,
                   "temperature": 0, "stream": False, "provider": {"allow_fallbacks": False, "require_parameters": True}}
        if request.response_schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "tournament_result", "strict": True, "schema": request.response_schema}}
        # Snapshot the mutable messages/schema before hashing and sending.
        payload = json.loads(_canonical(payload))
        key = _hash({"mode": mode, "source": request.source_sha256, "prompt_version": request.prompt_version,
                     "purpose": request.purpose, "payload": payload})
        bound = (Decimal(budget.input_token_bound) * _money(budget.input_usd_per_million)
                 + Decimal(request.max_output_tokens) * _money(budget.output_usd_per_million)) / Decimal("1000000")
        return payload, key, bound + _money(budget.extra_cost_usd)

    @staticmethod
    def _result(call: dict, cached: bool) -> ModelResult:
        result = call["result"]
        return ModelResult(call["id"], result["provider_id"], result["model"], result["content"],
                           Decimal("0") if cached else _money(call["actual_usd"]), cached)

    def _settle(self, state: dict, call: dict, cost: Decimal) -> None:
        call["actual_usd"] = str(cost)
        call["status"] = "failed"  # Charge is committed even if content validation fails.
        state["provider_usage_usd"] = str(_money(state["provider_usage_usd"]) + cost)
        if cost > _money(call["reserved_usd"]):
            state["blocked_reason"] = "Reported cost exceeded the reservation; review pricing/token bounds"
        if _money(self._summary(state)["effective_spent_usd"]) > self.limit:
            state["blocked_reason"] = "Local spending ceiling exceeded; no further paid calls"

    def call(self, request: ModelRequest, budget: RequestBudget) -> ModelResult:
        payload, cache_key, bound = self._request(request, budget, self.client.mode)
        with self._locked():
            state = self._load()
            matching = [call for call in state["calls"] if call["cache_key"] == cache_key]
            for call in matching:
                if call["status"] == "completed":
                    return self._result(call, True)
            if state["blocked_reason"]:
                raise BudgetExceeded(state["blocked_reason"])
            if any(call["status"] in UNRESOLVED for call in state["calls"]):
                raise AccountingUncertain("An earlier request has unresolved accounting; no new paid calls")
            if matching:
                raise InvalidModelResponse("This request already failed; inspect it and use an explicit repair request")
            self.client.assert_ready()
            provider = self.client.preflight()
            if state["key_id"] is not None and state["key_id"] != provider["key_id"]:
                raise LedgerError("API key identity changed; refusing to bypass the existing ledger")
            state["key_id"] = provider["key_id"]
            state["provider_usage_usd"] = str(max(_money(state["provider_usage_usd"]), _money(provider["usage_usd"])))
            self._save(state)
            if bound > _money(self._summary(state)["available_usd"]) or bound > _money(provider["remaining_usd"]):
                raise BudgetExceeded("Request reservation does not fit the remaining allowance")
            call = {"id": uuid4().hex, "cache_key": cache_key, "purpose": request.purpose,
                    "source_sha256": request.source_sha256, "prompt_version": request.prompt_version,
                    "requested_model": request.model, "started_at": datetime.now(timezone.utc).isoformat(),
                    "reserved_usd": str(bound), "actual_usd": None, "status": "pending", "provider_id": None,
                    "input_token_bound": budget.input_token_bound, "max_output_tokens": request.max_output_tokens,
                    "price_reference": budget.price_reference,
                    "input_usd_per_million": str(budget.input_usd_per_million),
                    "output_usd_per_million": str(budget.output_usd_per_million),
                    "extra_cost_usd": str(budget.extra_cost_usd)}
            state["calls"].append(call)
            self._save(state)
            try:
                response = self.client.send(payload)
            except Exception as exc:
                call.update(status="uncertain", error_type=type(exc).__name__)
                self._save(state)
                raise AccountingUncertain("Request may have been charged; reservation retained and automatic retry disabled") from None
            provider_id = response.get("id") if isinstance(response, dict) else None
            if isinstance(provider_id, str) and len(provider_id) <= 256:
                call["provider_id"] = provider_id
            try:
                usage = response["usage"]
                cost = _money(usage["cost"])
            except (KeyError, TypeError, ValueError):
                call.update(status="uncertain", error_type="MissingOrInvalidCost")
                self._save(state)
                raise AccountingUncertain("Response has no confirmed cost; reservation retained") from None
            self._settle(state, call, cost)
            if isinstance(response.get("model"), str):
                call["actual_model"] = response["model"]
            try:
                counts = {name: usage[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")}
                if any(type(value) is not int or value < 0 for value in counts.values()):
                    raise ValueError("Invalid token counts")
                call["usage"] = counts
                details = usage.get("completion_tokens_details") or {}
                reasoning = details.get("reasoning_tokens", 0)
                if type(reasoning) is int and reasoning >= 0:
                    call["reasoning_tokens"] = reasoning
                if counts["prompt_tokens"] > budget.input_token_bound or counts["completion_tokens"] > request.max_output_tokens:
                    state["blocked_reason"] = "Reported tokens exceeded request bounds; review cost assumptions"
                choice = response["choices"][0]
                content = choice["message"]["content"]
                actual_model = response["model"]
                if choice["finish_reason"] != "stop" or not isinstance(content, str) or not content.strip():
                    raise ValueError("Incomplete, refused or empty output")
                if not isinstance(actual_model, str) or not actual_model or not call["provider_id"]:
                    raise ValueError("Missing model or generation ID")
                if request.response_schema is not None and not isinstance(json.loads(content), dict):
                    raise ValueError("Expected JSON object content")
                call["actual_model"] = actual_model
                if state["blocked_reason"]:
                    raise ValueError("Accounting bound was exceeded")
                call["result"] = {"provider_id": call["provider_id"], "model": actual_model, "content": content}
                call["result_sha256"] = _hash(call["result"])
                call["status"] = "completed"
            except (KeyError, IndexError, TypeError, ValueError):
                call["error_type"] = "InvalidOrIncompleteResponse"
                self._save(state)
                raise InvalidModelResponse("Response was charged but not accepted; inspect the ledger before repair") from None
            self._save(state)
            return self._result(call, False)

    def reconcile_generation(self, call_id: str) -> dict:
        """Resolve a known generation's final charge; never retries the request.

        No ID means no automatic release: provider activity needs review.
        Reconciliation settles accounting only; a lost response is not cached.
        """
        with self._locked():
            state = self._load()
            call = next((call for call in state["calls"] if call["id"] == call_id), None)
            if call is None or call["status"] not in UNRESOLVED:
                raise LedgerError("Call is not awaiting reconciliation")
            if not call["provider_id"]:
                raise AccountingUncertain("No generation ID is known; inspect provider activity before further spending")
            self.client.assert_ready()
            provider = self.client.preflight()
            if provider["key_id"] != state["key_id"]:
                raise LedgerError("API key identity changed")
            data = self.client.generation(call["provider_id"])["data"]
            if data.get("id") != call["provider_id"] or not data.get("finish_reason"):
                raise AccountingUncertain("Generation is not confirmed final")
            self._settle(state, call, _money(data["total_cost"]))
            call["error_type"] = "AccountingReconciledResponseUnavailable"
            self._save(state)
            return self._summary(state)



# Prepare and validate real translations, including the saved one-question probe.




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
    from .archive_builder import _save
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



# Evidence-backed bilingual review through the real cost-controlled gateway.




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
    from .archive_builder import _save
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



# Prepare verified HTML and PDF statements for translation without network access.




def _checked_bytes(path: str, expected_hash: str) -> bytes:
    data = saved_path(path).read_bytes()
    if sha256(data).hexdigest() != expected_hash:
        raise ValueError(f"Saved file hash does not match: {path}")
    return data


def _reviewed_probe(data_dir: Path) -> tuple[dict, dict] | None:
    translation_path = data_dir / "live_api_test.json"
    review_path = data_dir / "live_api_review.json"
    if not translation_path.is_file() or not review_path.is_file():
        return None
    translation_bytes = translation_path.read_bytes()
    translation = json.loads(translation_bytes)
    review = json.loads(review_path.read_bytes())
    if translation["status"] != "passed_basic_checks" or review["status"] != "review_passed":
        return None
    if (review["problem_id"] != translation["problem_id"]
            or review["translation_report_sha256"] != sha256(translation_bytes).hexdigest()
            or review["translation_call_id"] != translation["call_id"]):
        raise ValueError("Saved probe review is not bound to its translation")
    ledger = read_verified_ledger(data_dir)
    verify_recorded_response(ledger, translation["call_id"], "translate", translation["translation"])
    verify_recorded_response(ledger, review["call_id"], "review", review["review"])
    return translation, review


def _html_statements(item: dict, language: str) -> list[dict]:
    if item["status"] != "extracted" or not item["coverage_verified"]:
        raise ValueError("HTML extraction needs source review")
    digest = item["section_sha256"]
    fragment = _checked_bytes(item["section_html_path"], digest).decode("utf-8")
    statements = []
    for q in item["questions"]:
        if q["issues"] or q["images"]:
            raise ValueError(f"HTML question requires source or image review: {q['id']}")
        if fragment[q["source_start"]:q["source_end"]] != q["original_html"]:
            raise ValueError(f"Original HTML question range does not match: {q['id']}")
        source = StatementSource(q["id"], digest, "html", language, q["text"], q["original_html"])
        statements.append({"source": source, "number": q["number"], "author": q["author"],
                           "scores": [{"subpart": None, "value": q["points"]}]})
    tail = item["trailing_html"]
    if note := _statement_text(tail).strip():
        if not fragment.endswith(tail):
            raise ValueError("Original HTML section note does not match")
        source = StatementSource(f"{item['source']['anchor']}:note", digest, "html", language, note, tail)
        statements.append({"source": source, "number": None, "author": None, "scores": []})
    return statements


def _pdf_statements(item: dict, language: str, checks: dict) -> list[dict]:
    if (item["format"] != "single_page_score_column" or not item["span_coverage_verified"]
            or not all((item["age_group"], item["difficulty"], item["date"]))):
        raise ValueError("PDF extraction needs source review")
    digest = item["source_sha256"]
    _checked_bytes(item["source_path"], digest)
    statements = []
    for q in item["questions"]:
        crop = saved_path(q["region_image"])
        crop_hash = sha256(crop.read_bytes()).hexdigest()
        text = q["text"]
        check = checks.get(q["id"])
        if check is not None:
            if (check["source_sha256"] != digest or check["crop_sha256"] != crop_hash
                    or check["text_sha256"] != sha256(text.encode("utf-8")).hexdigest()
                    or set(check["checked_issues"]) != set(q["issues"])):
                raise ValueError(f"PDF source check no longer matches: {q['id']}")
            for before, after in check["replacements"]:
                if text.count(before) != 1:
                    raise ValueError(f"PDF transcription correction is ambiguous: {q['id']}")
                text = text.replace(before, after, 1)
        elif q["issues"] or q["graphics"]:
            raise ValueError(f"PDF question requires source or graphics review: {q['id']}")
        source = StatementSource(q["id"], digest, "pdf", language, text, q["text"])
        statements.append({"source": source, "number": q["number"], "author": q["author"],
                           "scores": q["scores"], "source_crop": crop.as_posix(),
                           "crop_sha256": crop_hash, "graphics": q["graphics"],
                           "graphics_kind": check.get("graphics_kind") if check else None,
                           "source_check": check["finding"] if check else None})
    return statements


def _prepare_requests(statements: list[dict], probe: tuple[dict, dict] | None) -> list[dict]:
    prepared = []
    for entry in statements:
        source = entry["source"]
        row = {**entry, "source": asdict(source), "status": "ready_for_translation"}
        if probe and source.id == probe[0]["problem_id"]:
            translation, review = probe
            if source.text != translation["source_text"] or source.source_sha256 != review["source_sha256"]:
                raise ValueError(f"Reviewed probe has a different source: {source.id}")
            row.update(status="review_passed", english=translation["translation"]["english"],
                       translation_call_id=translation["call_id"], review_call_id=review["call_id"])
        else:
            row["translation_request"] = asdict(translation_request(source))
        prepared.append(row)
    return prepared


def prepare_translation(data_dir: Path) -> dict:
    manifest = json.loads((data_dir / "manifest.json").read_bytes())
    inspection_record = manifest["inspection"]
    inspection = json.loads(_checked_bytes(inspection_record["path"], inspection_record["sha256"]))
    inspected = {(row["source_url"], row["anchor"], row["page"]): row
                 for key in ("html_sections", "pdf_pages") for row in inspection[key]}
    checks_path = data_dir / "pdf_source_checks.json"
    checks = json.loads(checks_path.read_bytes()) if checks_path.is_file() else {}
    probe = _reviewed_probe(data_dir)
    report = {"stage": "translation_preparation", "model_calls": 0, "review_model": REVIEW_MODEL,
              "sets": [], "skipped": [], "errors": []}
    for kind in ("html", "pdf"):
        record = manifest.get(f"{kind}_extraction")
        if not record:
            continue
        extraction = json.loads(_checked_bytes(record["path"], record["sha256"]))
        report["skipped"].extend(extraction["skipped"])
        report["errors"].extend(extraction["errors"])
        for item in extraction["sets"]:
            if kind == "html":
                url, anchor, page = item["source"]["page_url"], item["source"]["anchor"], None
            else:
                url, anchor, page = item["source_url"], "", item["page"]
            location = {"source_url": url, "anchor": anchor, "page": page}
            try:
                evidence = inspected[url, anchor, page]
                language = evidence["language"]["language"]
                if evidence["status"] != "ready_for_extraction" or language == "en":
                    report["skipped"].append({**location, "reason": evidence["status"]})
                    continue
                if kind == "pdf" and item["source_sha256"] != evidence["source_sha256"]:
                    raise ValueError("PDF extraction and inspection refer to different source hashes")
                statements = (_html_statements(item, language) if kind == "html"
                              else _pdf_statements(item, language, checks))
                numbers = [entry["number"] for entry in statements if entry["number"] is not None]
                if not numbers or numbers != list(range(1, len(numbers) + 1)):
                    raise ValueError("Prepared set must have consecutive question numbers from 1")
                report["sets"].append({**location, "kind": kind, "age_group": item["age_group"],
                                       "difficulty": item["difficulty"], "date": item["date"],
                                       "question_count": len(numbers),
                                       "statements": _prepare_requests(statements, probe)})
            except (OSError, KeyError, ValueError) as exc:
                report["errors"].append({**location, "error": str(exc)})
    if not report["sets"]:
        report["errors"].append({"error": "No extracted sets were prepared"})
    return report



# Preview or run the same translation and review path for every prepared set.




LEDGER_BRANCH = "openrouter-ledger"


def build_plan(prepared: dict) -> dict:
    if prepared["errors"]:
        raise ValueError(f"Source preparation reported {len(prepared['errors'])} error(s)")
    requests = []
    for item in prepared["sets"]:
        for row in item["statements"]:
            if "translation_request" in row:
                source = StatementSource(**row["source"])
                requests.append({"id": source.id, "translate": row["translation_request"],
                                 "review_template": asdict(review_request(source, ""))})
    plan = {"requests": requests, "per_call_reservation_ceiling_usd": str(SMALL_TEXT_REQUEST_CEILING),
            "max_new_calls": 2 * len(requests),
            "max_new_reservations_usd": str(2 * len(requests) * SMALL_TEXT_REQUEST_CEILING)}
    plan["sha256"] = sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return plan


class GitLedger:
    """Commit each accounting checkpoint to one fast-forward-only Git ref."""

    def __init__(self, checkout: Path):
        self.checkout = checkout
        self.path = checkout / "data" / "model_calls.json"

    def _git(self, *args, allowed=(0,)) -> int:
        result = subprocess.run(["git", "-C", str(self.checkout), *args], capture_output=True, text=True)
        if result.returncode not in allowed:
            raise LedgerError(f"Ledger Git checkpoint failed during {args[0]}")
        return result.returncode

    def verify_local(self, path: Path) -> None:
        if self._git("diff", "--quiet", "HEAD", "--", "data/model_calls.json", allowed=(0, 1)):
            raise LedgerError("Ledger checkout has uncommitted accounting changes")
        if self.path.read_bytes() != path.read_bytes():
            raise LedgerError("Runner is not using the canonical Git ledger")

    def __call__(self, path: Path) -> None:
        self.path.write_bytes(path.read_bytes())
        self._git("add", "--", "data/model_calls.json")
        if self._git("diff", "--cached", "--quiet", "--", "data/model_calls.json", allowed=(0, 1)):
            self._git("commit", "-m", "Record OpenRouter accounting checkpoint", "--", "data/model_calls.json")
            self._git("push", "origin", f"HEAD:refs/heads/{LEDGER_BRANCH}")


def run_paid(data_dir: Path, prepared: dict, path: Path, gateway: ModelGateway) -> dict:
    report = {"sets": [], "model_accounting": gateway.summary(), "status": "running"}
    _save(path, report)
    for item in prepared["sets"]:
        result = {key: value for key, value in item.items() if key != "statements"}
        result.update(status="running", statements=[])
        report["sets"].append(result)
        for row in item["statements"]:
            source = StatementSource(**row["source"])
            entry = {key: value for key, value in row.items() if key != "translation_request"}
            result["statements"].append(entry)
            try:
                if row["status"] != "review_passed":
                    translation = translate_statement(data_dir, source, allow_paid=True, gateway=gateway)
                    entry.update(status=translation["status"], translation=translation)
                    if translation["status"] == "passed_basic_checks":
                        english = translation["translation"]["english"]
                        review = review_statement(data_dir, source, english, allow_paid=True, gateway=gateway)
                        entry.update(status=review["status"], english=english, review=review)
            except (GatewayError, OSError, ValueError, KeyError) as exc:
                entry.update(status="stopped", error=str(exc))
                result["status"] = "stopped"
                report.update(status="stopped", model_accounting=gateway.summary())
                _save(path, report)
                raise
            print(f"{source.id} | {entry['status']}", flush=True)
            report["model_accounting"] = gateway.summary()
            _save(path, report)
        result["status"] = ("review_passed" if all(row["status"] == "review_passed" for row in result["statements"])
                            else "needs_review")
        _save(path, report)
    report["status"] = ("review_passed" if all(item["status"] == "review_passed" for item in report["sets"])
                        else "needs_review")
    _save(path, report)
    return report


def paid_main(data_dir=Path("data"), plan_path=Path("data/run_plan.json"),
              results_path=Path("data/translation_run.json"), approved_plan=None,
              ledger_checkout=None) -> int:
    try:
        prepared = prepare_translation(data_dir)
        plan = build_plan(prepared)
        _save(plan_path, plan)
        print(f"Plan {plan['sha256']} | up to {plan['max_new_calls']} calls | "
              f"total new reservations at most USD {plan['max_new_reservations_usd']}", flush=True)
        if not approved_plan:
            return 0
        if plan["sha256"] != approved_plan or not ledger_checkout:
            raise ValueError("Paid mode requires the approved plan hash and canonical Git ledger checkout")
        checkpoint = GitLedger(ledger_checkout)
        checkpoint.verify_local(data_dir / "model_calls.json")
        gateway = ModelGateway(data_dir, client=OpenRouterClient(allow_live=True), checkpoint=checkpoint)
        result = run_paid(data_dir, prepared, results_path, gateway)
        print(f"Run status: {result['status']} | total recorded USD {result['model_accounting']['spent_usd']}")
        return int(result["status"] != "review_passed")
    except (GatewayError, OSError, ValueError, KeyError) as exc:
        print(f"Translation run stopped: {exc}", flush=True)
        return 1
