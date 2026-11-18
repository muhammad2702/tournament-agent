# Four checked 2009 examples

The four PDFs contain 24 questions and the Senior Basic section note. The English was produced by real OpenRouter translation and bilingual review calls. Source corrections were checked against original HTML or PDF pages. The reviewed English and original file paths are retained in the single SQLite table at `data/archive.sqlite`.

## Source checks

- Junior Basic Q1 keeps nine squares, the 1/3/5 colour counts, and the equal versus unequal size conditions.
- Senior Basic Q3 keeps four cubes and `100^{100}`. Its section note occurs once after Q5.
- Senior Basic Q4 says one point on each side.
- Junior Advanced Q2 keeps the multiplication signs. Q5 keeps both implications and distinct 2 and 7 point scores.
- Senior Advanced Q4 labels the underbrace “n ones.” Q7 allows any finite number of moves.
- The advanced PDF source pages are saved as original files. Question crops remain available for comparison with notation and figures.

## Output pages

Each saved PDF was rendered to a page image and inspected for missing text, clipping, scores, question numbers, mathematical marks, attribution, and note placement. All four are one page. The final PDFs are at:

- [Junior Basic](../output/junior/basic/2009-10-18.pdf)
- [Senior Basic](../output/senior/basic/2009-10-18.pdf)
- [Junior Advanced](../output/junior/advanced/2009-10-25.pdf)
- [Senior Advanced](../output/senior/advanced/2009-10-25.pdf)

The archive has 255 HTML links and 99 PDF links to 73 distinct PDF URLs. Some sections have unclear anchors, scanned pages, unusual layouts, or figures requiring source review. Those rows remain marked `needs_review` in SQLite. Running the program does not label those sets complete.
