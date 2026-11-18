# Program structure

Run `.venv/bin/python -m tournament_agent.main` from the repository root. The
program uses the saved delivery when it exists. For a new archive, it downloads
and inspects sources, extracts ready sets, and writes a plan. It does not make
paid calls by itself. The GitHub Actions workflow executes an approved plan.

| File | What it does |
| --- | --- |
| `models.py` | Shared records and saved path handling. |
| `main.py` | Runs the stages in order and saves extraction and preparation reports. |
| `archive_builder.py` | Starts at the fixed archive URL, records every source link, downloads each distinct page and PDF sequentially, caches original bytes, and checkpoints the manifest. |
| `webpage_extractor.py` | Reads HTML anchors and questions. Lingua checks actual prose in each section and PDF page before deciding whether a source is English. HTML question ranges and image references stay attached to the original HTML. |
| `pdf_extractor.py` | Uses PyMuPDF spans and coordinates to find question boundaries, scores, authors, and graphics. Saves question regions for source review. |
| `model_call.py` | Prepares statements, calls OpenRouter only with the approved plan and account ledger, records costs, and asks for bilingual review. |
| `verification.py` | Applies saved source reviews, typesets the four PDFs, builds the output manifest, checks source hashes, and audits archive coverage. |

## Source flow

1. `archive_builder.run` saves the assignment index, numbered linked pages,
   PDF links, and original files. Each URL is downloaded once. Failed URLs stay
   visible in the manifest.
2. `main.inspect_sources` checks cached HTML sections and PDF pages. Lingua
   labels confident English units for skipping. Unclear language is marked for
   review.
3. `main.extract_cached_html` and `main.extract_cached_pdf` call the two
   extractors. They save original source ranges, text spans, figures, and page
   crops alongside candidate question text. Missing or ambiguous boundaries
   remain review items.
4. `model_call.prepare_translation` checks hashes and source review decisions
   before creating the translation requests. `model_call.paid_main` prints the
   exact plan and maximum reservation; it sends requests only when given that
   plan hash and the canonical ledger checkout.
5. Translation and bilingual review results are saved after each statement.
   `verification.render_main` accepts reviewed English, applies recorded source
   corrections, typesets the four PDFs, and saves page images for visual QA.
6. `verification.verify_main` checks cached source and report hashes, HTML
   question ranges, PDF span coverage, source images, and model accounting.

Figures remain connected to their source crops. A question with unresolved
figure placement or mathematical notation cannot silently become a reviewed
translation or accepted PDF.
