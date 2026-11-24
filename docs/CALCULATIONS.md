# Calculations, duplicates and conflicts

This answers feedback points 5, 6 and 9. The code is in `backend/sniff/ask.py` (calculations), `backend/sniff/records.py` (duplicates and conflicts) and `backend/sniff/verify.py` (`spend`). Field names are in `FIELDS.md`.

The model never calculates. For a facts question it fills a fixed template: document type, field, operation (`list`, `sum`, `count`, `min`, `max`, `next_expiring`, `outstanding`, `latest`) and filters (person, provider, year, month, a date range, or a relative period). It copies dates as written ("March 2026"); code turns them into date ranges. Code also repairs two common router slips: a person filter on bills or receipts (which name no person) is matched against the company, and an expiry asked of a type that has none means every type. Company names match by the first 6 letters of a long word, so "electricity" finds "Northern Electric Supply Co.". Code then filters, removes duplicates, computes and writes the numbers into the answer.

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

## The date each document is about

A date filter ("in 2026", "in March 2026", "from July to September 2026") is applied to the field asked about when that field is a date. Otherwise it is applied to the date the document is about:

| Type | Date used for filters and for "newest" |
|---|---|
| utility_bill | `billing_month` |
| receipt | `date` |
| certificate, transcript | `issue_date` |
| id_card, passport | `expiry_date` |
| bank_statement | `period_end` |
| warranty_card | `purchase_date` |
| offer_letter | `offer_date` |
| insurance_policy, tenancy_agreement | `start_date` |
| fee_voucher | `due_date` |

So "How much were Sara's university fees in 2023?" adds the vouchers due in 2023, and "premiums for policies starting in 2026" adds the policies whose cover starts in 2026.

**Newest document** (op `latest`): among the matching documents, the one with the latest date from this table. "What is Hamza's current CGPA?" reads `cgpa` on his newest transcript; "What is the number of Ali's latest passport?" reads `passport_number` on the passport that expires last. If two documents share that date (a conflict, such as a draft and a signed agreement), both values are shown.

## Money on a bank statement

| Field | What it is | Used for |
|---|---|---|
| `opening_balance` | the balance on the first day of the period | only checked by the statement rule |
| `total_credits` | all money paid in during the period | income ("How much was paid into the Crescent Bank account in 2026?") |
| `total_debits` | all money paid out during the period | spending from the account |
| `closing_balance` | the balance on the last day | the account balance |

The statement rule is `opening_balance + total_credits - total_debits = closing_balance`, within 1.

**Balance** (`closing_balance` with op `sum` or `latest`): for each bank and account, take the statement with the latest `period_end` and use its closing balance; then add these across accounts. Adding the closing balances of several statements of one account would count the same money again, because each balance already holds the money of the months before. With a date filter, the newest statement inside the period is used, so "balance at the end of March 2026" works the same way.

**Money in and out** over a period is the sum of `total_credits` or `total_debits` over unique statements whose `period_end` is in the period.

## Other A4 money fields

`monthly_salary` (offer letter), `premium` (insurance), `monthly_rent` and `security_deposit` (tenancy) and `amount` (fee voucher) are plain amounts. Sum, min, max and list work on them as they do on receipts, using the date table above. `cgpa` is a number between 0 and 4; min, max and list work on it. Code does not multiply rent by months or split a premium across years: a premium counts in the year its cover starts.

## Duplicates

**Exact copies** (the same bytes, found by sha256, in another folder) are one document. The first file indexed holds the text, vectors and facts; each copy is a row in `files` with `copy_of` pointing to it and no rows of its own. A copy is not read or sent to the model again. Search, Ask and the library show the document once; `GET /documents/{id}` lists all its paths. If the file holding the rows is deleted or changed, the first copy takes over the rows, including any corrections, so the document stays. Renaming either file keeps everything.

**Near-duplicates** are different files with the same record, like a bill saved as a PDF and also as a phone photo. Two documents are the same record when their key matches, after lower-casing and removing spaces and punctuation (amounts compared to 2 decimals):

| Type | Key |
|---|---|
| utility_bill | provider + account_number + billing_month |
| receipt | merchant + date + total_amount |
| id_card | id_number |
| certificate | holder_name + title + issue_date |
| passport | passport_number |
| transcript | registration_number + issue_date |
| bank_statement | bank + account_number + period_end |
| warranty_card | brand + serial_number |
| offer_letter | employer + candidate_name + position |
| insurance_policy | insurer + policy_number + start_date |
| tenancy_agreement | landlord + tenant + start_date |
| fee_voucher | registration_number + semester |

Within a key, documents that agree on every stable field are near-duplicates. Calculations count one of them: the one with the fewest facts needing review, then the most facts a person corrected or approved, then the one indexed first. The others are listed in the answer's `skipped_duplicates` and the answer text says how many were left out. A document with a key field missing is never merged.

## Facts that still need review

Decision: totals and lists include facts with status `needs_review`. Leaving them out would give a wrong total without saying so. Instead the answer lists them in `unreviewed` and the text says "N of the values used need review", so the user knows what to check. Reminders are created only when the user clicks "Add to Google Calendar". Corrected and approved values are the ones stored, so every later calculation and reminder uses them.

## Conflicts

Within a key, documents that disagree on a stable field are a conflict, for example one CNIC on two cards with different expiry dates. Neither value is picked:

- both documents stay in counts and lists;
- `GET /conflicts` returns the key, the field, and each value with its document, page and status;
- an answer that uses the field fills `conflicts` and adds "Conflict: the expiry date for id_number ... is A in x.jpg but B in y.pdf. Check which is right.";
- `/upcoming` attaches the conflict to the date it affects.

Stable fields: every field of `id_card`, `certificate`, `passport`, `transcript`, `warranty_card`, `offer_letter`, `insurance_policy`, `tenancy_agreement` and `fee_voucher`. For bills and receipts every stable field is part of the key, so they never conflict (same key means near-duplicate). A bank statement's amounts are not stable, so two statements of the same account and period end can only conflict on `period_start`. Examples in the test data: a revised offer letter with a new joining date, a draft and a signed tenancy agreement with different end dates, a re-issued fee voucher with a new due date, and a travel policy extended to a later end date.

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
- Both sides of a conflict stay in sums. A re-issued fee voucher is therefore counted twice in a fee total until the user corrects or removes one; the answer shows the conflict.
- A bank balance below zero (an overdraft) fails the money rule and goes to review.
