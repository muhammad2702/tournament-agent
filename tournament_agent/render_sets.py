"""Render reviewed sets to PDF and rasterize every generated page for QA."""

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

import pymupdf

from .pipeline import _save
from .reconcile import build_manifest


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
        body = statement_tex(row["english"])
        body = re.sub(r"(?m)\n(?=[a-z]\)\s)", r"\\par\n", body)
        sections.append(r"\textbf{" + label + "}" + points + r"\par\nobreak")
        sections.append(body + r"\par")
        if row["author"]:
            sections.append(r"\nobreak{\raggedleft\itshape " + _plain(row["author"]) + r"\par}")
        sections.append(r"\medskip")
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
            results.append({**key, "status": "rendered_needs_visual_qa", "path": relative.as_posix(),
                            "sha256": sha256(path.read_bytes()).hexdigest(), "page_count": len(images),
                            "page_images": images})
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            results.append({**key, "status": "render_failed", "error": str(exc)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--results", type=Path, default=Path("data/translation_run.json"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--qa", type=Path, default=Path("qa"))
    parser.add_argument("--engine", default=shutil.which("xelatex"))
    args = parser.parse_args()
    if not args.engine:
        parser.error("XeLaTeX is required for PDF export")
    report = json.loads(args.results.read_bytes())
    outputs = render_sets(report, args.output, args.qa, args.engine)
    _save(args.qa / "render_report.json", {"outputs": outputs, "model_accounting": report["model_accounting"]})
    _save(args.output / "manifest.json", build_manifest(args.data_dir, report, outputs))
    for item in outputs:
        print(f"{item['age_group']}/{item['difficulty']} {item['date']} | {item['status']}"
              + (f" | {item['error']}" if "error" in item else ""))
    return int(not outputs or any(item["status"] != "rendered_needs_visual_qa" for item in outputs))


if __name__ == "__main__":
    raise SystemExit(main())
