"""Prepare verified HTML and PDF statements for translation without network access."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from .extractor import _statement_text
from .live_test import translation_request
from .models import StatementSource
from .paths import saved_path
from .validator import REVIEW_MODEL, read_verified_ledger, verify_recorded_response


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
