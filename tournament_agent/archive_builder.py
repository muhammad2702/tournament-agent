"""Save the fixed Tournament archive in one SQLite table."""

from hashlib import sha256
from pathlib import Path
import re
import sqlite3
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

ARCHIVE_URL = "https://www.turgor.ru/en/problems/allproblems.php"


def build_archive(db_path=Path("data/archive.sqlite")):
    """Read the index, numbered pages and their PDFs one at a time."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = db_path.parent / "raw"
    raw.mkdir(exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS source (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, url TEXT NOT NULL,
            anchor TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',
            path TEXT, sha256 TEXT, status TEXT NOT NULL,
            language TEXT, problem_count INTEGER, english TEXT, output_path TEXT,
            question_number INTEGER, author TEXT, points TEXT, source_text TEXT,
            crop_path TEXT)""")
        queue = [("index", ARCHIVE_URL, "", "")]
        visited = set()
        while queue:
            kind, reference, anchor, label = queue.pop(0)
            url, fragment = urldefrag(reference)
            anchor = anchor or fragment
            old = db.execute(
                "SELECT id, path, sha256 FROM source WHERE kind=? AND url=? AND anchor=? AND label=? LIMIT 1",
                (kind, url, anchor, label),
            ).fetchone()
            path = raw / (sha256(url.encode()).hexdigest() + (".pdf" if kind == "pdf" else ".html"))
            data = None
            if path.is_file():
                data = path.read_bytes()
            elif old and old[1] and Path(old[1].replace("\\", "/")).is_file():
                path = Path(old[1].replace("\\", "/"))
                data = path.read_bytes()
            if data is None:
                try:
                    with urlopen(Request(url, headers={"User-Agent": "TournamentAgent/1.0"}), timeout=30) as response:
                        data = response.read()
                    if kind == "pdf" and not data.lstrip().startswith(b"%PDF-"):
                        raise ValueError("Download is not a PDF")
                    path.write_bytes(data)
                except (OSError, ValueError) as error:
                    if old:
                        db.execute("UPDATE source SET status='failed' WHERE id=?", (old[0],))
                    else:
                        db.execute("INSERT INTO source(kind,url,anchor,label,status) VALUES(?,?,?,?,'failed')",
                                   (kind, url, anchor, label))
                    print(f"Archive download failed: {url}: {error}")
                    continue
            digest = sha256(data).hexdigest()
            if old:
                db.execute("UPDATE source SET path=?, sha256=?, status=CASE WHEN status IN ('verified', 'review_passed') THEN status ELSE 'downloaded' END WHERE id=?",
                           (path.as_posix(), digest, old[0]))
            else:
                db.execute("INSERT INTO source(kind,url,anchor,label,path,sha256,status) VALUES(?,?,?,?,?,?,'downloaded')",
                           (kind, url, anchor, label, path.as_posix(), digest))
            if kind == "pdf" or url in visited:
                continue
            visited.add(url)
            soup = BeautifulSoup(data, "html.parser")
            for tag in soup.find_all("a", href=True):
                target = urljoin(url, tag["href"].strip())
                target_url, target_anchor = urldefrag(target)
                parts = urlsplit(target_url)
                if parts.netloc.lower() != urlsplit(ARCHIVE_URL).netloc.lower():
                    continue
                text = tag.get_text(" ", strip=True)
                if kind == "index" and re.search(r"/\d+/index\.php$", parts.path, re.I):
                    queue.append(("html", target_url, target_anchor, text))
                elif parts.path.lower().endswith(".pdf"):
                    queue.append(("pdf", target_url, target_anchor, text))
        db.commit()
        return db.execute("SELECT kind, count(*) FROM source GROUP BY kind").fetchall()
