"""Create reviewed English PDFs and check files recorded in SQLite."""

from hashlib import sha256
from pathlib import Path
import sqlite3
import textwrap

import pymupdf

from .models import saved_path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def render_ready(database=Path("data/archive.sqlite")):
    """Create an output only when every saved question in that set passed review."""
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        paths = db.execute("SELECT DISTINCT output_path FROM source WHERE kind='question' AND output_path IS NOT NULL").fetchall()
        for (output,) in paths:
            questions = db.execute("""SELECT * FROM source WHERE kind='question' AND output_path=?
                ORDER BY question_number, id""", (output,)).fetchall()
            if not questions or any(row["status"] not in ("review_passed", "verified") for row in questions):
                continue
            destination = Path(output)
            if destination.is_file():
                continue
            if any("\\" in row["english"] or "```" in row["english"] for row in questions):
                print(f"Layout needs review: {output}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            pdf = pymupdf.open()
            title = questions[0]["label"].title()
            for row in questions:
                page = pdf.new_page(width=595, height=842)
                page.insert_font(fontname="dejavu", fontfile=FONT)
                page.insert_text((42, 54), "Tournament of Towns", fontname="dejavu", fontsize=17)
                page.insert_text((42, 78), title, fontname="dejavu", fontsize=11)
                name = f"Question {row['question_number']}" if row["question_number"] is not None else "Note"
                page.insert_text((42, 112), name, fontname="dejavu", fontsize=13)
                y = 145
                for paragraph in row["english"].splitlines():
                    for line in textwrap.wrap(paragraph, width=83) or [""]:
                        if y > 785:
                            page = pdf.new_page(width=595, height=842)
                            page.insert_font(fontname="dejavu", fontfile=FONT)
                            y = 55
                        page.insert_text((42, y), line, fontname="dejavu", fontsize=11)
                        y += 17
                if row["points"]:
                    page.insert_text((42, min(y + 20, 805)), f"Points: {row['points']}",
                                     fontname="dejavu", fontsize=10)
            pdf.save(destination)
            pdf.close()
            db.execute("UPDATE source SET status='verified' WHERE kind='question' AND output_path=?", (output,))
            print(f"Created {destination}")
        db.commit()


def verify_database(database=Path("data/archive.sqlite")):
    """Check each distinct original download and each generated PDF."""
    with sqlite3.connect(database) as db:
        sources = db.execute("""SELECT path,sha256 FROM source
            WHERE kind IN ('index','html','pdf') AND path IS NOT NULL
            GROUP BY url""").fetchall()
        outputs = db.execute("""SELECT DISTINCT output_path FROM source
            WHERE kind='question' AND output_path IS NOT NULL
            AND status IN ('review_passed','verified')""").fetchall()
    for path, expected in sources:
        if sha256(saved_path(path).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Original source changed: {path}")
    for (path,) in outputs:
        if not Path(path).is_file():
            raise ValueError(f"Missing reviewed PDF: {path}")
        with pymupdf.open(path) as document:
            if document.page_count == 0:
                raise ValueError(f"Empty reviewed PDF: {path}")
    return len(sources), len(outputs)
