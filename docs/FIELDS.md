# Document fields and rules

This answers feedback points 3 and 4. The fields are defined in `backend/sniff/schemas.py` (`FIELDS`), and the rules in `backend/sniff/verify.py`. This file is the readable copy; if they differ, the code is right and this file is out of date.

The model only copies each value as it is printed ("21.03.2003", "Rs. 4,320/-"). Code parses it, checks it and decides its status. No model does a date comparison or a sum.

## Fact status

| Status | Meaning |
|---|---|
| `verified` | All three checks pass: the value is on the page, one of its field's labels is next to it, and its own rules and the cross-field rules pass. `facts.verified` is 1 only in this case. |
| `needs_review` | At least one check failed. `facts.reason` says which, e.g. "found next to 'Arrears', not a 'Current Charges' label" or "due date before bill date". |
| `corrected` | The user changed the value (`PATCH /facts/{id}`). The new value must pass the field's own rules. A broken cross-field rule is kept as the reason. |
| `approved` | The user confirmed the value as it is (`POST /facts/{id}/approve`). |

Calculations, Upcoming and reminders use the value in the facts table, so corrected and approved values are the ones used.

## How "next to its label" is checked

1. The page layout comes from PyMuPDF word boxes (text PDFs) or OCR boxes (scans and photos). OCR boxes are turned back by the median tilt of the text first, so a label and its value on a tilted scan stay on one row. Word and text files have no layout, so their lines are used in order.
2. Boxes are grouped into rows. The value is searched in each row, ignoring spaces and case. Dates and months are also searched in common written forms, in case the model tidied them. Amounts are matched as numbers, so "4,320" and "4320.00" both match 4320.
3. For each place the value appears, the nearest label is found among the labels of every field of this document type, plus the labels that belong to no field (listed under each table). Nearest means: a label before the value on the same row first; then a label after it on the same row, or one on another row within 1.6 line heights up or down and 8 line heights sideways (rows below count as a little further away). When two labels are equally near, the longer one wins ("Sub Total" over "Total").
4. The value passes if its own label is the nearest. If another field's label is nearer, it fails with "found next to 'X'". Organisation names (`provider`, `merchant`, `institution`) also pass when they are in the first 3 rows of the page, the letterhead.
5. Short labels (under 6 letters) must be whole words, so "Subtotal" does not contain "Total". OCR often drops spaces, so a change from lower to upper case also counts as a word break ("IssuedbyBrightpath"), and longer labels may run into the next word ("Warrantyvaliduntil").

## Rules for each kind of value

| Kind | Rule |
|---|---|
| date | Parses as a day-first date (e.g. 21.03.2003, 21/03/2003, 21 Mar 2003, "10th day of July, 2025"); year 1900 to 2100 |
| month | Parses to `YYYY-MM` (e.g. "March 2026", "Mar-2026", "03/2026") |
| money | Not negative; at most 2 decimals; required totals must be above 0 |
| id | A 13-digit number must be in CNIC form `#####-#######-#`; otherwise 4 to 25 letters, digits, dashes or slashes with at least 3 digits |
| status | `paid` or `unpaid` |
| text | 2 to 120 characters with at least one letter |

## Cross-field rules

Money rules allow a difference of up to 1 for rounding. A rule is skipped when one of its values is missing. When a rule fails, every fact in it gets `needs_review` with the rule as the reason.

| Type | Rule | Reason shown |
|---|---|---|
| utility_bill | `due_date` on or after `bill_date` | due date before bill date |
| utility_bill | `bill_date` not before `billing_month` | bill date before the billing month |
| utility_bill | `payment_date` on or after `bill_date` | payment date before bill date |
| utility_bill | `amount_due` = `current_charges` + `previous_balance`, or that plus `tax` when the bill lists tax outside the current charges | amount due is not current charges + previous balance (+ tax) |
| id_card | `expiry_date` after `issue_date` | expiry date not after issue date |
| id_card | `date_of_birth` before `issue_date` | date of birth not before issue date |
| receipt | `total_amount` = `subtotal` + `tax` (tax 0 if not printed) | total is not subtotal + tax |
| receipt | `warranty_until` after `date` | warranty ends before the purchase date |

"Required" fields feed the overview's "missing required fields" list. "Stable" fields do not change for the same card or record, so two different values are a conflict (see `CALCULATIONS.md`). The near-duplicate key under each table is also explained there.

