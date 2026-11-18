"""Try the existing extractors on every eligible unit in the cached archive."""

import argparse
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .pipeline import _save, extract_cached_html, extract_cached_pdf
from .reconcile import build_manifest


def _extraction_summary(report: dict, source_fields: tuple[str, ...]) -> dict:
    return {"statuses": dict(Counter(row["status"] for row in report["sets"])),
            "sets": [{**{field: row[field] for field in source_fields},
                      "question_count": len(row["questions"])} for row in report["sets"]],
            "errors": report["errors"], "skipped": report["skipped"]}


def audit_archive(data_dir: Path) -> dict:
    original = json.loads((data_dir / "manifest.json").read_bytes())
    inventory = build_manifest(data_dir, {"model_accounting": {}}, [])
    anchors = sorted({row["anchor"] for row in inventory["html_references"]
                      if row["status"] == "ready_for_extraction"})
    urls = [doc["source_url"] for doc in inventory["pdf_documents"] if doc["status"] == "inspected"
            and any(page["status"] == "ready_for_extraction" for page in doc["pages"])]
    fields = ("age_group", "difficulty", "date", "status", "issues")
    with TemporaryDirectory(prefix="tournament-archive-audit-") as folder:
        work_dir = Path(folder)
        _save(work_dir / "manifest.json", original)
        html = extract_cached_html(work_dir, anchors)
        pdf = extract_cached_pdf(work_dir, urls)
    return {"inventory_counts": inventory["counts"], "inspection_errors": inventory["inspection_errors"],
            "html": {"anchors_tried": len(anchors), **_extraction_summary(html, ("source", *fields))},
            "pdf": {"documents_tried": len(urls), **_extraction_summary(pdf, ("source_url", "page", *fields))},
            "paid_model_calls": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--report", type=Path, default=Path("qa/archive_audit.json"))
    args = parser.parse_args()
    result = audit_archive(args.data_dir)
    _save(args.report, result)
    print(f"HTML {result['html']['statuses']} | PDF {result['pdf']['statuses']} | report {args.report}")
    return int(bool(result["html"]["errors"] or result["pdf"]["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
