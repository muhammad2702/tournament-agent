"""Account for discovered source references alongside this run's reviewed outputs."""

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from .models import saved_path
from datetime import date
from hashlib import sha256
import shutil
import subprocess
from tempfile import TemporaryDirectory
import pymupdf
from .archive_builder import _save
import hashlib
from .model_call import ModelGateway
from collections import Counter
from .main import extract_cached_html, extract_cached_pdf





def build_manifest(data_dir: Path, report: dict, outputs: list[dict]) -> dict:
    source = json.loads((data_dir / "manifest.json").read_bytes())
    inspect = json.loads(saved_path(source["inspection"]["path"]).read_bytes())
    pages = {item["url"]: item for item in source["pages"]}
    html = {(item["source_url"], item["anchor"]): item for item in inspect["html_sections"]}
    pdf_pages = defaultdict(list)
    for item in inspect["pdf_pages"]:
        pdf_pages[item["source_url"]].append(item)
    by_location = {(item["source_url"], item["anchor"], item["page"]): item for item in outputs}
    records = []
    for ref in source["references"]:
        url, anchor = ref["page_url"], ref["anchor"]
        download = pages.get(url, {})
        probe = html.get((url, anchor), {})
        result = by_location.get((url, anchor, None), {})
        status = result.get("status") or probe.get("status", "needs_review")
        if download.get("status") == "failed":
            status = "download_failed"
        records.append({"kind": "html", "source_url": url, "anchor": anchor, "label": ref["label"],
                        "sha256": download.get("sha256"), **_details(probe, result), "status": status})
    pdf_refs = defaultdict(list)
    for ref in source["document_references"]:
        pdf_refs[ref["url"]].append(ref)
    seen_hashes = {}
    documents = []
    for download in source["documents"]:
        url, digest = download["url"], download.get("sha256")
        refs = pdf_refs[url]
        name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        evidence = " ".join([name, *(ref["label"] for ref in refs)])
        filename = re.sub(r"[_-]", " ", name.lower())
        solution_pattern = r"\b(?:solutions?|answers?|решени\w*|ответ[ыа]?)\b"
        doc_status = "download_failed" if download["status"] == "failed" else "inspected"
        if doc_status == "inspected" and re.search(solution_pattern, evidence, re.I):
            doc_status = ("excluded_solution" if re.search(solution_pattern, filename, re.I)
                          and not re.search(r"problems?|задач", filename, re.I) else "needs_scope_review")
        document = {"source_url": url, "sha256": digest, "references": refs, "pages": [], "status": doc_status}
        if digest is not None:
            first = seen_hashes.setdefault(digest, url)
            if first != url:
                document["same_content_as"] = first
        for probe in pdf_pages[url]:
            result = by_location.get((url, "", probe["page"]), {})
            status = doc_status if doc_status != "inspected" else result.get("status", probe["status"])
            document["pages"].append({"page": probe["page"], **_details(probe, result), "status": status})
        if doc_status == "inspected" and not document["pages"]:
            document["status"] = "needs_review"
        documents.append(document)
    return {"index": source["index"], "html_references": records, "pdf_documents": documents,
            "inspection_errors": inspect["errors"], "outputs": outputs, "model_accounting": report["model_accounting"],
            "repeated_problem_statements": _repeated_statements(report.get("sets", [])),
            "counts": {"html_references": len(records), "pdf_references": sum(len(doc["references"]) for doc in documents),
                       "pdf_documents": len(documents), "html_statuses": dict(Counter(row["status"] for row in records)),
                       "pdf_page_statuses": dict(Counter(page["status"] for doc in documents for page in doc["pages"]))}}


def _details(probe: dict, output: dict) -> dict:
    return {"date": output.get("date", probe.get("date")), "age_group": output.get("age_group", probe.get("age_group")),
            "difficulty": output.get("difficulty", probe.get("difficulty")), "question_count": output.get("question_count"),
            "inspection_status": probe.get("status"), "language": (probe.get("language") or {}).get("language"),
            "output_path": output.get("path"), "output_sha256": output.get("sha256")}


def _repeated_statements(sets: list[dict]) -> list[list[str]]:
    ids_by_text = defaultdict(list)
    for item in sets:
        for row in item["statements"]:
            if row["number"] is not None:
                statement = row["source"]
                ids_by_text[" ".join(statement["text"].split())].append(statement["id"])
    return [ids for ids in ids_by_text.values() if len(ids) > 1]



# Render reviewed sets to PDF and rasterize every generated page for QA.





_INLINE = re.compile(
    r"\\underbrace\{(?P<expression>[^{}]+)\}_\{(?P<variable>[A-Za-z])\s*\\text\{(?P<label>[^{}]+)\}\}"
    r"|\^(?:\{(?P<sup>[^{}]+)\}|(?P<digit>\d+))|_\{(?P<sub>[^{}]+)\}")

