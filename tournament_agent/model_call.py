"""Make real OpenRouter translation and review calls for unfinished questions."""

import json  # OpenRouter's HTTP protocol uses JSON; the program saves no JSON files.
import os
from pathlib import Path
import re
import sqlite3
from urllib.request import Request, urlopen


def translate_ready(database=Path("data/archive.sqlite")):
    """Resume saved questions; new spending requires a key and an explicit call cap."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    limit = int(os.environ.get("MAX_NEW_CALLS", "0"))
    if not key or limit <= 0:
        with sqlite3.connect(database) as db:
            pending = db.execute("SELECT count(*) FROM source WHERE kind='question' AND status IN ('ready_for_translation','awaiting_review')").fetchone()[0]
        print(f"Questions awaiting real model calls: {pending}. No key or approved call cap supplied.")
        return

    translation_prompt = Path("prompts/translate_set.md").read_text(encoding="utf-8")
    review_prompt = Path("prompts/review_simple.md").read_text(encoding="utf-8")
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""SELECT * FROM source WHERE kind='question'
            AND status IN ('ready_for_translation','awaiting_review') ORDER BY id""").fetchall()
        calls = 0
        for row in rows:
            if calls >= limit:
                break
            english = row["english"]
            if row["status"] == "ready_for_translation":
                payload = {"model": "google/gemini-2.5-flash-lite", "max_tokens": 1000, "messages": [
                    {"role": "system", "content": translation_prompt},
                    {"role": "user", "content": row["source_text"]}]}
                request = Request("https://openrouter.ai/api/v1/chat/completions",
                                  data=json.dumps(payload).encode(), headers={
                                      "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
                # An interrupted request stays uncertain so a resume never pays twice blindly.
                db.execute("UPDATE source SET status='translation_sent' WHERE id=?", (row["id"],))
                db.commit()
                with urlopen(request, timeout=90) as response:
                    english = json.load(response)["choices"][0]["message"]["content"].strip()
                calls += 1
                if not english or sorted(re.findall(r"\d+", english)) != sorted(re.findall(r"\d+", row["source_text"])):
                    db.execute("UPDATE source SET status='needs_review' WHERE id=?", (row["id"],))
                    db.commit()
                    continue
                db.execute("UPDATE source SET english=?,status='awaiting_review' WHERE id=?", (english, row["id"]))
                db.commit()
            if calls >= limit:
                break
            payload = {"model": "google/gemini-2.5-flash", "max_tokens": 256, "messages": [
                {"role": "system", "content": review_prompt},
                {"role": "user", "content": f"Original:\n{row['source_text']}\n\nEnglish:\n{english}"}]}
            request = Request("https://openrouter.ai/api/v1/chat/completions",
                              data=json.dumps(payload).encode(), headers={
                                  "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            db.execute("UPDATE source SET status='review_sent' WHERE id=?", (row["id"],))
            db.commit()
            with urlopen(request, timeout=90) as response:
                verdict = json.load(response)["choices"][0]["message"]["content"].strip()
            calls += 1
            status = "review_passed" if verdict == "PASS" else "needs_review"
            db.execute("UPDATE source SET status=? WHERE id=?", (status, row["id"]))
            db.commit()
            print(f"{row['anchor']}: {status}")
        print(f"Real OpenRouter calls made: {calls}")
