"""Command-line entry point for the archive inventory."""

import argparse
from pathlib import Path
import sys

from .pipeline import INDEX_URL, extract_cached_html, extract_cached_pdf, inspect_sources, run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inventory Tournament of Towns archive pages")
    parser.add_argument("--index-url", default=INDEX_URL)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--pace", type=float, default=0.5, help="minimum seconds between requests")
    parser.add_argument("--refresh", action="store_true", help="download even if cached")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true", help="inspect cached sections and PDF page languages locally; no network or model calls")
    mode.add_argument("--extract-html", action="store_true", help="split selected cached HTML sections into questions; no network or model calls")
    mode.add_argument("--extract-pdf", action="store_true", help="split requested cached non-English PDF page sets and create source review images; no model calls")
    mode.add_argument("--prepare-translation", action="store_true", help="prepare verified extracted statements and inspect model inputs locally")
    mode.add_argument("--test-api", action="store_true", help="run one small REAL translation API test using an extracted question")
    mode.add_argument("--review-api", action="store_true", help="run one real bilingual review of the saved translation probe")
    parser.add_argument("--anchor", action="append", default=[], help="section anchor to extract; repeat for multiple sections")
    parser.add_argument("--document-url", action="append", default=[], help="cached PDF URL to extract; repeat for multiple documents")
    parser.add_argument("--allow-paid", action="store_true", help="explicitly enable the paid request for --test-api or --review-api")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.retries < 0 or args.pace < 0:
        parser.error("timeout must be positive; retries and pace must be nonnegative")
    if args.extract_html and not args.anchor:
        parser.error("--extract-html requires at least one --anchor")
    if args.anchor and not args.extract_html:
        parser.error("--anchor is only used with --extract-html")
    if args.extract_pdf and not args.document_url:
        parser.error("--extract-pdf requires at least one --document-url")
    if args.document_url and not args.extract_pdf:
        parser.error("--document-url is only used with --extract-pdf")
    if (args.test_api or args.review_api) and not args.allow_paid:
        parser.error("Live API operations require --allow-paid")
    if args.allow_paid and not (args.test_api or args.review_api):
        parser.error("--allow-paid is only used with live API operations")
    if args.prepare_translation:
        from .pipeline import _save
        from .prepare_translation import prepare_translation
        from .translator import GatewayError

        try:
            report = prepare_translation(args.data_dir)
            path = args.data_dir / "prepared_translation.json"
            _save(path, report)
        except (GatewayError, OSError, ValueError, KeyError) as exc:
            print(f"Translation preparation stopped: {exc}")
            return 1
        request_count = 0
        for item in report["sets"]:
            statements = item["statements"]
            note_count = len(statements) - item["question_count"]
            new = sum("translation_request" in row for row in statements)
            reused = len(statements) - new
            request_count += new
            print(f"{item['date']} {item['age_group']} {item['difficulty']} | "
                  f"{item['question_count']} questions, {note_count} section note(s) | "
                  f"{new} new translations, {reused} reviewed input reused")
            for row in statements:
                if row.get("source_check"):
                    print(f"  Q{row['number']} source check: {row['source_check']}")
        for issue in report["errors"]:
            print(f"Preparation error: {issue}")
        print(f"Prepared requests: {request_count}; model calls: {report['model_calls']}; report: {path}")
        return int(bool(report["errors"]))
    if args.review_api:
        from .validator import review_saved_translation
        from .translator import GatewayError
        try:
            report = review_saved_translation(args.data_dir, allow_paid=True)
        except (GatewayError, ValueError, KeyError, OSError) as exc:
            print(f"Real review stopped: {exc}")
            return 1
        print(f"Review: {report['status']}; model: {report['model']}")
        for check in report["review"].get("checks", []):
            print(f"  {check['name']}: {check['verdict']} — {check['explanation']}")
        for issue in report["review"].get("issues", []):
            print(f"  Issue: {issue['explanation']}; suggested fix: {issue['suggested_fix']}")
        print(f"New charge USD: {report['new_cost_usd']}; cache hit: {report['from_cache']}")
        print(f"Report: {args.data_dir / 'live_api_review.json'}")
        return int(report["status"] != "review_passed")
    if args.test_api:
        from .live_test import run_live_api_test
        from .translator import GatewayError
        try:
            report = run_live_api_test(args.data_dir, allow_paid=True)
        except (GatewayError, ValueError, KeyError, OSError) as exc:
            print(f"Real API test stopped: {exc}")
            return 1
        print(f"Real API test: {report['status']}")
        print(f"Model: {report['model']}; generation: {report['provider_id']}")
        print(f"New charge USD: {report['new_cost_usd']}; cache hit: {report['from_cache']}")
        print(report["translation"].get("english", "No valid English response"))
        print(f"Report: {args.data_dir / 'live_api_test.json'}")
        return int(report["status"] != "passed_basic_checks")
    if args.extract_pdf:
        report = extract_cached_pdf(args.data_dir, args.document_url)
        for item in report["sets"]:
            print(f"Page {item['page']} {item['age_group']}: {len(item['questions'])} questions, {item['status']}, span coverage: {item['span_coverage_verified']}")
            for question in item["questions"]:
                scores = ", ".join(f"{score['subpart'] or 'question'}: {score['value']}" for score in question["scores"])
                print(f"  Q{question['number']}: {scores}; {question['author']}")
        print(f"Errors: {len(report['errors'])}; skipped: {len(report['skipped'])}; model calls: 0")
        print(f"Review: {args.data_dir / 'extracted' / 'pdf_questions_review.html'}")
        return int(bool(report["errors"]))
    if args.extract_html:
        report = extract_cached_html(args.data_dir, args.anchor)
        for item in report["sets"]:
            print(f"{item['source']['anchor']}: {len(item['questions'])} questions, {item['status']}, source coverage: {item['coverage_verified']}")
            for question in item["questions"]:
                print(f"  Q{question['number']}: {question['points']} points; {question['author']}")
        print(f"Errors: {len(report['errors'])}; skipped: {len(report['skipped'])}; paid model calls: 0")
        print(f"Report: {args.data_dir / 'extracted' / 'html_questions.json'}")
        print(f"Review: {args.data_dir / 'extracted' / 'html_questions_review.html'}")
        return int(bool(report["errors"] or report["skipped"] or any(item["status"] != "extracted" for item in report["sets"])))
    if args.inspect:
        from collections import Counter
        report = inspect_sources(args.data_dir)
        print(f"HTML sections: {len(report['html_sections'])}; PDF pages: {len(report['pdf_pages'])}")
        print(f"Statuses: {dict(Counter(p['status'] for p in report['html_sections'] + report['pdf_pages']))}")
        print(f"Errors: {len(report['errors'])}; paid model calls: {report['model_calls']}")
        print(f"Report: {args.data_dir / 'inspection.json'}")
        return int(bool(report["errors"]))
    manifest = run(args.data_dir, args.index_url, timeout=args.timeout,
                   retries=args.retries, pace=args.pace, refresh=args.refresh)
    index = manifest["index"]
    print(f"Index: {index['status']} {index['url']}")
    if index.get("error"):
        print(f"  {index['error']}")
    refs = manifest["references"]
    pages = manifest["pages"]
    print(f"References: {len(refs)}; unique pages: {len(pages)}")
    for ref in refs:
        fragment = "#" + ref["anchor"] if ref["anchor"] else ""
        print(f"  {ref['page_url']}{fragment}  {ref['label']}")
    print("Page downloads:")
    for page in pages:
        detail = page.get("error", "cache" if page.get("from_cache") else "network")
        print(f"  {page['status']}: {page['url']} ({detail})")
    print(f"PDF references: {len(manifest['document_references'])}; unique PDFs: {len(manifest['documents'])}")
    for doc in manifest["documents"]:
        detail = doc.get("error", "cache" if doc.get("from_cache") else "network")
        print(f"  {doc['status']}: {doc['url']} ({detail})")
    print(f"Manifest: {args.data_dir / 'manifest.json'}")
    return int(index["status"] != "downloaded" or not refs or any(p["status"] != "downloaded" for p in pages + manifest["documents"]))


if __name__ == "__main__":
    sys.exit(main())