## id_card

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `name` | text | yes | yes | the card holder's full name | Name, Full Name, Holder, Name of Holder |
| `id_number` | id | yes | yes | the card number (CNIC #####-#######-#, licence, roll or employee number) | Identity Number, ID No, ID Number, CNIC, Card No, Licence No, License No, Roll No, Employee No, Registration No |
| `date_of_birth` | date |  | yes | the holder's date of birth | Date of Birth, DOB, Born, Birth |
| `issue_date` | date |  | yes | the date the card was issued | Date of Issue, Issue Date, Issued, Issued on, Valid From |
| `expiry_date` | date | yes | yes | the date the card stops being valid | Date of Expiry, Expiry, Expiry Date, Expires, Valid Until, Valid Thru, Valid Through |

Near-duplicate key: `id_number`. Labels of no field: Signature.

## utility_bill

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `provider` | text | yes | yes | the company that sent the bill | Electric, Electricity, Gas, Water, Internet, Telecom, Power, Supply, Ltd, Limited, Co, Company, Bill, Statement, Invoice (or the first 3 rows) |
| `account_number` | id | yes | yes | the customer's account or consumer number | Account No, Account Number, Account, Acct, Consumer No, Customer ID |
| `billing_month` | month | yes | yes | the month the bill is for | Billing Month, Bill Month, Billing Period, Statement Period, Period, Month |
| `bill_date` | date |  |  | the date the bill was issued | Issue Date, Bill Date, Statement Date, Invoice Date, Date of Issue, Issued on |
| `due_date` | date | yes |  | the last date to pay without a late fee | Due Date, Last date of payment, Pay by, Payment Due, Last Date |
| `current_charges` | money | yes |  | this month's charges, including taxes, without old unpaid amounts | Current Charges, Current Bill, Charges for the month, Current Month Charges, Current Amount |
| `previous_balance` | money |  |  | unpaid amount carried over from earlier bills (arrears) | Previous Balance, Arrears, Arrear, Balance brought forward, Previous Dues, Unpaid Balance, Outstanding |
| `tax` | money |  |  | the tax on this bill | Tax, Taxes, GST, Sales Tax, VAT, Duties |
| `amount_due` | money | yes |  | the total payable: current charges plus previous balance | Amount Due, Amount Payable, Total Payable, Total Due, Net Amount Due, Total Amount Due, Net Payable, Payable within due date |
| `payment_status` | status |  |  | paid or unpaid, only if the bill says so | Paid, Unpaid, Payment Status, Status, Payment received |
| `payment_date` | date |  |  | the date the bill was paid, if printed | Paid on, Payment Date, Date of Payment, Payment received on, Received on |

Near-duplicate key: `provider` + `account_number` + `billing_month`. Labels of no field: Reference, Amount after due date, Late payment surcharge, Surcharge, Charges, Rent, Adjustment, Consumption, Customer Care, Units, Add-ons.

## certificate

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `holder_name` | text | yes | yes | the person the certificate is for | This is to certify that, certify that, Presented to, confers upon, Awarded to, granted to, Name |
| `title` | text | yes | yes | the degree, course or award | has successfully completed, completed the course, the degree of, the certificate in, for successfully completing, for being awarded, requirements for, examination for, in recognition of |
| `institution` | text | yes | yes | the organisation that issued it | University, College, Institute, Academy, School, Center, Centre, Board, Learning, Issued by (or the first 3 rows) |
| `issue_date` | date | yes | yes | the date it was issued | Issue Date, Date of Issue, Issued on, Awarded on, Given this, Date |

Near-duplicate key: `holder_name` + `title` + `issue_date`. Labels of no field: Certificate No, Serial No, Certificate ID, Registrar, Director.

A label word inside the value itself counts for its own field ("University" in "Lakeshore University") but not for another field ("School" in "Secondary School Certificate" is not an institution label).

## receipt

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `merchant` | text | yes |  | the shop or website | Receipt, Invoice, Cash Memo, Order Confirmation, Tel, Mart, Store, Pharmacy, Station, Restaurant, Grocers, Appliances (or the first 3 rows) |
| `date` | date | yes |  | the purchase date | Date, Placed on, Order Date, Purchase Date, Invoice Date, Dated |
| `subtotal` | money |  |  | the amount before tax, if printed | Subtotal, Sub Total, Sub-total |
| `tax` | money |  |  | the tax, if printed | Tax, GST, Sales Tax, Service Tax, VAT |
| `total_amount` | money | yes |  | the total paid | Total, Grand Total, Amount Paid, Net Payable, Amount Due, Net Total, Order Total, Total Paid, Total Amount |
| `warranty_until` | date |  |  | the date the warranty ends, if any | Warranty, Guarantee, Valid until, Warranty till, expires |

Near-duplicate key: `merchant` + `date` + `total_amount`. Labels of no field: Cash tendered, Change, Receipt #, Receipt No, Order No, Unit Price, Qty, Time.

## other

No fields. These documents are indexed for search only.

## Known limits

- The label lists come from the bills, cards and receipts we generate and wording common on local bills (Arrears, CNIC, Last date of payment). A real document that uses a label not in the list gets `needs_review`, not a wrong `verified`.
- OCR misreads labels too ("Bom" for "Born"); the value then needs review even when it is right.
- Only the 4 A3 types have fields. The other 8 types from the proposal are planned for A4.
