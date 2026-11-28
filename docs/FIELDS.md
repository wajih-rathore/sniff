# Document fields and rules

It covers all 12 document types. The fields are defined in `backend/sniff/schemas.py` (`FIELDS`), and the rules in `backend/sniff/verify.py`. This file is the readable copy; if they differ, the code is right and this file is out of date.

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
3. For each place the value appears, the nearest label is found among the labels of every field of this document type, plus the labels that belong to no field (listed under each table). Nearest means: a label before the value on the same row first; then a label after it on the same row, or one on another row within 1.6 line heights up or down and 8 line heights sideways (rows below count as a little further away, and for a label below the value the smaller of the two line heights is used, so a big letterhead does not reach the small labels under it). When two labels are equally near, the longer one wins ("Sub Total" over "Total").
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
| passport | 6 to 9 capital letters and digits, at least 4 digits (a Pakistani number is 2 letters + 7 digits); spaces are removed |
| serial | 4 to 30 letters, digits or dashes, with at least one digit |
| gpa | a number above 0 and at most 4, with at most 2 decimals (the first number printed, so "3.52 / 4.00" reads as 3.52) |
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
| passport | `expiry_date` after `issue_date` | expiry date not after issue date |
| passport | `date_of_birth` before `issue_date` | date of birth not before issue date |
| passport | `expiry_date` is 5 or 10 years after `issue_date`, or one day less | passport is not valid for 5 or 10 years |
| transcript | `issue_date` not after today | issue date is in the future |
| bank_statement | `period_start` before `period_end` | period start not before period end |
| bank_statement | `opening_balance` + `total_credits` - `total_debits` = `closing_balance` | opening balance + credits - debits is not the closing balance |
| warranty_card | `warranty_until` after `purchase_date` | warranty ends before the purchase date |
| warranty_card | `warranty_until` at most 10 years after `purchase_date` | warranty longer than 10 years |
| offer_letter | `offer_date` on or before `start_date` | start date before the offer date |
| insurance_policy, tenancy_agreement | `end_date` after `start_date` | end date not after start date |
| fee_voucher | `due_date` on or after `issue_date` | due date before issue date |

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

## passport

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `name` | text | yes | yes | the passport holder's full name | Name, Full Name, Holder's Name |
| `passport_number` | passport | yes | yes | the passport number | Passport No, Passport Number, Passport # |
| `nationality` | text | yes | yes | the holder's nationality | Nationality |
| `date_of_birth` | date |  | yes | the holder's date of birth | Date of Birth, DOB, Born |
| `issue_date` | date |  | yes | the date the passport was issued | Date of Issue, Issue Date, Issued on |
| `expiry_date` | date | yes | yes | the date the passport stops being valid | Date of Expiry, Expiry Date, Expiry, Valid Until |

Near-duplicate key: `passport_number`. Labels of no field: Place of Birth, Place of Issue, Issuing Authority, Citizenship No, Tracking No, Booklet No, Sex, Type, Country Code, Signature.

One layout prints the name in capitals, as passports do. The machine-readable lines at the bottom repeat the number; it is checked next to its printed label, not in those lines.

## transcript

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `student_name` | text | yes | yes | the student's full name | Student Name, Name of Student, Name |
| `registration_number` | id | yes | yes | the student's registration or roll number | Registration No, Registration Number, Reg No, Roll No, Student ID |
| `institution` | text | yes | yes | the university or college that issued it | University, College, Institute, School, Academy (or the first 3 rows) |
| `program` | text | yes | yes | the degree programme | Program, Programme, Degree Program, Degree |
| `cgpa` | gpa | yes | yes | the cumulative GPA (CGPA) on the transcript, not a semester GPA | CGPA, Cumulative GPA, Cumulative Grade Point Average |
| `issue_date` | date | yes | yes | the date the transcript was issued | Date of Issue, Issue Date, Issued on, Date |

