# Demonstration run and QA

## Run trace

The approved workflow translated and reviewed 23 questions and the Senior
Basic section note. It reused the earlier reviewed Junior Basic Q1. The
three PDF sets that passed the automated checks exported in GitHub Actions.
The Senior Basic section note was held because the model's conditions check
returned empty source and English quote fields despite a pass verdict.

The saved note was compared directly with the original HTML. It explains the
equation and introduces no condition. Its evidence and decision are recorded
with the source hash, English hash, and both call IDs. Source comparison also
restored the one-point-per-side condition in Senior Basic Q4, the multiplication
signs in Junior Advanced Q2, the word “ones” under the brace in Senior
Advanced Q4, and the unbounded finite number of moves in Senior Advanced Q7.
The original model responses and automated verdicts remain in the live trace.
The exporter applied the recorded decisions and generated all four final PDFs
offline, with no additional API calls or hand edits to the PDFs.
[Live log](https://github.com/muhammad2702/tournament-agent/actions/runs/44257730151/job/134643359260) · [Raw run](../data/translation_run.json) · [QA decisions](../data/qa_decisions.json)

## Source comparisons

- **Basic HTML.** Junior Basic Q1 retains the 9 total squares, the 1/3/5 colour
  counts, and equal same-colour versus unequal different-colour squares.
  Senior Basic Q3 retains the four cubes and `100^{100}`. Its explanatory
  note is present once after Q5. Senior Basic Q4 explicitly marks one point
  on each side. [Original Basic extraction](../data/extracted/html_questions.json)
- **Advanced PDF.** Junior Advanced Q3 retains both squares in
  `(a+b^{2})(b+a^{2})`. Junior Advanced Q5 keeps the two implications in their
  source order, with 2 points for a) and 7 points for b). Senior Advanced Q4
  brackets the final factor and labels it “n ones.” Senior Advanced Q7 asks
  which `N` allow the cave to be opened in some finite number of moves.
  [Source PDF review](../data/extracted/pdf_questions_review.html) · [Source corrections](../data/pdf_source_checks.json)

## Page checks

Each final PDF was rendered to PNG and opened at full page size. Question
numbers, scores, author credits, nonblank pages, and PDF hashes were checked
against the records. All four pages were inspected for clipping, missing
text, broken notation, and misplaced notes or credits. Names and attributions
retain their original spelling. There are no separate source diagrams in
these four sets. The vector marks from Senior Advanced Q4 are part of the
underbrace and are typeset as mathematics.

The manifest identifies two exact statement repeats. Junior Basic Q4 is
also Senior Basic Q1, and Junior Advanced Q7 is also Senior Advanced Q6.
Each keeps its own number, points and attribution from the original set.

| Page | What was checked | Preview |
| --- | --- | --- |
| Junior Basic 1 | Five questions, 3/4/4/5/5 points, and complete credits | [PNG](junior/basic/2009-10-18-p1.png) |
| Senior Basic 1 | Five questions, 4/4/4/4/5 points, exponents, and the separate note | [PNG](senior/basic/2009-10-18-p1.png) |
| Junior Advanced 1 | Seven questions, both exponents, and distinct Q5 subpart scores | [PNG](junior/advanced/2009-10-25-p1.png) |
| Senior Advanced 1 | Seven questions, the underbrace and label, and the complete final question | [PNG](senior/advanced/2009-10-25-p1.png) |

[Page and PDF hashes](visual_checks.json) · [Output manifest](../output/manifest.json)

## Accounting

| Model | Live calls | Live spend |
| --- | ---: | ---: |
| google/gemini-2.5-flash-lite | 24 | $0.0021382 |
| google/gemini-2.5-flash | 24 | $0.0538347 |
| Total for this run | 48 | $0.0559729 |

The prior translation and bilingual review add two calls and $0.0017948.
The canonical ledger totals 50 calls and $0.0577677, with no unresolved
reservations. [Accounting checkpoint](https://github.com/muhammad2702/tournament-agent/blob/3fdde00532598a14f86c4755ec579173b15514fd/data/model_calls.json)

## Remaining archive work

The inventory records every discovered reference. The offline audit tried
the existing extractors on each cached unit classified ready. Of the 19 HTML
sections, two extracted and 17 need review. Of the 42 PDF pages, 15 extracted
and 27 need review. Inspection also recorded 33 URL/anchor errors. The audit
does not establish complete problem-set coverage for the remaining archive.
[Offline archive audit](archive_audit.json)
