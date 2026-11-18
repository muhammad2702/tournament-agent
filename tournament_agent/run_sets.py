"""Preview or run the same translation and review path for every prepared set."""

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from .live_test import translate_statement
from .models import StatementSource
from .pipeline import _save
from .prepare_translation import prepare_translation
from .translator import (GatewayError, LedgerError, ModelGateway, OpenRouterClient,
                         SMALL_TEXT_REQUEST_CEILING)
from .validator import review_request, review_statement


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--plan", type=Path, default=Path("data/run_plan.json"))
    parser.add_argument("--results", type=Path, default=Path("data/translation_run.json"))
    parser.add_argument("--approved-plan", help="SHA-256 of the preview approved for paid calls")
    parser.add_argument("--ledger-checkout", type=Path, help="Canonical Git ledger checkout for a paid run")
    args = parser.parse_args()
    try:
        prepared = prepare_translation(args.data_dir)
        plan = build_plan(prepared)
        _save(args.plan, plan)
        print(f"Plan {plan['sha256']} | up to {plan['max_new_calls']} calls | "
              f"total new reservations at most USD {plan['max_new_reservations_usd']}", flush=True)
        if not args.approved_plan:
            return 0
        if plan["sha256"] != args.approved_plan or not args.ledger_checkout:
            raise ValueError("Paid mode requires the approved plan hash and canonical Git ledger checkout")
        checkpoint = GitLedger(args.ledger_checkout)
        checkpoint.verify_local(args.data_dir / "model_calls.json")
        gateway = ModelGateway(args.data_dir, client=OpenRouterClient(allow_live=True), checkpoint=checkpoint)
        result = run_paid(args.data_dir, prepared, args.results, gateway)
        print(f"Run status: {result['status']} | total recorded USD {result['model_accounting']['spent_usd']}")
        return int(result["status"] != "review_passed")
    except (GatewayError, OSError, ValueError, KeyError) as exc:
        print(f"Translation run stopped: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