_DOCUMENT = r"""
\documentclass[11pt,a4paper]{article}
\usepackage[margin=19mm]{geometry}
\usepackage{fontspec}
\usepackage{amsmath}
\setmainfont{DejaVu Serif}
\setlength{\parindent}{0pt}
\setlength{\parskip}{4pt}
\raggedbottom
\newsavebox{\questionbox}
\newenvironment{questionblock}
 {\begin{lrbox}{\questionbox}\begin{minipage}{\linewidth}\raggedright}
 {\end{minipage}\end{lrbox}%
  \ifdim\dimexpr\ht\questionbox+\dp\questionbox\relax>\textheight
     \PackageError{tournament}{A question exceeds one page}{Review the question layout.}%
  \fi
  \noindent\usebox{\questionbox}\par\medskip}
\begin{document}
{\LARGE\bfseries Tournament of Towns}\par
"""
_ESCAPE = str.maketrans({"#": r"\#", "$": r"\$", "%": r"\%", "&": r"\&", "_": r"\_",
                         "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
                         "^": r"\textasciicircum{}", "\\": r"\textbackslash{}"})


def _plain(text: str) -> str:
    if "\\" in text or "```" in text or re.search(r"\^[\w{]|_\{", text):
        raise ValueError("Unrecognized mathematical or Markdown markup in statement")
    return text.translate(_ESCAPE)


def statement_tex(text: str) -> str:
    parts = []
    start = 0
    for match in _INLINE.finditer(text):
        parts.append(_plain(text[start:match.start()]))
        if match["expression"] is not None:
            expression = r"\ldots".join(_plain(part) for part in match["expression"].split(r"\ldots"))
            label = _plain(match["label"].strip())
            parts.append(r"\(\underbrace{" + expression + "}_{" + match["variable"]
                         + r"\,\text{" + label + r"}}\)")
        elif match["sub"] is not None:
            parts.append(r"\textsubscript{" + _plain(match["sub"]) + "}")
        else:
            parts.append(r"\textsuperscript{" + _plain(match["sup"] or match["digit"]) + "}")
        start = match.end()
    parts.append(_plain(text[start:]))
    return "".join(parts)


def set_tex(item: dict) -> str:
    title = f"{item['age_group'].title()} {item['difficulty'].title()}"
    day = date.fromisoformat(item["date"])
    heading = f"{day.day} {day.strftime('%B')} {day.year} · {title}"
    sections = [_DOCUMENT, _plain(heading) + r"\par\medskip\hrule\bigskip"]
    for row in item["statements"]:
        if row["status"] != "review_passed" or not row.get("english"):
            raise ValueError(f"Statement lacks reviewed English: {row['source']['id']}")
        if row.get("graphics") and row.get("graphics_kind") != "notation":
            raise ValueError(f"Figure placement requires source review: {row['source']['id']}")
        if row["number"] is None:
            label, points = "Note", ""
        else:
            label = f"Question {row['number']}"
            scores = []
            for score in row["scores"]:
                subpart = (score["subpart"] or "").translate(str.maketrans("аб", "ab"))
                scores.append(f"{subpart + ' ' if subpart else ''}{score['value']} points")
            points = r"\quad " + _plain(" · ".join(scores))
        english = row["english"]
        if row["number"] is None:
            english = re.sub(r"^\s*Note\s*:\s*", "", english, count=1, flags=re.I)
        body = statement_tex(english)
        body = re.sub(r"(?m)\n(?=[a-z]\)\s)", r"\\par\n", body)
        sections.append(r"\begin{questionblock}")
        sections.append(r"\textbf{" + label + "}" + points + r"\par\nobreak")
        sections.append(body + r"\par")
        if row["author"]:
            sections.append(r"\nobreak{\raggedleft\itshape " + _plain(row["author"]) + r"\par}")
        sections.append(r"\end{questionblock}")
    return "\n".join([*sections, r"\end{document}"]) + "\n"


