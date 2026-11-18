# Direct program flow

`main.py` always calls `archive_builder.py` on the fixed assignment index. The archive builder reads its links, visits each numbered page, downloads each distinct PDF, and records every source in one SQLite `source` table with the path to its original file.

`main.py` then sends HTML sections to `webpage_extractor.py` and PDF pages to `pdf_extractor.py`. Lingua checks the actual source text before English sources are skipped. Ambiguous boundaries, figures, and notation stay marked for review. Extracted question rows are saved in the same SQLite table.

`model_call.py` reads unfinished question rows. It contacts OpenRouter only when a key and an explicit new-call cap are present. A translation is saved before its separate bilingual review, so the program can resume. The wire protocol uses JSON; no JSON files are saved.

`verification.py` creates PDFs only from fully reviewed questions and checks the original source hashes and output PDFs. `models.py` holds the extractor records and cross-platform path reader. These are the seven Python files in the program.
