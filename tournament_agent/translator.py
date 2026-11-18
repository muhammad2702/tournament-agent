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


HARD_CAP = Decimal("5.00")
LOCAL_CEILING = Decimal("4.50")
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
                        ceiling: Decimal = Decimal("0.01")) -> tuple[RequestBudget, dict, Decimal]:
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
    def __init__(self, directory: Path, *, client=None, limit_usd=LOCAL_CEILING):
        self.directory = Path(directory)
        self.client = client if client is not None else OpenRouterClient()
        self.limit = _money(limit_usd)
        if not 0 < self.limit <= LOCAL_CEILING:
            raise ValueError("Local ceiling must be positive and no more than $4.50")
        if not isinstance(self.client, OpenRouterClient) or self.client.mode != "live":
            raise ValueError("Only the real OpenRouter client is supported")
        self.path = self.directory / "model_calls.json"

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
            _write(self.path, state)
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
            _write(self.path, state)
            try:
                response = self.client.send(payload)
            except Exception as exc:
                call.update(status="uncertain", error_type=type(exc).__name__)
                _write(self.path, state)
                raise AccountingUncertain("Request may have been charged; reservation retained and automatic retry disabled") from None
            provider_id = response.get("id") if isinstance(response, dict) else None
            if isinstance(provider_id, str) and len(provider_id) <= 256:
                call["provider_id"] = provider_id
            try:
                usage = response["usage"]
                cost = _money(usage["cost"])
            except (KeyError, TypeError, ValueError):
                call.update(status="uncertain", error_type="MissingOrInvalidCost")
                _write(self.path, state)
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
                _write(self.path, state)
                raise InvalidModelResponse("Response was charged but not accepted; inspect the ledger before repair") from None
            _write(self.path, state)
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
            _write(self.path, state)
            return self._summary(state)
