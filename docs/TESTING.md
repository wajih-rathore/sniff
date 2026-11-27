# Acceptance test of the installed app

This is the first end-to-end test of Sniff as a person uses it. Every use case was run against the installed back end
and timed. It tests and measures only; nothing was fixed. The same script will run again after the fixes, so the two
results can be compared.

- **Date:** 27 Nov 2026, 19:32 to 21:13 local time. "Today" for every date check is 27 Nov 2026.
- **Build tested:** the installed copy in `%LOCALAPPDATA%\Sniff`. `sniff-backend.exe` was built on 25 Nov at 20:08
  from the `feat/desktop` back end (the back end code is the same as at `6b45cac`). It does not have the router fix
  for "how much is my gas bill" (`f1287e5`), which is only on `main`. The launcher build is `frontend/dist` of the
  desktop worktree: its script name `index-3hb6qd1z.js` is the one inside the installed `sniff.exe`.
- **How it ran:** a copy of the installed back end on port 8790 with its own data folder
  (`E:\E\FAST\Semester 7\GenAI\.cache\acceptance\home`). Wajih's own copy on 8765 was not touched. The installed back
  end always listens on 8765 (`desktop.py` has no setting for it), so `eval/acceptance.py copy` changes that one number
  inside a copy of the exe; nothing else differs. It used the shared model servers (Qwen3-1.7B on 8080, all-MiniLM on
  8081) through a small proxy that logs every model call, so model time and model calls could be counted.
