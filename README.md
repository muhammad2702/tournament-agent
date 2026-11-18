# Tournament of Towns English PDFs

Run the program from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m tournament_agent.main
```

The program reads the fixed [Tournament archive](https://www.turgor.ru/en/problems/allproblems.php), saves each page and PDF, checks its actual language with Lingua, extracts supported questions, and checks the English PDFs. It processes the archive on every run. Saved original files are reused; the existence of four output PDFs does not bypass the archive.

The four checked 2009 examples are:

| Set | Output |
| --- | --- |
| Junior Basic | [PDF](output/junior/basic/2009-10-18.pdf) |
| Senior Basic | [PDF](output/senior/basic/2009-10-18.pdf) |
| Junior Advanced | [PDF](output/junior/advanced/2009-10-25.pdf) |
| Senior Advanced | [PDF](output/senior/advanced/2009-10-25.pdf) |