Near-duplicate key: `registration_number` + `issue_date`. Labels of no field: SGPA, GPA, Semester GPA, Credit Hours, Credits, Grade, Transcript No, Date of Birth, Father's Name, Session.

Each semester also prints a semester GPA. Those values sit next to "Semester GPA" or "SGPA", which belong to no field, so a semester GPA copied as the CGPA needs review.

## bank_statement

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `bank` | text | yes | yes | the bank that issued the statement | Bank, Bank Limited, Bank Ltd (or the first 3 rows) |
| `account_number` | id | yes | yes | the account number (not the IBAN) | Account Number, Account No, A/C No, Account # |
| `period_start` | date | yes | yes | the first day the statement covers | Statement Period, Period From, From |
| `period_end` | date | yes | yes | the last day the statement covers | Period To, To |
| `opening_balance` | money |  |  | the balance at the start of the period | Opening Balance, Balance brought forward, Balance B/F |
| `total_credits` | money |  |  | all money paid into the account in the period | Total Credits, Total Deposits, Credits |
| `total_debits` | money |  |  | all money paid out of the account in the period | Total Debits, Total Withdrawals, Debits |
| `closing_balance` | money | yes |  | the balance at the end of the period | Closing Balance, Balance C/F, Ending Balance |

Near-duplicate key: `bank` + `account_number` + `period_end`. Labels of no field: Statement Date, IBAN, Branch, Branch Code, Cheque No, Customer ID.

The account number is stored in full, like a bill's account number. Showing it masked is planned (MCP already masks long numbers). The IBAN is not a field.

## warranty_card

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `product` | text | yes | yes | the product the warranty covers | Product, Product Name, Item, Appliance |
| `brand` | text | yes | yes | the brand or maker | Brand, Manufacturer (or the first 3 rows) |
| `serial_number` | serial | yes | yes | the product's serial number | Serial No, Serial Number, S/N |
| `purchase_date` | date | yes | yes | the date it was bought | Date of Purchase, Purchase Date, Purchased on |
| `warranty_until` | date | yes | yes | the date the warranty ends | Warranty valid until, Valid until, Warranty till, Warranty expires, Valid Till |

Near-duplicate key: `brand` + `serial_number`. Labels of no field: Model No, Model, Dealer, Warranty Period, Customer Name, Card No, Phone, Invoice No.

Brand is the maker, not the shop; the shop is printed next to "Dealer", which belongs to no field.

## offer_letter

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `employer` | text | yes | yes | the company making the offer | Pvt Ltd, Ltd, Limited, Company, on behalf of (or the first 3 rows) |
| `candidate_name` | text | yes | yes | the person offered the job | Dear, Candidate, Name, To |
| `position` | text | yes | yes | the job title offered | Position, Designation, Job Title |
| `monthly_salary` | money | yes | yes | the gross salary per month | Monthly Gross Salary, Gross Monthly Salary, Monthly Salary, Gross Salary, Salary |
| `offer_date` | date | yes | yes | the date of the letter | Date, Dated |
| `start_date` | date | yes | yes | the date the job starts | Joining Date, Date of Joining, Start Date, join us on |

Near-duplicate key: `employer` + `candidate_name` + `position`. Labels of no field: Ref, Reference, Basic Salary, Allowance, House Rent Allowance, Medical Allowance, Probation, Reply by, Department, Working Hours.

The gross salary is the field. Basic salary and allowances are printed next to labels that belong to no field.

## insurance_policy

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `insurer` | text | yes | yes | the insurance company | Insurance, Assurance, Takaful, Insurer (or the first 3 rows) |
| `policy_number` | id | yes | yes | the policy number | Policy No, Policy Number, Policy # |
| `insured_name` | text | yes | yes | the person insured or the policy holder | Insured, Name of Insured, Policy Holder, Policyholder, Insured Person |
| `premium` | money | yes | yes | the premium for the policy period | Premium, Annual Premium, Total Premium, Premium Payable |
| `start_date` | date | yes | yes | the date cover starts | Period of Insurance, From, Start Date, Effective Date, Commencement Date |
| `end_date` | date | yes | yes | the date cover ends (the policy expires) | To, End Date, Expiry Date, Valid Until |

