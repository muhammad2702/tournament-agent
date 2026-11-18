"""Account for discovered source references alongside this run's reviewed outputs."""

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from .paths import saved_path


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
            "counts": {"html_references": len(records), "pdf_references": sum(len(doc["references"]) for doc in documents),
                       "pdf_documents": len(documents), "html_statuses": dict(Counter(row["status"] for row in records)),
                       "pdf_page_statuses": dict(Counter(page["status"] for doc in documents for page in doc["pages"]))}}


def _details(probe: dict, output: dict) -> dict:
    return {"date": output.get("date", probe.get("date")), "age_group": output.get("age_group", probe.get("age_group")),
            "difficulty": output.get("difficulty", probe.get("difficulty")), "question_count": output.get("question_count"),
            "inspection_status": probe.get("status"), "language": (probe.get("language") or {}).get("language"),
            "output_path": output.get("path"), "output_sha256": output.get("sha256")}
