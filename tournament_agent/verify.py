"""Read-only verification of the real saved handoff, without API calls.

Run from the repository root: python -m tournament_agent.verify
This is actual cached-data verification, not a simulated model/API test.
"""

import hashlib
import json
from pathlib import Path

from .paths import saved_path
from .translator import ModelGateway


def main() -> int:
    manifest = json.loads(Path("data/manifest.json").read_text(encoding="utf-8"))
    downloaded = [manifest["index"], *manifest["pages"], *manifest["documents"]]
    for item in downloaded:
        data = saved_path(item["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"Original source hash mismatch: {item['path']}")
    print(f"Original source hashes verified: {len(downloaded)}")
    for key in ("inspection", "html_extraction", "pdf_extraction"):
        record = manifest[key]
        data = saved_path(record["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"Report hash mismatch: {key}")
    print("Manifest report hashes verified: 3")
    html_report = json.loads(saved_path(manifest["html_extraction"]["path"]).read_text(encoding="utf-8"))
    html_count = 0
    for item in html_report["sets"]:
        original = saved_path(item["section_html_path"]).read_bytes().decode("utf-8")
        reconstructed = item["preamble_html"] + "".join(q["original_html"] for q in item["questions"]) + item["trailing_html"]
        if original != reconstructed or hashlib.sha256(original.encode()).hexdigest() != item["section_sha256"]:
            raise ValueError("HTML question/source coverage mismatch")
        for question in item["questions"]:
            if original[question["source_start"]:question["source_end"]] != question["original_html"]:
                raise ValueError("HTML question source range mismatch")
        html_count += len(item["questions"])
    print(f"HTML question source ranges verified: {html_count}")
    pdf_report = json.loads(saved_path(manifest["pdf_extraction"]["path"]).read_text(encoding="utf-8"))
    pdf_count = 0
    for item in pdf_report["sets"]:
        ids = [span["id"] for span in item["preamble_spans"]] + [span["id"] for q in item["questions"] for span in q["spans"]]
        if len(ids) != len(set(ids)) or len(ids) != item["source_span_count"]:
            raise ValueError("PDF span coverage mismatch")
        if not saved_path(item["page_image"]).is_file() or any(not saved_path(q["region_image"]).is_file() for q in item["questions"]):
            raise ValueError("Missing PDF review images")
        pdf_count += len(item["questions"])
    print(f"PDF question spans and source images verified: {pdf_count}")
    review = json.loads(Path("data/live_api_review.json").read_text(encoding="utf-8"))
    translation_data = Path("data/live_api_test.json").read_bytes()
    if hashlib.sha256(translation_data).hexdigest() != review["translation_report_sha256"]:
        raise ValueError("Translation changed since its recorded review")
    summary = ModelGateway(Path("data")).summary()
    print(f"Real model calls: {summary['attempted_calls']}; recorded spend USD: {summary['spent_usd']}")
    print(f"Unresolved charges: {len(summary['unresolved_call_ids'])}")
    print("No source downloads, model requests or report rewrites performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
