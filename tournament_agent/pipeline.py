"""Run the inventory and checkpoint successes and failures in a manifest."""

from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import hashlib

from .discovery import discover, discover_documents
from .fetcher import Fetcher
from .paths import saved_path

INDEX_URL = "https://www.turgor.ru/en/problems/allproblems.php"


def _decode(data: bytes, content_type: str) -> str:
    match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type, re.I)
    if not match:
        match = re.search(rb"charset\s*=\s*['\"]?([\w.-]+)", data[:4096], re.I)
    encoding = match.group(1) if match else "utf-8"
    if isinstance(encoding, bytes):
        encoding = encoding.decode("ascii")
    return data.decode(encoding)


def _save(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def run(data_dir: Path, index_url: str = INDEX_URL, *, timeout: float = 30,
        retries: int = 2, pace: float = 0.5, refresh: bool = False) -> dict:
    data_dir = Path(data_dir)
    manifest_path = data_dir / "manifest.json"
    fetcher = Fetcher(data_dir / "raw", timeout, retries, pace, refresh)
    manifest = {"scope": "numbered index pages and same-host PDF links in the index and downloaded pages; classification and extraction pending",
                "index": {"url": index_url, "status": "pending"}, "references": [], "pages": [],
                "document_references": [], "documents": []}
    _save(manifest_path, manifest)
    try:
        index = fetcher.fetch(index_url)
        manifest["index"] = {**asdict(index), "status": "downloaded"}
        html = _decode(saved_path(index.path).read_bytes(), index.content_type)
        refs = discover(html, index.final_url)
        manifest["references"] = [asdict(ref) for ref in refs]
        manifest["document_references"] = [asdict(ref) for ref in discover_documents(html, index.final_url)]
    except (OSError, ValueError, UnicodeError, LookupError) as exc:
        manifest["index"].update(status="failed", error=f"{type(exc).__name__}: {exc}")
        _save(manifest_path, manifest)
        return manifest

    # dict preserves the order of first appearance and drops duplicate page URLs only.
    page_urls = dict.fromkeys(ref.page_url for ref in refs)
    manifest["pages"] = [{"url": url, "status": "pending"} for url in page_urls]
    _save(manifest_path, manifest)
    for page in manifest["pages"]:
        try:
            page.update(asdict(fetcher.fetch(page["url"])), status="downloaded")
            page_html = _decode(saved_path(page["path"]).read_bytes(), page["content_type"])
            manifest["document_references"].extend(asdict(ref) for ref in discover_documents(page_html, page["final_url"]))
        except (OSError, ValueError, UnicodeError, LookupError) as exc:
            page.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        _save(manifest_path, manifest)
    urls = dict.fromkeys(ref["url"] for ref in manifest["document_references"])
    manifest["documents"] = [{"url": url, "status": "pending", "classification_status": "needs_review", "language": "unknown"} for url in urls]
    _save(manifest_path, manifest)
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(fetcher.fetch, doc["url"], kind="pdf"): doc for doc in manifest["documents"]}
        for future in as_completed(pending):
            document = pending[future]
            try:
                document.update(asdict(future.result()), status="downloaded")
            except (OSError, ValueError) as exc:
                document.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            _save(manifest_path, manifest)
    return manifest


