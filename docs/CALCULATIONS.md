# Calculations, duplicates and conflicts

This answers feedback points 5, 6 and 9. The code is in `backend/sniff/ask.py` (calculations), `backend/sniff/records.py` (duplicates and conflicts) and `backend/sniff/verify.py` (`spend`). Field names are in `FIELDS.md`.

The model never calculates. For a facts question it fills a fixed template: document type, field, operation (`list`, `sum`, `count`, `min`, `max`, `next_expiring`, `outstanding`) and filters (person, provider, year, month, a date range, or a relative period). It copies dates as written ("March 2026"); code turns them into date ranges. Code also repairs two common router slips: a person filter on bills or receipts (which name no person) is matched against the company, and an expiry asked of a type that has none means every type. Company names match by the first 6 letters of a long word, so "electricity" finds "Northern Electric Supply Co.". Code then filters, removes duplicates, computes and writes the numbers into the answer.

## Money on a bill

| Field | What it is | Used for |
|---|---|---|
| `current_charges` | this month's charges, including taxes | spending ("How much did we pay for electricity in 2026?") |
| `previous_balance` | arrears: unpaid amount from earlier bills | only checked by the bill rule; never summed |
| `tax` | the tax on this bill | tax totals |
| `amount_due` | total payable = current charges + previous balance | the bill's own amount; outstanding balance |
| `payment_status`, `payment_date` | only when printed (e.g. a PAID stamp) | outstanding balance |

**Spending** over a period is the sum, over unique bills whose `billing_month` is in the period, of `current_charges`. If the router asks to sum `amount_due` over more than one bill of the same account, code sums `current_charges` instead and says so in the answer. The reason: when a bill is not paid, the next bill repeats it inside `amount_due`, so summing `amount_due` counts the same money twice. With one bill per account nothing can repeat, so `amount_due` is summed as asked. If a bill prints tax outside its current charges (`amount_due` = current + previous + tax), that bill's tax is added to its spending.

**Tax** is the sum of `tax` over unique bills in the period.

**Outstanding balance** (op `outstanding`): for each provider and account, take the latest bill by `billing_month`. If it is not marked paid, its `amount_due` is owed. Nothing else is added, because that `amount_due` already includes the older unpaid bills. If every latest bill is marked paid, the answer is 0.

Receipts are simpler: `total_amount` is the money spent, `subtotal` and `tax` are only checked by the receipt rule.

## Duplicates

**Exact copies** (the same bytes, found by sha256, in another folder) are one document. The first file indexed holds the text, vectors and facts; each copy is a row in `files` with `copy_of` pointing to it and no rows of its own. A copy is not read or sent to the model again. Search, Ask and the library show the document once; `GET /documents/{id}` lists all its paths. If the file holding the rows is deleted or changed, the first copy takes over the rows, including any corrections, so the document stays. Renaming either file keeps everything.

**Near-duplicates** are different files with the same record, like a bill saved as a PDF and also as a phone photo. Two documents are the same record when their key matches, after lower-casing and removing spaces and punctuation (amounts compared to 2 decimals):

| Type | Key |
|---|---|
| utility_bill | provider + account_number + billing_month |
| receipt | merchant + date + total_amount |
| id_card | id_number |
| certificate | holder_name + title + issue_date |

Within a key, documents that agree on every stable field are near-duplicates. Calculations count one of them: the one with the fewest facts needing review, then the most facts a person corrected or approved, then the one indexed first. The others are listed in the answer's `skipped_duplicates` and the answer text says how many were left out. A document with a key field missing is never merged.

## Facts that still need review

Decision: totals and lists include facts with status `needs_review`. Leaving them out would give a wrong total without saying so. Instead the answer lists them in `unreviewed` and the text says "N of the values used need review", so the user knows what to check. Reminders are created only when the user clicks "Add to Google Calendar". Corrected and approved values are the ones stored, so every later calculation and reminder uses them.

## Conflicts

Within a key, documents that disagree on a stable field are a conflict, for example one CNIC on two cards with different expiry dates. Neither value is picked:

- both documents stay in counts and lists;
- `GET /conflicts` returns the key, the field, and each value with its document, page and status;
- an answer that uses the field fills `conflicts` and adds "Conflict: the expiry date for id_number ... is A in x.jpg but B in y.pdf. Check which is right.";
- `/upcoming` attaches the conflict to the date it affects.

Stable fields: all `id_card` fields, all `certificate` fields; for bills and receipts every stable field is part of the key, so they never conflict (same key means near-duplicate).

## What each answer carries

| Field | Contents |
|---|---|
| `counted` | the documents used in the calculation |
| `skipped_duplicates` | near-duplicates left out |
| `unreviewed` | facts used that still need review |
| `conflicts` | conflicts on the field asked about |
| `facts`, `sources`, `query` | the facts used, their pages, and the template the router filled |

## Known limits

- Keys compare text exactly after normalising. An OCR error in a key field (a misread account number) keeps two copies apart, and both are counted.
- A changed or renewed document with the same key and the same stable values is treated as a duplicate. For bills this means a corrected bill for the same month is folded into the first one; the more trusted copy wins.
- Spending assumes `current_charges` includes tax, as on the bills we generate. The "tax outside current charges" case is handled by the rule above but is not in the test data yet.