- **Test folder:** 82 files in `.cache\acceptance\docs`, built by `eval/acceptance.py docs`:
  - the whole `dev` set: 72 files, all 12 types and `other`, as 28 text PDFs, 16 scans and 28 photos or screenshots;
  - 3 real SROIE receipt photos;
  - 7 planted files: two Metro Gas bills where October (5,400 this month) carries September's unpaid 3,000 as arrears,
    so it asks for 8,400; an exact copy of the September bill in `Backup\`; the October bill again as a phone photo
    (a near-duplicate); a second ID card with Zainab Malik's CNIC number but other dates (a conflict); an offer letter
    as DOCX; a certificate as PPTX.
  - The right answer for each check comes from `truth.json` in that folder, written when the folder is built.
- **Power and load:** before each timing the script records `Win32_Battery.BatteryStatus`, the AC line, the charge,
  CPU load, free commit and any build running. No build ran at any point. The laptop was on mains until about 20:25
  (it was briefly on battery at 20:21; the search and launcher timings from then were run again on mains) and on
  battery after that, the charge falling from 91% to 42%. Each timing below says which. Battery timings are slower;
  the start-up with an index, asking, MCP and change-detection timings should be run again on mains before they are
  compared with anything.

How to run it again:

```
cd backend
uv run python ../eval/acceptance.py docs  --docs <folder> --synth <main>\data\synth\out --real <main>\data\real\out
uv run python ../eval/acceptance.py copy  --to <dir>\bin --port 8790
uv run python ../eval/acceptance.py start --exe <dir>\bin\sniff-backend.exe --home <dir>\home --port 8790
uv run python ../eval/acceptance.py run   --url http://127.0.0.1:8790 --docs <folder> --home <dir>\home --frontend <frontend\dist>
uv run python ../eval/acceptance.py stop  --home <dir>\home
uv run python ../eval/acceptance.py selftest     # the checkers on known input
```

`run` needs an empty data folder for its first step (index). The steps run in the order index, search, ui,
reminders, overview, ask, mcp, facts, changes, badinput, because facts and the later steps change the index. Results
go to `eval/results/acceptance.json`; each step replaces its own section. When the back end has `/ask/stream`, the
script also times the first token. If `desktop.py` learns to read its port from the environment, `copy` is no longer
needed.

## Targets

| Target | Result | Pass |
|---|---|---|
| Search under 100 ms (at the API) | p50 38.6 ms, p95 59.0 ms over 135 calls (mains) | PASS |
| A new file findable within 2 s | a new file in a watched folder: p50 27.9 s, p95 29.2 s (battery); the first file of a new folder: 53.5 s (mains) | FAIL |
| A fact answer within 2 s | p50 8.1 s, p95 12.8 s over the 14 answers that took the facts route (battery) | FAIL |
| First word of a written answer within 3 s | p50 59.4 s, p95 79.6 s; there is no streaming, so the first word comes with the whole answer (battery) | FAIL |
| Every Sniff process together under 4.5 GB | peak 3.29 GB while indexing, 2.77 GB while asking, 2.95 GB with the MCP server | PASS |

## 1. Start-up

From process start until `GET /index/status` answers, 5 runs each.

| Expected | Result | Pass | Timing |
|---|---|---|---|
| Answers soon after the process starts, empty data folder | answered in 5 of 5 | PASS | p50 4.14 s, p95 5.18 s (mains) |
| Same, with 82 files indexed and the folder watched | answered in 5 of 5; the watched folder came back | PASS | p50 5.64 s, p95 6.29 s (battery) |

The desktop report measured 16 s from the app's start to a working back end; that also covers the Tauri window and
the model servers.

## 2. F1: indexing and change detection

The folder was added with `POST /watch`, as the launcher's `/watch` command does. Mains at the start (19:33) and at
the end (20:15); not checked in between.

| Expected | Result | Pass | Timing |
|---|---|---|---|
| The first file can be found soon | found only after its type and facts were read | FAIL (target 2 s) | 53.5 s |
| All files can be found | all 82, the last one when the scan ended | FAIL | 2,537 s (42 min) |
| All facts read | all 81 documents typed, 0 errors | PASS | 2,537 s (42 min) |
| Every format gives text | text from all 82 files (table below) | PASS | |

Time per document. The exact copy needs no reading and no model call, so 81 documents. The proxy log splits each
document into reading (with OCR) and model time: a document is read, embedded, classified, then its facts are read.

| Format | Files | Total p50 / p95 | Reading p50 / p95 | Model p50 / p95 |
|---|---|---|---|---|
| Text PDF | 31 | 27.0 / 45.7 s | 0.05 / 0.21 s | 26.9 / 45.6 s |
| Scanned PDF | 16 | 35.8 / 55.0 s | 8.8 / 11.2 s | 27.8 / 37.1 s |
| Photo or screenshot | 29 | 27.9 / 48.1 s | 3.8 / 7.6 s | 24.2 / 40.1 s |
| Real receipt photo (SROIE) | 3 | 32.1 / 35.0 s | 5.8 / 6.0 s | 26.3 / 29.0 s |
| DOCX | 1 | 20.5 s | 0.04 s | 20.4 s |
| PPTX | 1 | 15.5 s | 0.07 s | 15.4 s |
| All | 81 | 29.5 / 50.2 s | 3.4 / 9.6 s | 24.8 / 45.6 s |

Over the whole run the chat model took 2,242 s of the 2,537 s (88%), embeddings 8 s, everything else 287 s. There
were 158 chat calls and 81 embedding calls: two chat calls per document (type, then facts), plus retries.

Text by format. Word recall is the share of the words drawn on the page that appear in the indexed text.

| Format | Files with text | Word recall mean / min |
|---|---|---|
| Text PDF, DOCX, PPTX | 33 of 33 | 1.00 / 1.00 |
| Scanned PDF (OCR) | 16 of 16 | 0.70 / 0.39 |
| Photo or screenshot (OCR) | 29 of 29 | 0.73 / 0.55 |
| Real receipt photo (OCR) | 3 of 3 | no reference text |

Part of the missing recall on scans is OCR joining words ("BILALAHMED"), which this count treats as a miss.

Change detection: 5 runs on a small one-page receipt PDF in a new `Changes\` folder inside the watched folder, timed
from the disk change until the API shows it (battery, 20:57).

| Change | Expected | Result | Pass | p50 / p95 |
|---|---|---|---|---|
| New file | findable | 5 of 5; 2 chat calls and 1 embedding call each | PASS (FAIL on the 2 s target) | 27.9 / 29.2 s |
| Edit (a line added) | read again | 5 of 5, the new word found | PASS | 22.7 / 26.7 s |
| Rename | same document, no model calls | 5 of 5, 0 model calls | PASS | 3.4 / 3.6 s |
| Copy into another folder | marked as a copy | 5 of 5, the copy's path listed on the same document, 0 model calls | PASS | 3.5 / 4.6 s |
| Delete, while the copy remains | the document stays, found through the copy | 5 of 5 | PASS | 3.1 / 3.7 s |
| Delete the last copy | removed | 5 of 5, gone from the list and from search | PASS | 3.3 / 4.3 s |

Rename, copy and delete take about 3 s because the watcher waits 2 s of quiet before it scans.

## 3. F2: search

| Expected | Result | Pass | Timing |
|---|---|---|---|
| Under 100 ms at the API | 27 queries, 5 rounds, 135 of 135 under 100 ms | PASS | p50 38.6 ms, p95 59.0 ms, max 64.1 ms (mains) |
| The right file in the top 5 | 25 of 27 | 2 misses | |
| Keystroke to results in the real launcher | 25 of 25 shown | PASS | p50 193.5 ms, p95 215.3 ms (mains) |

The 27 queries name things in the documents (a provider, a person, a product, a course, the planted DOCX and PPTX,
the merchants of the 3 real receipts). The 2 misses: "Orchard Bank statement March 2025" (the March statement is a
scan and its OCR text reads "ORCHARDBANKLTD" as one word, so the keyword cannot match; the January and February
statements came first) and "Restaurant Sin Du" (the OCR text of that real receipt starts at the address and has no
shop name at all).

How the launcher was timed: the shipped build was served as static files with its API address pointed at the test
copy (the browser build calls `127.0.0.1:8000`, which the server rewrote to `127.0.0.1:8790`; nothing else changed).
Headless Chromium (Playwright) typed each of 5 queries at 40 ms per key, 5 rounds after one warm-up round. The time
runs from the last keydown to the first frame after the top result's file name is in the results list. It includes
the launcher's 120 ms pause after typing, so search and drawing take about 75 ms of it.

## 4. F3 and F4: types, facts and review

| Expected | Result | Pass |
|---|---|---|
| The right type for every document | 81 of 81 | PASS |
| The right value for every labelled field | 404 of 439 (0.92); 29 wrong, 6 missing; 13 values given for fields the document does not have | no target |
| `verified` values are right | 381 of 388 (0.982) | no target |
| Every `needs_review` has a reason | 63 of 63 | PASS |
| Correcting a value changes the totals | September bill's current charges 3,000 changed to "Rs. 3,100": the total went from 13,800 to 13,900; "abc" was refused with 422 | PASS |
| Approving a value | status `approved`, reason cleared; after setting it back, the total was 13,800 again | PASS |
| Correcting a misread near-duplicate | the photo's billing month (read as 2026-07) set to 2026-10: the total became 8,400, with the photo left out as a duplicate | PASS |

Field accuracy by type: certificate, insurance, bank statement, passport and offer letter 1.00; ID card 0.98;
utility bill and fee voucher 0.89; transcript 0.88; tenancy 0.86; receipt 0.85; warranty card 0.80. Of the 63 values
sent to review, 54% were in fact right, so review asks about many correct values.

The 7 wrong `verified` values: three warranty products read with their model number ("UPS1500VA VX-1500" for
"UPS 1500VA"), a name read as "BILALAHMED ONOI", and three SROIE merchants (an address line, and two names without a
prefix). The product and SROIE cases are partly strict labels.

Only the fourth phrasing of the total reached the facts route ("What is the sum of the current charges on my Metro
Gas bills in 2026?"). The first three went to search or found nothing (section 5). The total itself, 13,800, is wrong:
see the near-duplicate failure below.

## 5. F5: asking

30 questions, 5 per category, asked round-robin so no prompt ran twice in a row. All on battery (20:32 to 20:49).
The shipped back end has no `/ask/stream`, so the first word arrives with the whole answer.

| Category | Expected | Correct | Citations right | Latency p50 / p95 |
|---|---|---|---|---|
| Single fact | the field's value, cited at its file and page | 4 of 5 | 4 of 5 | 37.9 / 68.9 s |
| Total across bills | gas 2026: 8,400 (copy and photo skipped, arrears counted once); electricity 2025: 27,668; Metro Electronics Mart 2025: 231,800.40 | 0 of 5 | 4 of 5 | 56.6 / 67.5 s |
| Expiring window | every date in the window and no other | 2 of 5 | 2 of 5 | 9.3 / 52.1 s |
| "How much is my gas bill" (5 phrasings) | the latest bill: 8,400, due 12 Dec 2026 | 0 of 5 | 0 of 5 | 12.6 / 63.2 s |
| Conflict | Zainab's CNIC: 20 Jan 2027 in one card, 3 Mar 2028 in the other, shown as a conflict | 0 of 5 | 1 of 5 | 8.1 / 31.9 s |
| Written answers | the right content, cited | 5 of 5 | 5 of 5 | 59.4 / 79.6 s |

By route: 14 answers came from the facts route in 4.9 to 21.0 s (p50 8.1 s), almost all of it the one router call.
16 of the 30 went to the search route, including 11 of the 25 fact questions, and took 31.9 to 79.6 s (router call
plus answer call).

What the wrong answers said:
- "How much did I spend on gas in 2026?" went to search, and the model added numbers itself: "PKR 5400.00 + PKR
  9072.00 = PKR 14472.00" (the second number is the late-payment amount). "Add up my Metro Gas bills for 2026" gave
  "a total of PKR 17,400.00" with a December bill that does not exist. The electricity question was also summed by
  the model, wrongly. The LLM doing arithmetic is what the rubric forbids.
- "What was my total gas spending this year?" and "How much do I owe for gas?": the router set the year to 2023, so
  "No matching documents found."
- "How much is my gas bill?" and "How much is the gas bill I need to pay?": the sum of all 9 utility bills, 45,088.61.
  The search-route phrasings quoted the 2025 bill (5,558).
- Conflict questions: the router dropped "Zainab Malik", took the newest ID card of anyone, and answered with Bilal's
  CNIC (2029-05-06). The conflict exists (the overview and Upcoming show it) but was never shown in an answer.
- "What expires in the next 90 days?" and "... next 30 days?": the router limited it to warranty cards, so "No
  matching documents found". The licence expiring on 14 Dec 2026 was missed.
- "What is the due date of my October 2026 gas bill?" went to search and gave September's due date (2026-10-15).

Passed: the licence and passport expiry dates, the rent and the DOCX salary (both through search), "Which document
expires next?", "Which warranties end in 2027?" and all 5 written answers.

## 6. F6: reminders

| Expected | Result | Pass |
|---|---|---|
| `/upcoming`: the licence (14 Dec 2026), the October gas bill (due 12 Dec 2026), Zainab's CNIC (20 Jan 2027) with its conflict | all three, and the CNIC item carries the conflict; but the October bill is listed twice, as the PDF and as the photo | FAIL |
| Each `.ics` file is valid and has alarms | 4 of 4: CRLF lines of at most 75 octets, one VEVENT with UID, DTSTAMP and an all-day DTSTART on the right date, display alarms 7 days and 1 day before | PASS |
| `/reminders/preview` matches what is sent | title, date and description equal the `.ics` in 4 of 4; the description has the file name only | PASS |
| Google Calendar | not connected on this machine; not tested | n/a |
| Windows notifications | not re-tested; the desktop report saw a reminder toast | cited |

## 7. F7: MCP

A real MCP client (the `mcp` Python SDK over stdio) started `python -m sniff.mcp_server` against the test copy's data
folder. The server ran the shipped source (the desktop worktree at `6b45cac`), because the installed app cannot start
the MCP server: `sniff-backend.exe` only runs the API. Battery, 21:09.

| Expected | Result | Pass |
|---|---|---|
| Refused while MCP is off, and logged | refused and logged as refused; but the agent only sees "Error executing tool list_documents", not the reason | PASS (reason hidden) |
| Switched on through `PUT /mcp/settings`: only allowed types and fields | 52 documents of the 7 allowed types; no hidden type, no field outside the list | PASS |
| Page text only for types allowed with "*" | search returned 0 hits (by default no type has "*") | PASS |
| An answer that needs a hidden type is withheld | "Withheld: ..." for Zainab's ID card | PASS |
| An allowed answer is right (gas 2026: 8,400) | "Withheld": the router used search, and search answers need "*" for every type | FAIL |
| ID numbers are masked (ID cards allowed for this check) | 7 of 8 masked, for example KHI-••-••5642; EMP-27986 was shown in full | FAIL |
| Every call is in the access log | 19 of 19, with the 1 refusal | PASS |

Time per call: `list_documents` p50 29.6 ms, `search_documents` 48.1 ms (the first call of each 381 and 563 ms),
`ask` p50 36.8 s (7.2 to 58.1 s). The MCP server process used 302 MB.

## 8. F9: overview

| Number | Truth | Overview | Pass |
|---|---|---|---|
| Files indexed | 82 | 82 | PASS |
| Documents | 81 | 81 | PASS |
| Documents per type (13 types) | all 13 counts | the same | PASS |
| Exact copies | 1 | 1 | PASS |
| Near-duplicates | 1 | 0 | FAIL |
| Expiring in 30 / 60 / 90 days | 1 / 2 / 2 | 1 / 2 / 2 | PASS |
| Documents missing a required field | 0 | 5 | FAIL |
| Conflicts | 2 (issue and expiry date of one CNIC) | 2 | PASS |
| Errors | 0 | 0 | PASS |
| Facts to review | same as the review list | 63 and 63 | PASS |

The 5 "missing" fields are dates that extraction did not find (a scanned tenancy's start and end, two SROIE receipt
dates, a pharmacy receipt date, a transcript's issue date). The overview counts correctly from what was extracted; it
is wrong against the folder.

## 9. Memory

Peak private memory, sampled every 0.5 s, of the test back end, the chat server and the embedding server (and the MCP
server when it ran). Wajih's own back end (about 336 MB) was also running and is not counted.

| Phase | Back end | Chat server | Embedding server | MCP server | Peak of the sum | Pass (4.5 GB) |
|---|---|---|---|---|---|---|
| Indexing (mains) | 992 MB | 2,250 MB | 75 MB | | 3,287 MB | PASS |
| Asking (battery) | 468 MB | 2,229 MB | 75 MB | | 2,773 MB | PASS |
| MCP (battery) | 334 MB | 2,236 MB | 77 MB | 302 MB | 2,948 MB | PASS |

## 10. Bad input

Six files written into a new `Bad\` folder inside the watched folder; `locked.pdf` was held open with no sharing
(as a program that has not finished writing it would). Battery, 21:06.

| File | Expected | Result | Pass |
|---|---|---|---|
| `corrupt.pdf` (a PDF header, then random bytes) | error recorded | `FileDataError: Failed to open file ... as type pdf.` | PASS |
| `empty.pdf` (0 bytes) | error recorded | `EmptyFileError: Cannot open empty file` | PASS |
| `protected.pdf` (AES-256 password) | error recorded | not reached while the locked file blocked the scan; afterwards `ValueError: document closed or encrypted` | PASS, after the lock |
| `large.pdf` (50 pages of text) | indexed | indexed: about 26 s of embedding calls (7 batches of 32 chunks) and 2 s for its type | PASS |
| `budget.xlsx` (not a supported type) | ignored, no crash | ignored | PASS |
| `locked.pdf` (held open by another process) | error recorded, the other files go on | the scan stopped at it with `PermissionError ... Permission denied` (shown as the watcher's error); no row for the file; the files after it in that scan were not indexed until another change in the folder | FAIL |

The back end stayed up throughout. After the lock was gone, the overview counted 3 errors, but the API never returns
the error text (a document has no error field), so the launcher cannot say what went wrong. In one run the skipped
files were picked up 28 s after the lock was released (another change was pending); in the other only after the
locked file was touched. Files with an error are read again on every scan.

## 11. F8: install and uninstall

Not re-tested here. From the desktop report (`sniff-agents/reports/desktop.md`): the installer is 166 MB and installs
silently in 73 s; a real window started the back end in 16 s, indexed, searched, answered, opened a PDF at its page,
and the hotkey, tray and Quit worked; a silent uninstall with `/DELETEAPPDATA` left nothing. The clean-machine test in
Windows Sandbox is still to do (Sandbox is not enabled on this laptop).

## What failed, and the likely cause

1. **New files are not findable for about 30 s (target 2 s).** `scan.index_file` reads, embeds, classifies and
   extracts, then commits once, so a file appears only after two chat calls. The first file of a new folder took
   53.5 s. Likely fix: commit the text and vectors first and read types and facts afterwards (the `feat/speed`
   branch says it does this; not tested here).
2. **Asking is slow: fact answers 5 to 21 s, written answers 32 to 80 s (targets 2 s and 3 s).** The router alone is
   one Qwen3-1.7B call on CPU; written answers need a second call; nothing streams. Measured on battery, which slows
   the CPU, so the mains numbers will be lower but not near the targets.
3. **The router picks the wrong route or invents filters.** 11 of 25 fact questions went to search; others got a
   year of 2023, lost the person's name, or were limited to one document type. This build lacks the router repair on
   `main` (`f1287e5`). Causes are the small model and a long prompt with every field of every type.
4. **On the search route the model does arithmetic.** It added amounts and invented a December bill. Cause: the
   search answer prompt does not forbid sums, and totals reach it because of failure 3.
5. **"How much is my gas bill?" fails in all 5 phrasings.** The facts route summed every utility bill (the provider
   filter was dropped) or the search route quoted an old bill. The expected behaviour (the latest bill) is in
   `feat/speed`, not in this build.
6. **Conflicts never reach an answer.** The conflict is detected (overview, Upcoming), but the router dropped "Zainab
   Malik", so the answer used another person's card and no conflict was attached.
7. **A near-duplicate with one misread field is counted twice.** OCR read the photo's month as July, so its key
   (provider, account, month) differed: totals gave 13,800 instead of 8,400, Upcoming lists the bill twice, and the
   overview shows 0 near-duplicates. Correcting the month in review fixes all three.
8. **A file held open by another program stops the whole scan.** `scan._scan` hashes the file outside its
   try/except, so the `PermissionError` ends the scan; the file gets no error row and later files wait for the next
   change event.
9. **Errors are recorded but cannot be seen.** The overview counts them; no API returns the message.
10. **MCP:** the refusal reason does not reach the agent (the SDK replaces the exception text with "Error executing
    tool"); `mask_ids` does not mask IDs with fewer than 6 digits (EMP-27986); any answer from the search route is withheld
    under the default allow-list, so router mistakes (failure 3) turn into withheld answers.
11. **Extraction gaps:** 5 documents miss a required date (the overview's "missing" count), and 54% of the values
    sent to review were right. Not a crash, but more review work than needed.
12. **Search top 5 misses 2 of 27, both from OCR:** words joined into one ("ORCHARDBANKLTD"), and a receipt header
    that OCR did not read.
13. **Testing and running the shipped app:** the back end's port is fixed at 8765, so a second copy could only run
    after a binary patch; and the installed app has no way to start the MCP server without the repo and uv.
14. **Of two identical files, the one scanned first becomes the document,** here `Backup\Metro Gas Sep 2026.pdf`
    rather than `Bills\...`. Search results and citations then point at the backup.