def print_pdf(item: dict, output: Path, engine: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as folder:
        temporary = Path(folder).resolve()
        tex_path, draft = temporary / "set.tex", temporary / "set.pdf"
        tex_path.write_text(set_tex(item), encoding="utf-8")
        result = subprocess.run([engine, "-halt-on-error", "-interaction=nonstopmode", "-no-shell-escape",
                                 tex_path.name], cwd=temporary, capture_output=True, timeout=60)
        if result.returncode or not draft.is_file():
            details = result.stdout.decode(errors="replace").splitlines()
            error = next((line[2:] for line in details if line.startswith("! ")), "no TeX error line")
            raise ValueError(f"XeLaTeX PDF export failed ({error})")
        if b"Missing character:" in (temporary / "set.log").read_bytes():
            raise ValueError("One or more characters are missing from the renderer fonts")
        with pymupdf.open(draft) as document:
            if not document.page_count:
                raise ValueError("Renderer produced an empty PDF")
            pages = [page.get_text() for page in document]
            numbers = [int(n) for text in pages for n in re.findall(r"(?m)^Question (\d+)\b", text)]
            notes = sum(len(re.findall(r"(?m)^Note$", text)) for text in pages)
            expected_notes = sum(row["number"] is None for row in item["statements"])
            if (not all(text.strip() for text in pages) or numbers != list(range(1, item["question_count"] + 1))
                    or notes != expected_notes):
                raise ValueError("Rendered PDF failed page, question-number or note validation")
        draft.replace(output)


def render_sets(report: dict, output: Path, qa_dir: Path, engine: str) -> list[dict]:
    results = []
    for item in report["sets"]:
        key = {field: item[field] for field in ("source_url", "anchor", "page", "date", "age_group", "difficulty", "question_count")}
        if item["status"] != "review_passed":
            results.append({**key, "status": "needs_review"})
            continue
        try:
            group, difficulty = item["age_group"], item["difficulty"]
            if group not in {"junior", "senior"} or difficulty not in {"basic", "advanced"}:
                raise ValueError("Set classification is invalid")
            relative = Path(group) / difficulty / f"{date.fromisoformat(item['date'])}.pdf"
            if any(result.get("path") == relative.as_posix() for result in results):
                raise ValueError("Another set has the same date, age group and difficulty")
            path = output / relative
            print_pdf(item, path, engine)
            images = []
            with pymupdf.open(path) as document:
                for page in document:
                    image = qa_dir / group / difficulty / f"{relative.stem}-p{page.number + 1}.png"
                    image.parent.mkdir(parents=True, exist_ok=True)
                    page.get_pixmap(dpi=144).save(image)
                    images.append(image.relative_to(qa_dir).as_posix())
            manual_reviews = [row["source"]["id"] for row in item["statements"] if row.get("manual_review")]
            results.append({**key, "status": "rendered_needs_visual_qa", "path": relative.as_posix(),
                            "sha256": sha256(path.read_bytes()).hexdigest(), "page_count": len(images),
                            "page_images": images, "manual_reviews": manual_reviews})
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            results.append({**key, "status": "render_failed", "error": str(exc)})
    return results


def apply_manual_reviews(report: dict, decisions: dict) -> None:
    applied = set()
    for item in report["sets"]:
        for row in item["statements"]:
            source = row["source"]
            decision = decisions.get(source["id"])
            if decision is None:
                continue
            review = row.get("review", {})
            replacement = decision.get("replacement")
            expected_status = "review_passed" if replacement else "invalid_review"
            if (row["status"] != expected_status
                    or review.get("review", {}).get("verdict") != "pass"
                    or review.get("validation_errors") != decision["validation_errors"]
                    or source["source_sha256"] != decision["source_sha256"]
                    or sha256(row["english"].encode()).hexdigest() != decision["english_sha256"]
                    or row["translation"]["call_id"] != decision["translation_call_id"]
                    or review["call_id"] != decision["review_call_id"]
                    or decision["source_evidence"] not in source["text"]
                    or decision["english_evidence"] not in row["english"]):
                raise ValueError(f"Manual review evidence no longer matches {source['id']}")
            if replacement:
                before, after = replacement
                if row["english"].count(before) != 1:
                    raise ValueError(f"Reviewed replacement is ambiguous for {source['id']}")
                row["english"] = row["english"].replace(before, after, 1)
            row.update(status="review_passed", manual_review=decision)
            applied.add(source["id"])
        if item["statements"] and all(row["status"] == "review_passed" for row in item["statements"]):
            item["status"] = "review_passed"
    report["manual_reviews_applied"] = sorted(applied)


def render_main(data_dir=Path("data"), results_path=Path("data/translation_run.json"),
                output=Path("output"), qa_dir=Path("qa"), engine=None) -> int:
    engine = engine or shutil.which("xelatex")
    if not engine:
        raise ValueError("XeLaTeX is required for PDF export")
    report = json.loads(results_path.read_bytes())
    decisions_path = data_dir / "qa_decisions.json"
    if decisions_path.is_file():
        apply_manual_reviews(report, json.loads(decisions_path.read_bytes()))
    outputs = render_sets(report, output, qa_dir, engine)
    _save(qa_dir / "render_report.json", {"outputs": outputs, "model_accounting": report["model_accounting"]})
    _save(output / "manifest.json", build_manifest(data_dir, report, outputs))
    for item in outputs:
        print(f"{item['age_group']}/{item['difficulty']} {item['date']} | {item['status']}"
              + (f" | {item['error']}" if "error" in item else ""))
    return int(not outputs or any(item["status"] != "rendered_needs_visual_qa" for item in outputs))





# Read-only verification of the real saved handoff, without API calls.




def verify_main() -> int:
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





# Try the existing extractors on every eligible unit in the cached archive.




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


def audit_main(data_dir=Path("data"), report_path=Path("qa/archive_audit.json")) -> int:
    result = audit_archive(data_dir)
    _save(report_path, result)
    print(f"HTML {result['html']['statuses']} | PDF {result['pdf']['statuses']} | report {report_path}")
    return int(bool(result["html"]["errors"] or result["pdf"]["errors"]))