def inspect_sources(data_dir: Path) -> dict:
    """Inspect cached sources locally; do not fetch or call any model."""
    from .extractor import inspect_html, inspect_pdf
    from .models import SourceRef

    data_dir = Path(data_dir)
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = {"stage": "source_inspection", "model_calls": 0,
              "html_sections": [], "pdf_pages": [], "errors": [],
              "notes": ["Page probes are not confirmed sets. Problem extraction, translation and validation are not yet implemented.",
                        "Unanchored archive pages need section discovery; page navigation is not used to decide a set's language."]}
    raw_by_url = {page["url"]: page for page in manifest["pages"]}
    anchors_by_url = {}
    for ref in manifest["references"]:
        if ref["anchor"]:
            anchors_by_url.setdefault(ref["page_url"], set()).add(ref["anchor"])

    def read_verified(item):
        if item["status"] != "downloaded":
            raise ValueError("Source has not been downloaded successfully")
        data = saved_path(item["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("Source hash does not match manifest")
        return data

    for ref in manifest["references"]:
        if not ref["anchor"]:
            continue
        try:
            page = raw_by_url[ref["page_url"]]
            html = _decode(read_verified(page), page["content_type"])
            probe, fragment = inspect_html(html, SourceRef(**ref), anchors_by_url[ref["page_url"]])
            section_key = hashlib.sha256((ref["page_url"] + "#" + ref["anchor"]).encode()).hexdigest()
            section_path = data_dir / "sections" / (section_key + ".html")
            section_path.parent.mkdir(parents=True, exist_ok=True)
            section_path.write_text(fragment, encoding="utf-8", newline="")
            report["html_sections"].append({**asdict(probe), "section_html_path": section_path.as_posix(), "source_sha256": page["sha256"]})
        except (OSError, ValueError, KeyError, LookupError) as exc:
            report["errors"].append({"source_url": ref["page_url"], "anchor": ref["anchor"], "error": f"{type(exc).__name__}: {exc}"})

    for document in manifest["documents"]:
        try:
            read_verified(document)
            report["pdf_pages"].extend({**asdict(probe), "source_sha256": document["sha256"]}
                                       for probe in inspect_pdf(saved_path(document["path"]), document["url"]))
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            report["errors"].append({"source_url": document["url"], "error": f"{type(exc).__name__}: {exc}"})
    report_path = data_dir / "inspection.json"
    _save(report_path, report)
    manifest["inspection"] = {"path": report_path.as_posix(), "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                              "html_sections": len(report["html_sections"]), "pdf_pages": len(report["pdf_pages"]),
                              "errors": len(report["errors"])}
    _save(manifest_path, manifest)
    return report


def extract_cached_html(data_dir: Path, anchors: list[str]) -> dict:
    """Extract only explicitly selected cached HTML sections, without network."""
    from html import escape
    from .extractor import extract_html_questions, section_html
    from .models import SourceRef

    data_dir = Path(data_dir)
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inspection_path = saved_path(manifest["inspection"]["path"])
    if hashlib.sha256(inspection_path.read_bytes()).hexdigest() != manifest["inspection"]["sha256"]:
        raise ValueError("Inspection report no longer matches its recorded hash")
    inspection = json.loads(inspection_path.read_text(encoding="utf-8"))
    report = {"stage": "html_question_extraction", "sets": [], "errors": [], "skipped": [], "model_calls": 0}
    wanted = set(anchors)
    probes = [probe for probe in inspection["html_sections"] if probe["anchor"] in wanted]
    for missing in sorted(wanted - {probe["anchor"] for probe in probes}):
        report["errors"].append({"anchor": missing, "error": "No inspected HTML section found"})
    pages = {page["url"]: page for page in manifest["pages"]}
    for probe in probes:
        if probe["status"] != "ready_for_extraction":
            report["skipped"].append({"anchor": probe["anchor"], "reason": probe["status"]})
            continue
        try:
            page = pages[probe["source_url"]]
            data = saved_path(page["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != probe["source_sha256"]:
                raise ValueError("Original page hash changed since inspection")
            known_anchors = {ref["anchor"] for ref in manifest["references"]
                             if ref["page_url"] == probe["source_url"] and ref["anchor"]}
            original = section_html(_decode(data, page["content_type"]), probe["anchor"], known_anchors)
            # read_text() normalizes CRLF on Windows, which would break exact
            # source comparison and make saved character offsets misleading.
            fragment = saved_path(probe["section_html_path"]).read_bytes().decode("utf-8")
            if fragment != original:
                raise ValueError("Saved section no longer matches original page")
            result = extract_html_questions(fragment, SourceRef(probe["source_url"], probe["anchor"]))
            report["sets"].append({**asdict(result), "section_html_path": probe["section_html_path"],
                                   "age_group": probe["age_group"], "difficulty": probe["difficulty"], "date": probe["date"]})
        except (OSError, ValueError, KeyError) as exc:
            report["errors"].append({"anchor": probe["anchor"], "error": f"{type(exc).__name__}: {exc}"})

    output_dir = data_dir / "extracted"
    report_path = output_dir / "html_questions.json"
    _save(report_path, report)
    # A plain local review artifact: HTML tags in the source are escaped, not executed.
    rows = ["<!doctype html><html lang='en'><meta charset='utf-8'><title>HTML extraction review</title>",
            "<style>body{font:16px system-ui;margin:32px;max-width:1500px}table{width:100%;table-layout:fixed;border-collapse:collapse}th,td{border:1px solid #bbb;padding:12px;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}h2{margin-top:40px}</style>",
            "<h1>HTML question extraction: source and result</h1><p>First two questions per section. No translation or model calls. Source coverage checks character retention, not semantic correctness.</p>"]
    for item in report["sets"]:
        rows.append(f"<h2>{escape(item['source']['anchor'])}: {len(item['questions'])} questions, {escape(item['status'])}</h2>")
        rows.append("<table><tr><th>Original question HTML</th><th>Extracted record</th></tr>")
        for question in item["questions"][:2]:
            metadata = f"Question {question['number']} | points: {question['points']} | author: {question['author']}"
            rows.append(f"<tr><td><pre>{escape(question['original_html'])}</pre></td><td><p>{escape(metadata)}</p><pre>{escape(question['text'])}</pre></td></tr>")
        rows.append("</table>")
        if item["trailing_html"].strip():
            rows.append(f"<h3>Preserved section note</h3><pre>{escape(item['trailing_html'])}</pre>")
    rows.append("</html>")
    (output_dir / "html_questions_review.html").write_text("\n".join(rows), encoding="utf-8")
    manifest["html_extraction"] = {"path": report_path.as_posix(), "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                                   "anchors": sorted(wanted), "sets": len(report["sets"]), "errors": len(report["errors"])}
    _save(manifest_path, manifest)
    return report


def extract_cached_pdf(data_dir: Path, document_urls: list[str]) -> dict:
    """Extract requested cached non-English PDF pages and save source-image evidence."""
    from dataclasses import replace
    from html import escape
    import pymupdf
    from .pdf_extractor import extract_pdf_page

    data_dir = Path(data_dir)
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inspection_path = saved_path(manifest["inspection"]["path"])
    if hashlib.sha256(inspection_path.read_bytes()).hexdigest() != manifest["inspection"]["sha256"]:
        raise ValueError("Inspection report no longer matches its recorded hash")
    inspection = json.loads(inspection_path.read_text(encoding="utf-8"))
    report = {"stage": "pdf_question_extraction", "sets": [], "skipped": [], "errors": [], "model_calls": 0}
    documents = {document["url"]: document for document in manifest["documents"]}
    output_dir = data_dir / "extracted"
    image_dir = output_dir / "pdf_source"
    for url in dict.fromkeys(document_urls):
        try:
            source = documents[url]
            if source["status"] != "downloaded":
                raise ValueError("PDF was not downloaded successfully")
            if hashlib.sha256(saved_path(source["path"]).read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError("Original PDF hash changed")
            probes = [probe for probe in inspection["pdf_pages"] if probe["source_url"] == url]
            if not probes:
                raise ValueError("No inspected PDF pages found")
            with pymupdf.open(saved_path(source["path"])) as document:
                for probe in probes:
                    if probe["source_sha256"] != source["sha256"]:
                        raise ValueError("PDF inspection belongs to a different source hash")
                    if probe["status"] != "ready_for_extraction":
                        report["skipped"].append({"source_url": url, "page": probe["page"], "reason": probe["status"]})
                        continue
                    page = document[probe["page"] - 1]
                    result = extract_pdf_page(page, url)
                    image_dir.mkdir(parents=True, exist_ok=True)
                    prefix = f"{source['sha256'][:16]}-p{result.page}"
                    image_path = image_dir / f"{prefix}.png"
                    page.get_pixmap(dpi=120).save(image_path)
                    questions = []
                    for question in result.questions:
                        crop_path = image_dir / f"{prefix}-q{question.number}.png"
                        page.get_pixmap(dpi=150, clip=pymupdf.Rect(question.bbox)).save(crop_path)
                        questions.append(replace(question, region_image=crop_path.as_posix()))
                    result = replace(result, questions=tuple(questions))
                    report["sets"].append({**asdict(result), "source_sha256": source["sha256"], "source_path": source["path"], "page_image": image_path.as_posix()})
        except (OSError, ValueError, KeyError, IndexError, RuntimeError) as exc:
            report["errors"].append({"source_url": url, "error": f"{type(exc).__name__}: {exc}"})
    report_path = output_dir / "pdf_questions.json"
    _save(report_path, report)
    rows = ["<!doctype html><html lang='en'><meta charset='utf-8'><title>PDF extraction review</title>",
            "<style>body{font:16px system-ui;margin:30px}table{width:100%;table-layout:fixed;border-collapse:collapse}td,th{border:1px solid #bbb;padding:12px;vertical-align:top}img{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}.warning{color:#922}h2{margin-top:40px}</style>",
            "<h1>PDF question extraction review</h1><p>Original Russian source crops beside candidate extracted records. No translation or paid model calls. Span coverage confirms retained text spans, not mathematical correctness. Uncertain notation stays needs_review.</p>"]
    for item in report["sets"]:
        page_image = saved_path(item["page_image"]).relative_to(output_dir).as_posix()
        rows.append(f"<h2>Page {item['page']}: {escape(str(item['age_group']))} {escape(str(item['difficulty']))} — {escape(item['status'])}</h2><p><a href='{escape(page_image, quote=True)}'>Open full original page</a></p>")
        rows.append("<table><tr><th>Original source region</th><th>Extracted record</th></tr>")
        for question in item["questions"]:
            crop = saved_path(question["region_image"]).relative_to(output_dir).as_posix()
            score_text = ", ".join(f"{score['subpart'] or 'whole question'}: {score['value']}" for score in question["scores"])
            warnings = "<ul class='warning'>" + "".join(f"<li>{escape(issue)}</li>" for issue in question["issues"]) + "</ul>" if question["issues"] else ""
            rows.append(f"<tr><td><img src='{escape(crop, quote=True)}' alt='Original question {question['number']}'></td><td><b>Question {question['number']}</b><p>Scores: {escape(score_text)}<br>Author: {escape(str(question['author']))}</p><pre>{escape(question['text'])}</pre>{warnings}<details><summary>Original spans and positions</summary><pre>{escape(json.dumps(question['spans'], ensure_ascii=False, indent=2))}</pre></details></td></tr>")
        rows.append("</table>")
    rows.append("</html>")
    (output_dir / "pdf_questions_review.html").write_text("\n".join(rows), encoding="utf-8")
    manifest["pdf_extraction"] = {"path": report_path.as_posix(), "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                                  "documents": list(dict.fromkeys(document_urls)), "page_sets": len(report["sets"]), "errors": len(report["errors"])}
    _save(manifest_path, manifest)
    return report
