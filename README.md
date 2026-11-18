# Tournament of Towns English PDFs

Run the program from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m tournament_agent.main
```

The program reads the fixed [Tournament archive](https://www.turgor.ru/en/problems/allproblems.php), saves each page and PDF, checks its actual language with Lingua, extracts supported questions, and checks the English PDFs. It processes the archive on every run. Saved original files are reused; the existence of four output PDFs does not bypass the archive.

The **only local record store** is [archive.sqlite](data/archive.sqlite). Its single `source` table links each archive reference or question to its original file path, source hash, status, language, English text, and output PDF path. The table also shows sources that need review. There is no local manifest or JSON file.

The four checked 2009 examples are:

| Set | Output |
| --- | --- |
| Junior Basic | [PDF](output/junior/basic/2009-10-18.pdf) |
| Senior Basic | [PDF](output/senior/basic/2009-10-18.pdf) |
| Junior Advanced | [PDF](output/junior/advanced/2009-10-25.pdf) |
| Senior Advanced | [PDF](output/senior/advanced/2009-10-25.pdf) |

## Real model calls

There are no simulated model calls. With no key or approved call cap, the program lists unfinished questions and makes zero model calls. To allow **new paid calls**, set `OPENROUTER_API_KEY` and `MAX_NEW_CALLS` to the approved number before running the same command. The GitHub Actions manual workflow accepts this call cap and uses the `OpenRouterKey` secret. Translation and bilingual review use two calls per question and save their progress in SQLite, so an interrupted run can resume.

OpenRouter requires JSON for its HTTP request and response. The program does not write JSON files. See [QA.md](qa/QA.md) for the four source comparisons, page checks, and unresolved archive layouts.
