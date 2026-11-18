"""Walk the archive, extract questions, translate approved sources, and check PDFs."""

from pathlib import Path
import sqlite3

import pymupdf
from bs4 import UnicodeDammit

from .archive_builder import build_archive
from .models import SourceRef, saved_path
from .webpage_extractor import inspect_html, inspect_pdf, extract_html_questions
from .pdf_extractor import extract_pdf_page
from .model_call import translate_ready
from .verification import render_ready, verify_database


def main():
    database = Path("data/archive.sqlite")
    print("Archive:", build_archive(database))
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        html_rows = db.execute("SELECT * FROM source WHERE kind='html'").fetchall()
        anchors_by_url = {}
        for row in html_rows:
            if row["anchor"]:
                anchors_by_url.setdefault(row["url"], set()).add(row["anchor"])
        for row in html_rows:
            if not row["path"] or not row["anchor"]:
                continue
            try:
                page = UnicodeDammit(saved_path(row["path"]).read_bytes()).unicode_markup
                ref = SourceRef(row["url"], row["anchor"], row["label"])
                probe, fragment = inspect_html(page, ref, anchors_by_url[row["url"]])
                db.execute("UPDATE source SET language=? WHERE id=?", (probe.language.language, row["id"]))
                if probe.status != "ready_for_extraction":
                    if row["status"] != "verified":
                        db.execute("UPDATE source SET status=? WHERE id=?", (probe.status, row["id"]))
                    continue
                questions = extract_html_questions(fragment, ref)
                if not questions.coverage_verified or not all((probe.age_group, probe.difficulty, probe.date)):
                    db.execute("UPDATE source SET status='needs_review' WHERE id=?", (row["id"],))
                    continue
                output = f"output/{probe.age_group}/{probe.difficulty}/{probe.date}.pdf"
                db.execute("UPDATE source SET problem_count=? WHERE id=?", (len(questions.questions), row["id"]))
                for question in questions.questions:
                    if db.execute("SELECT 1 FROM source WHERE kind='question' AND anchor=?", (question.id,)).fetchone():
                        continue
                    status = "needs_review" if question.issues or question.images else "ready_for_translation"
                    db.execute("""INSERT INTO source
                        (kind,url,anchor,label,path,sha256,status,language,output_path,
                         question_number,author,points,source_text)
                        VALUES('question',?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (row["url"], question.id, f"{probe.age_group} {probe.difficulty} {probe.date}",
                         row["path"], row["sha256"], status, probe.language.language, output,
                         question.number, question.author, str(question.points), question.text))
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                print(f"HTML needs review: {row['url']}#{row['anchor']}: {error}")
                db.execute("UPDATE source SET status='needs_review' WHERE id=? AND status!='verified'", (row["id"],))
        pdf_rows = db.execute("SELECT url,path,sha256 FROM source WHERE kind='pdf' AND path IS NOT NULL GROUP BY url").fetchall()
        for source in pdf_rows:
            try:
                probes = inspect_pdf(saved_path(source["path"]), source["url"])
                with pymupdf.open(saved_path(source["path"])) as document:
                    for probe in probes:
                        anchor = f"p{probe.page}"
                        old = db.execute("SELECT id,status FROM source WHERE kind='pdf_page' AND url=? AND anchor=?",
                                         (source["url"], anchor)).fetchone()
                        if old and old["status"] == "verified":
                            continue
                        if old is None:
                            db.execute("""INSERT INTO source(kind,url,anchor,path,sha256,status,language)
                                VALUES('pdf_page',?,?,?,?,?,?)""",
                                (source["url"], anchor, source["path"], source["sha256"],
                                 probe.status, probe.language.language))
                        else:
                            db.execute("UPDATE source SET status=?,language=? WHERE id=?",
                                       (probe.status, probe.language.language, old["id"]))
                        if probe.status != "ready_for_extraction":
                            continue
                        page = document[probe.page - 1]
                        result = extract_pdf_page(page, source["url"])
                        if not all((result.age_group, result.difficulty, result.date)):
                            continue
                        output = f"output/{result.age_group}/{result.difficulty}/{result.date}.pdf"
                        for question in result.questions:
                            if db.execute("SELECT 1 FROM source WHERE kind='question' AND anchor=?", (question.id,)).fetchone():
                                continue
                            crop = Path("data/crops") / f"{source['sha256'][:16]}-p{probe.page}-q{question.number}.png"
                            crop.parent.mkdir(parents=True, exist_ok=True)
                            page.get_pixmap(dpi=150, clip=pymupdf.Rect(question.bbox)).save(crop)
                            status = "needs_review" if question.issues or question.graphics else "ready_for_translation"
                            db.execute("""INSERT INTO source
                                (kind,url,anchor,label,path,sha256,status,language,output_path,
                                 question_number,author,points,source_text,crop_path)
                                VALUES('question',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (source["url"], question.id,
                                 f"{result.age_group} {result.difficulty} {result.date}",
                                 source["path"], source["sha256"], status, probe.language.language,
                                 output, question.number, question.author,
                                 ", ".join(str(score.value) for score in question.scores),
                                 question.text, crop.as_posix()))
            except (OSError, ValueError, RuntimeError) as error:
                print(f"PDF needs review: {source['url']}: {error}")
        db.commit()
    translate_ready(database)
    render_ready(database)
    print("Verified original files and PDFs:", verify_database(database))


if __name__ == "__main__":
    main()