Near-duplicate key: `insurer` + `policy_number` + `start_date`. Labels of no field: Sum Insured, Sum Assured, Cover Type, Deductible, Vehicle, Issue Date, Agent, Branch.

The premium is the field. The sum insured is printed next to "Sum Insured", which belongs to no field.

## tenancy_agreement

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `landlord` | text | yes | yes | the owner letting the property | Landlord, Lessor, Owner |
| `tenant` | text | yes | yes | the person renting the property | Tenant, Lessee |
| `property_address` | text | yes | yes | the address of the rented property | Property Address, Property, Premises, Address of the Premises |
| `monthly_rent` | money | yes | yes | the rent per month | Monthly Rent, Rent per month, Rent, rent of |
| `security_deposit` | money |  | yes | the refundable security deposit | Security Deposit, Deposit |
| `start_date` | date | yes | yes | the first day of the tenancy | Commencement Date, Start Date, with effect from |
| `end_date` | date | yes | yes | the last day of the tenancy | Expiry Date, End Date, ending on |

Near-duplicate key: `landlord` + `tenant` + `start_date`. Labels of no field: Agreement Date, made on, Witness, Signature, Annual increase, Notice period, Advance rent.

Landlord and tenant both count as a person for the holder filter, so "Nusrat's flat" finds the agreements where Nusrat is the landlord.

## fee_voucher

| Field | Kind | Required | Stable | Meaning | Labels looked for near the value |
|---|---|---|---|---|---|
| `student_name` | text | yes | yes | the student the fee is for | Student Name, Name of Student, Name |
| `registration_number` | id | yes | yes | the student's registration or roll number | Registration No, Reg No, Roll No, Student ID |
| `institution` | text | yes | yes | the school, college or university | University, College, School, Institute, Academy (or the first 3 rows) |
| `semester` | text | yes | yes | the semester or term the fee is for, e.g. Fall 2026 | Semester, Session, Term, Fee for |
| `amount` | money | yes | yes | the total to pay by the due date | Total Payable, Amount Payable, Total Amount, Total Fee, Payable within due date, Net Payable |
| `issue_date` | date |  | yes | the date the voucher was issued | Issue Date, Date of Issue, Issued on |
| `due_date` | date | yes | yes | the last date to pay | Due Date, Last Date, Pay by, Last date of payment |

Near-duplicate key: `registration_number` + `semester`. Labels of no field: Challan No, Voucher No, Tuition Fee, Admission Fee, Lab Fee, Library Fee, Exam Fee, Sports Fee, Late Fee, Payable after due date, Amount after due date, Program, Account No, Branch.

A voucher often prints three copies side by side (bank, institution, student). The amount after the due date is printed next to "Payable after due date", which belongs to no field.
## other

No fields. These documents are indexed for search only.

## Known limits

- The label lists come from the bills, cards and receipts we generate and wording common on local bills (Arrears, CNIC, Last date of payment). A real document that uses a label not in the list gets `needs_review`, not a wrong `verified`.
- OCR misreads labels too ("Bom" for "Born"); the value then needs review even when it is right.
- The 8 newer types (passport, transcript, bank statement, warranty card, offer letter, insurance policy, tenancy agreement, fee voucher) were checked only on generated documents (label check on gold values, no model). How well the model extracts them is measured after the merge.
- Two wrong values still pass the label check on generated typed PDFs: an employer name given as `position` (the letter says "the position of X at Employer", and "Position" is a label) and, on small warranty cards, any value in the first 3 rows given as `brand`.
- The CGPA rule assumes a 4.0 scale. A transcript on a 5.0 or percentage scale needs review.
- "Issue date is in the future" uses today's date, so it is the one rule whose result can change over time.
