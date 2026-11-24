#!/usr/bin/env python
"""Synthetic document generator for Sniff: fake personal documents (PDFs and images) plus ground truth.

    cd backend && uv run python ../data/synth/generate.py --out ../data/synth/out --seed 7

Writes out/dev/..., out/test/..., out/labels.jsonl and the draft test sets data/eval/search.jsonl and ask.jsonl.
Everything is fictional. Deterministic for a given seed. "Today" is fixed at 2026-11-22.

Label notes for the evaluators:
- `text` is every string drawn on the page, grouped into rows (top to bottom, left to right inside a row,
  rows joined by newline, cells in a row joined by a space). Real OCR output order can differ on multi-column pages.
- Some ID cards print the name in UPPER CASE; labels keep Title Case. Compare names case-insensitively.
- Dev uses a different set of people and 2025 dates so it never collides with test answers. Index only test/ for eval.
- Bills carry arrears: a bill left unpaid shows up as previous_balance on the next one, so amount_due includes it.
  Spending is the sum of current_charges. Some paid bills carry a PAID stamp with the payment date.
- The test split has exact copies (same bytes in Work/Backup), near-duplicates (the same document rendered again as a
  photo, scan or PDF) and conflicts (one CNIC on two cards with different dates). Label rows say `duplicate_of` (the
  original file) and `duplicate_kind` ("exact" or "near"). Gold answers count each document once.
- A few test records are incomplete on purpose: a field is not printed and its label is null.
"""
import argparse
import calendar
import io
import json
import random
import shutil
import textwrap
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pymupdf
from PIL import Image, ImageChops, ImageEnhance, ImageFilter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from sniff.schemas import FIELDS as SPEC  # run from backend/ with uv, as the usage line says

TODAY = date(2026, 11, 22)
FIELDS = {t: list(f) for t, f in SPEC.items()} | {"other": []}  # the contract: backend/sniff/schemas.py
MONEY = {f for t in SPEC.values() for f, s in t.items() if s.kind == "money"}
DFMT = ["%d/%m/%Y", "%d %b %Y", "%Y-%m-%d", "%d.%m.%Y"]
GRAY, WHITE, BLACK = (.45, .45, .45), (1, 1, 1), (0, 0, 0)


# ---------------------------------------------------------------- helpers
def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def mix(rng):
    """Date printer that picks a random format on every call (mixed formats on one page)."""
    return lambda d: d.strftime(rng.choice(DFMT))


def fixed(rng):
    f = rng.choice(DFMT)
    return lambda d: d.strftime(f)


def money(rng):
    """Money printer: one style per document ('Rs. 4,320', 'PKR 4320.00', '4,320/-')."""
    style = rng.randrange(3)

    def fm(a):
        dec = abs(a - round(a)) > 1e-9
        if style == 0:
            return f"Rs. {a:,.2f}" if dec else f"Rs. {a:,.0f}"
        if style == 1:
            return f"PKR {a:.2f}"
        return (f"{a:,.2f}" if dec else f"{a:,.0f}") + "/-"
    return fm


def num(x):
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else f"{x:.2f}"


def digits(rng, n):
    return "".join(str(rng.randint(0 if i else 1, 9)) for i in range(n))


class Page:
    """A reportlab page that also records the text it draws. y is measured from the top."""

    def __init__(self, w, h):
        self.w, self.h, self.items = w, h, []
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=(w, h), invariant=1)

    def t(self, x, y, s, font="Helvetica", size=10, color=BLACK, align="l"):
        wd = stringWidth(s, font, size)
        x0 = x if align == "l" else x - wd if align == "r" else x - wd / 2
        self.c.setFont(font, size)
        self.c.setFillColorRGB(*color)
        self.c.drawString(x0, self.h - y, s)
        self.items.append((y, x0, s))

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=1):
        if fill:
            self.c.setFillColorRGB(*fill)
        if stroke:
            self.c.setStrokeColorRGB(*stroke)
        self.c.setLineWidth(lw)
        self.c.rect(x, self.h - y - h, w, h, stroke=bool(stroke), fill=bool(fill))

    def line(self, x1, y1, x2, y2, color=BLACK, lw=.7):
        self.c.setStrokeColorRGB(*color)
        self.c.setLineWidth(lw)
        self.c.line(x1, self.h - y1, x2, self.h - y2)

    def circle(self, cx, cy, r, fill):
        self.c.setFillColorRGB(*fill)
        self.c.circle(cx, self.h - cy, r, stroke=0, fill=1)

    def pdf(self):
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()

    def text(self):
        rows = []
        for y, x, s in sorted(self.items):
            if rows and y - rows[-1][0] <= 3.5:
                rows[-1][1].append((x, s))
            else:
                rows.append([y, [(x, s)]])
        return "\n".join(" ".join(s for _, s in sorted(r[1])) for r in rows)


# ---------------------------------------------------------------- people and organisations
TEST_PEOPLE = {  # name -> date of birth
    "Ali Raza Khan": date(1978, 5, 14), "Ayesha Khan": date(1982, 9, 3), "Sara Khan": date(2003, 3, 21),
    "Hamza Khan": date(2007, 11, 3), "Nusrat Khan": date(1950, 1, 28),
}
DEV_PEOPLE = {"Bilal Ahmed": date(1985, 2, 11), "Zainab Malik": date(1999, 12, 9), "Omar Farooq": date(1970, 7, 30)}
HOUSEHOLDS = {
    "test": dict(cust="Ali Raza Khan", addr="House 14, Street 7, Garden Town, Lakeshore City"),
    "dev": dict(cust="Bilal Ahmed", addr="Flat 3B, Block C, Orchard Heights, Lakeshore City"),
}
SEASON = {
    "electricity": [5200, 4800, 5600, 7400, 9800, 12500, 13800, 13200, 10500, 7200, 5400, 5100],
    "gas": [9200, 8600, 6100, 3500, 2200, 1500, 1300, 1300, 1700, 3000, 5400, 8400],
    "internet": [3500] * 12,
}
PROVIDERS = [  # layout is fixed per provider, like real bills
    dict(name="Northern Electric Supply Co.", cat="electricity", svc="Electricity", color=(.1, .3, .6), layout=0,
         due=["Due Date", "Last date of payment"], amt=["Amount Payable", "Total Due"], unit="kWh", pre="ELC",
         comps=[("Energy Charges", .72), ("Fuel Price Adjustment", .12), ("Taxes and Duties", .1), ("Meter Rent", .06)]),
    dict(name="Metro Gas Ltd", cat="gas", svc="Gas", color=(.8, .35, .1), layout=1,
         due=["Pay by", "Due Date"], amt=["Total Due", "Net Amount Due"], unit="m3", pre="MG",
         comps=[("Gas Charges", .8), ("Fixed Charges", .08), ("Gas Development Surcharge", .07), ("Taxes", .05)]),
    dict(name="FastLink Internet", cat="internet", svc="Internet", color=(.1, .5, .35), layout=2,
         due=["Last date of payment", "Pay by"], amt=["Net Amount Due", "Amount Payable"], unit="GB", pre="FL",
         comps=[("Package Charges (50 Mbps)", .85), ("Add-ons", .05), ("Taxes", .1)]),
]
INSTS = ["Lakeshore University", "Metro Institute of Technology", "Brightpath Academy", "Northfield College",
         "Summit Online Learning", "Lifeline Training Center", "Brightpath School"]
SOON, WINDOW = TODAY, TODAY + timedelta(days=90)


# ---------------------------------------------------------------- id cards
def card_id(rng, kind, issue):
    if kind == "national":
        return f"{digits(rng, 5)}-{digits(rng, 7)}-{rng.randint(1, 9)}"
    if kind == "licence":
        return f"{rng.choice(['LHR', 'KHI', 'ISB'])}-{issue.year % 100:02d}-{digits(rng, 6)}"
    return f"{issue.year % 100:02d}L-{digits(rng, 4)}" if kind == "student" else f"EMP-{digits(rng, 5)}"


def id_national(rng, d):
    p, f = Page(640, 400), fixed(rng)
    p.rect(0, 0, 640, 400, fill=(.93, .97, .93))
    p.rect(0, 0, 640, 70, fill=(.05, .4, .25))
    p.t(320, 32, "REPUBLIC OF EASTLAND", "Helvetica-Bold", 18, WHITE, "c")
    p.t(320, 55, "NATIONAL IDENTITY CARD", "Helvetica", 13, WHITE, "c")
    p.rect(30, 100, 150, 190, fill=(.75, .78, .8))
    p.circle(105, 165, 32, (.5, .53, .56))
    p.rect(55, 210, 100, 80, fill=(.5, .53, .56))
    lb = rng.choice([("Name", "Identity Number", "Date of Birth", "Date of Issue", "Date of Expiry"),
                     ("Full Name", "ID No.", "Born", "Issued", "Valid Until")])
    vals = [d["name"].upper(), d["id"], f(d["dob"]), f(d["issue"]), f(d["expiry"])]
    for i, (a, b) in enumerate(zip(lb, vals)):
        p.t(215, 105 + i * 50, a, "Helvetica", 9, GRAY)
        p.t(215, 123 + i * 50, b, "Helvetica-Bold", 16)
    for i in range(40):
        p.rect(30 + i * 5, 335, rng.choice([1, 2, 3]), 40, fill=BLACK)
    return p


def id_licence(rng, d):
    p, f = Page(640, 400), fixed(rng)
    p.rect(0, 0, 640, 400, fill=(.92, .94, .99))
    p.rect(0, 0, 640, 60, fill=(.1, .2, .55))
    p.t(25, 38, "DRIVING LICENCE", "Helvetica-Bold", 22, WHITE)
    p.t(615, 38, "Eastland Transport Authority", "Helvetica", 11, WHITE, "r")
    p.rect(25, 90, 120, 150, fill=(.75, .78, .85))
    p.circle(85, 140, 26, (.5, .53, .6))
    p.t(170, 105, "Licence No.", "Helvetica", 9, GRAY)
    p.t(170, 128, d["id"], "Helvetica-Bold", 22, (.1, .2, .55))
    nm = rng.choice(["Name", "Holder"])
    cells = [(nm, d["name"], 170, 170), ("DOB", f(d["dob"]), 420, 170), ("Valid From", f(d["issue"]), 170, 230),
             (rng.choice(["Valid Until", "Expiry"]), f(d["expiry"]), 420, 230)]
    for a, b, x, y in cells:
        p.t(x, y, a, "Helvetica", 9, GRAY)
        p.t(x, y + 18, b, "Helvetica-Bold", 14)
    p.t(25, 290, "Vehicle classes: LTV, MTV", "Helvetica", 11)
    p.t(25, 330, "Signature of holder", "Helvetica-Oblique", 9, GRAY)
    p.line(25, 322, 220, 322)
    return p


def id_inst(rng, d):
    p, f = Page(640, 400), fixed(rng)
    p.rect(0, 0, 640, 400, fill=(1, .96, .9))
    p.rect(0, 0, 640, 80, fill=(.85, .45, .1))
    p.t(320, 38, d["inst"], "Times-Bold", 22, WHITE, "c")
    p.t(320, 62, "STUDENT CARD" if d["sub"] == "student" else "STAFF ID CARD", "Helvetica", 13, WHITE, "c")
    p.rect(30, 105, 140, 175, fill=(.85, .8, .75))
    p.circle(100, 160, 30, (.6, .55, .5))
    numlbl = "Roll No." if d["sub"] == "student" else "Employee No."
    rows = [("Name", d["name"]), (numlbl, d["id"]), ("Date of Birth", f(d["dob"])), ("Issued", f(d["issue"]))]
    rows += [("Valid Thru", f(d["expiry"]))] if d["expiry"] else []  # incomplete record: no expiry printed
    for i, (a, b) in enumerate(rows):
        p.t(200, 108 + i * 50, a, "Helvetica", 9, GRAY)
        p.t(200, 126 + i * 50, b, "Helvetica-Bold", 15)
    p.t(320, 380, "If found please return to the registrar's office", "Helvetica-Oblique", 9, GRAY, "c")
    return p


def ids(rng, rows, people, fmts):
    out, cnic = [], {}
    for (who, kind, issue, expiry, inst), fmt in zip(rows, fmts):
        sub, layout = ("student", id_inst) if kind == "student" else ("staff", id_inst) if kind == "staff" else (
            kind, id_national if kind == "national" else id_licence)
        if kind == "national" and who in cnic:  # a renewed or re-issued card keeps the number: a conflict on dates
            idn = cnic[who]
        else:
            idn = card_id(rng, kind, issue)
            if kind == "national":
                cnic[who] = idn
        d = dict(name=who, id=idn, dob=people[who], issue=issue, expiry=expiry, inst=inst, sub=sub)
        f = {"name": who, "id_number": idn, "date_of_birth": people[who].isoformat(),
             "issue_date": issue.isoformat(), "expiry_date": expiry.isoformat()}
        out.append(dict(type="id_card", fmt=fmt, fields=f, layout=layout, d=d, dt=issue,
                        meta=dict(person=who, kind=kind, inst=inst, expiry=expiry)))
    return out


D = date
TEST_IDS = [
    ("Ali Raza Khan", "national", D(2017, 2, 10), D(2027, 2, 10), None),
    ("Ali Raza Khan", "national", D(2007, 3, 5), D(2017, 3, 5), None),
    ("Ali Raza Khan", "licence", D(2023, 6, 15), D(2028, 6, 15), None),
    ("Ali Raza Khan", "staff", D(2024, 1, 8), D(2027, 1, 8), "Harbor Logistics Pvt Ltd"),
    ("Ayesha Khan", "national", D(2022, 8, 19), D(2032, 8, 19), None),
    ("Ayesha Khan", "national", D(2024, 5, 2), D(2034, 5, 2), None),  # re-issued after a loss: same CNIC, new dates
    ("Ayesha Khan", "licence", D(2020, 7, 2), D(2025, 7, 2), None),
    ("Ayesha Khan", "staff", D(2025, 4, 1), D(2028, 4, 1), "Brightpath School"),
    ("Sara Khan", "national", D(2021, 10, 12), D(2031, 10, 12), None),
    ("Sara Khan", "student", D(2022, 9, 1), D(2026, 12, 31), "Lakeshore University"),
    ("Sara Khan", "licence", D(2024, 2, 20), D(2029, 2, 20), None),
    ("Hamza Khan", "national", D(2025, 12, 1), D(2035, 12, 1), None),
    ("Hamza Khan", "student", D(2025, 8, 25), D(2027, 8, 25), "Northfield College"),
    ("Hamza Khan", "student", D(2021, 4, 12), D(2024, 4, 12), "Brightpath School"),
    ("Nusrat Khan", "national", D(2016, 12, 4), D(2026, 12, 4), None),
]
DEV_IDS = [
    ("Bilal Ahmed", "national", D(2019, 5, 6), D(2029, 5, 6), None),
    ("Bilal Ahmed", "licence", D(2021, 9, 14), D(2026, 12, 14), None),
    ("Zainab Malik", "national", D(2018, 3, 3), D(2028, 3, 3), None),
    ("Zainab Malik", "student", D(2023, 9, 1), D(2027, 6, 30), "Northfield College"),
    ("Omar Farooq", "national", D(2010, 1, 20), D(2020, 1, 20), None),
    ("Omar Farooq", "staff", D(2025, 1, 6), D(2028, 1, 6), "Metro Institute of Technology"),
    ("Omar Farooq", "licence", D(2022, 4, 11), D(2027, 4, 11), None),
]


# ---------------------------------------------------------------- utility bills
def charges(rng, prov, total):
    parts = [round(total * w, 2 if total % 1 else 0) for _, w in prov["comps"][:-1]]
    parts.append(round(total - sum(parts), 2))
    return [(lbl, a) for (lbl, _), a in zip(prov["comps"], parts)]


def bill_vars(rng, d):
    mon = rng.choice([d["month"].strftime("%B %Y"), d["month"].strftime("%b-%Y"), d["month"].strftime("%m/%Y"),
                      d["month"].strftime("%Y-%m")])
    return mix(rng), money(rng), mon, rng.choice(d["prov"]["due"]), rng.choice(d["prov"]["amt"])


def bill_labels(rng):
    """Labels for current charges and arrears, drawn after bill_vars so older draws stay the same."""
    return rng.choice(["Current Charges", "Current Bill", "Charges for the month"]), rng.choice(["Arrears", "Previous Balance", "Balance brought forward"])


def bill_a(rng, d):
    p, c, pv = Page(595, 842), d["prov"]["color"], d["prov"]
    D_, fm, mon, dl, al = bill_vars(rng, d)
    p.rect(0, 0, 595, 90, fill=c)
    p.t(40, 45, pv["name"], "Helvetica-Bold", 22, WHITE)
    p.t(40, 68, f"{pv['svc']} Bill", "Helvetica", 12, WHITE)
    p.t(555, 45, "Customer Care: 0800-" + d["phone"], "Helvetica", 9, WHITE, "r")
    p.t(40, 125, "Customer Name", "Helvetica", 8, GRAY)
    p.t(40, 139, d["cust"], "Helvetica-Bold", 11)
    p.t(40, 165, "Service Address", "Helvetica", 8, GRAY)
    p.t(40, 179, d["addr"], "Helvetica", 10)
    p.rect(340, 110, 215, 86, stroke=c)
    y = 129
    for lb, v in [("Account No.", d["acct"]), ("Billing Month", mon), ("Issue Date", D_(d["issue"])), ("Reference", d["ref"])]:
        p.t(350, y, lb, "Helvetica", 9, GRAY)
        p.t(545, y, v, "Helvetica-Bold", 9, align="r")
        y += 18
    p.rect(40, 222, 515, 20, fill=(.92, .92, .92))
    p.t(48, 236, "Description", "Helvetica-Bold", 9)
    p.t(547, 236, "Amount", "Helvetica-Bold", 9, align="r")
    y = 262
    for lb, a in d["comps"]:
        p.t(48, y, lb, "Helvetica", 10)
        p.t(547, y, fm(a), "Helvetica", 10, align="r")
        p.line(40, y + 7, 555, y + 7, (.8, .8, .8))
        y += 22
    p.t(48, y, d["usage"], "Helvetica-Oblique", 9, GRAY)
    y += 24
    cl, pl = bill_labels(rng)
    for lb, a in [(cl, d["cur"]), (pl, d["prev"])]:
        p.t(48, y, lb, "Helvetica-Bold", 10)
        p.t(547, y, fm(a), "Helvetica-Bold", 10, align="r")
        y += 20
    y += 6
    p.rect(300, y, 255, 30, fill=c)
    p.t(310, y + 20, al, "Helvetica-Bold", 11, WHITE)
    p.t(545, y + 20, fm(d["amount"]), "Helvetica-Bold", 13, WHITE, "r")
    y += 60
    p.rect(40, y, 515, 30, stroke=(.7, .1, .1))
    if d["due"]:
        p.t(50, y + 19, dl + ":", "Helvetica-Bold", 11)
        p.t(58 + stringWidth(dl + ":", "Helvetica-Bold", 11), y + 19, D_(d["due"]), "Helvetica-Bold", 11)
    p.t(545, y + 19, "Amount after due date: " + fm(d["late"]), "Helvetica", 9, align="r")
    if d["paid_on"]:
        p.t(50, y + 58, "PAID", "Helvetica-Bold", 20, (.1, .55, .2))
        p.t(120, y + 58, "Paid on: " + D_(d["paid_on"]), "Helvetica-Bold", 10, (.1, .55, .2))
    p.t(40, y + 82, "Pay online, through your mobile banking app or at any authorised bank branch.", "Helvetica", 8, GRAY)
    return p


def bill_b(rng, d):
    p, c, pv = Page(595, 842), d["prov"]["color"], d["prov"]
    D_, fm, mon, dl, al = bill_vars(rng, d)
    p.rect(0, 0, 14, 842, fill=c)
    p.t(40, 60, pv["name"], "Times-Bold", 22, c)
    p.t(40, 80, f"Monthly {pv['svc']} Statement", "Times-Italic", 13)
    p.line(40, 92, 555, 92, c, 1.5)
    y = 122
    for lb, v in [("Account Number", d["acct"]), ("Statement Period", mon), ("Statement Date", D_(d["issue"])),
                  ("Billed To", d["cust"]), ("Premises", d["addr"])]:
        p.t(40, y, lb + ":", "Helvetica", 10, GRAY)
        p.t(170, y, v, "Helvetica-Bold", 10)
        y += 20
    p.rect(40, 250, 515, 76, fill=(.96, .94, .9))
    p.t(60, 275, al, "Helvetica", 11)
    p.t(60, 307, fm(d["amount"]), "Helvetica-Bold", 24, c)
    if d["due"]:
        p.t(330, 275, dl, "Helvetica", 11)
        p.t(330, 307, D_(d["due"]), "Helvetica-Bold", 18)
    p.t(40, 360, "Breakdown of charges", "Times-Bold", 14)
    y = 385
    for lb, a in d["comps"]:
        p.t(40, y, lb, "Times-Roman", 11)
        p.t(555, y, fm(a), "Times-Roman", 11, align="r")
        y += 20
    cl, pl = bill_labels(rng)
    for lb, a in [(cl, d["cur"])] + ([(pl, d["prev"])] if d["prev"] else []):  # arrears printed only when there are any
        p.t(40, y + 4, lb, "Times-Bold", 11)
        p.t(555, y + 4, fm(a), "Times-Bold", 11, align="r")
        y += 20
    p.t(40, y + 10, d["usage"], "Times-Italic", 10)
    p.t(40, y + 50, "A late payment surcharge applies if the bill is paid after the due date: " + fm(d["late"]),
        "Helvetica", 8, GRAY)
    if d["paid_on"]:
        p.t(330, y + 90, "PAID", "Helvetica-Bold", 22, (.1, .55, .2))
        p.t(410, y + 90, "Paid on " + D_(d["paid_on"]), "Helvetica", 11, (.1, .55, .2))
    return p


def bill_c(rng, d):
    p, pv = Page(595, 842), d["prov"]
    D_, fm, mon, dl, al = bill_vars(rng, d)
    p.t(40, 55, pv["name"].upper(), "Courier-Bold", 14)
    p.t(40, 72, f"INVOICE - {pv['svc'].upper()} SERVICE", "Courier", 10)
    p.t(40, 86, "=" * 62, "Courier", 10)
    y = 106
    for lb, v in [("Account", d["acct"]), ("Customer", d["cust"]), ("Address", d["addr"]), ("Period", mon),
                  ("Invoice date", D_(d["issue"]))]:
        p.t(40, y, f"{lb:.<20} {v}", "Courier", 10)
        y += 15
    p.t(40, y, "-" * 62, "Courier", 10)
    y += 16
    for lb, a in d["comps"]:
        p.t(40, y, f"{lb:<36}{fm(a):>26}", "Courier", 10)
        y += 15
    p.t(40, y, "=" * 62, "Courier", 10)
    y += 17
    cl, pl = bill_labels(rng)
    for lb, a in [(cl, d["cur"]), (pl, d["prev"])]:
        p.t(40, y, f"{lb:<36}{fm(a):>26}", "Courier", 10)
        y += 15
    p.t(40, y, f"{al:<36}{fm(d['amount']):>26}", "Courier-Bold", 10)
    y += 17
    if d["due"]:
        p.t(40, y, f"{dl:.<20} {D_(d['due'])}", "Courier-Bold", 10)
    p.t(40, y + 30, d["usage"], "Courier", 9)
    p.t(40, y + 46, "Quote the account number when paying. Thank you.", "Courier", 9)
    if d["paid_on"]:
        p.t(40, y + 70, f"*** PAID *** Paid on {D_(d['paid_on'])}", "Courier-Bold", 10)
    return p


UNPAID = {"electricity": {6, 11, 12}, "gas": {2, 3, 12}, "internet": set()}  # test months left unpaid: next bill has arrears


def bills(rng, split, year, months_for, fmts):
    hh, out, it = HOUSEHOLDS[split], [], iter(fmts)
    unpaid = UNPAID if split == "test" else {}
    for prov in PROVIDERS:
        acct = f"{prov['pre']}-{digits(rng, 4)}-{digits(rng, 4)}"
        prev_due, prev_month = 0.0, None
        for m in months_for(prov):
            total = round(SEASON[prov["cat"]][m - 1] * rng.uniform(.9, 1.1))
            if rng.random() < .3:
                total += rng.randint(1, 99) / 100
            issue = add_months(date(year, m, 1), 1) + timedelta(days=rng.randint(0, 4))
            due = issue + timedelta(days=rng.randint(10, 21))
            used = round(total / rng.uniform(30, 45)) if prov["cat"] == "electricity" else rng.randint(20, 400)
            comps = charges(rng, prov, total)
            prev = prev_due if prev_month in unpaid.get(prov["cat"], ()) and prev_month == m - 1 else 0.0
            amount = round(total + prev, 2)
            pr = random.Random(f"paid-{split}-{acct}-{m}")  # own generator, so the draws above stay as they were
            paid = m not in unpaid.get(prov["cat"], ())
            paid_on = issue + timedelta(days=pr.randint(1, (due - issue).days)) if paid and (m == 12 or pr.random() < .45) else None
            prev_due, prev_month = amount, m
            d = dict(prov=prov, acct=acct, cust=hh["cust"], addr=hh["addr"], month=date(year, m, 1), issue=issue,
                     due=due, amount=amount, cur=float(total), prev=prev, paid_on=paid_on, late=round(amount * 1.08, 2),
                     comps=comps, phone=digits(rng, 4) + "-" + digits(rng, 3), ref=digits(rng, 9),
                     usage=f"Consumption this month: {used} {prov['unit']}")
            tax = next(a for lb, a in comps if "Tax" in lb)
            f = {"provider": prov["name"], "account_number": acct, "billing_month": f"{year}-{m:02d}",
                 "bill_date": issue.isoformat(), "due_date": due.isoformat(), "current_charges": float(total),
                 "previous_balance": float(prev) if prev or prov["layout"] != 1 else None,  # layout b prints arrears only when owed
                 "tax": float(tax), "amount_due": amount, "payment_status": "paid" if paid_on else None,
                 "payment_date": paid_on.isoformat() if paid_on else None}
            out.append(dict(type="utility_bill", fmt=next(it), fields=f, d=d, dt=issue,
                            layout=[bill_a, bill_b, bill_c][prov["layout"]],
                            meta=dict(cat=prov["cat"], month=m, year=year, used=used, unit=prov["unit"])))
    return out


# ---------------------------------------------------------------- certificates
def cert_vars(rng, d):
    return mix(rng), rng.choice(["Issue Date", "Date of Issue", "Issued on", "Awarded on"])


def cert_a(rng, d):
    p, c = Page(842, 595), d["color"]
    D_, dl = cert_vars(rng, d)
    p.rect(20, 20, 802, 555, stroke=c, lw=4)
    p.rect(32, 32, 778, 531, stroke=c, lw=1)
    p.t(421, 100, d["inst"].upper(), "Times-Bold", 20, c, "c")
    p.t(421, 155, d["head"], "Times-Bold", 32, align="c")
    p.t(421, 200, "This is to certify that", "Times-Italic", 14, align="c")
    p.t(421, 250, d["holder"], "Times-BoldItalic", 32, align="c")
    p.t(421, 290, d["verb"], "Times-Italic", 14, align="c")
    for i, ln in enumerate(textwrap.wrap(d["title"], 44)):
        p.t(421, 335 + i * 28, ln, "Times-Bold", 22, align="c")
    p.t(421, 410, f"{dl}: {D_(d['issue'])}", "Times-Roman", 14, align="c")
    p.line(120, 500, 300, 500)
    p.t(210, 516, "Registrar", "Times-Roman", 11, align="c")
    p.line(540, 500, 720, 500)
    p.t(630, 516, "Director", "Times-Roman", 11, align="c")
    p.t(421, 548, "Certificate No. " + d["cid"], "Helvetica", 9, GRAY, "c")
    return p


def cert_b(rng, d):
    p, c = Page(595, 842), d["color"]
    p.rect(25, 25, 545, 792, stroke=c, lw=3)
    p.rect(33, 33, 529, 776, stroke=c, lw=.8)
    for i, ln in enumerate(textwrap.wrap(d["inst"].upper(), 26)):
        p.t(297, 115 + i * 28, ln, "Times-Bold", 24, c, "c")
    p.t(297, 190, "On the recommendation of the Academic Council", "Times-Italic", 12, align="c")
    p.t(297, 250, "confers upon", "Times-Roman", 14, align="c")
    p.t(297, 310, d["holder"], "Times-BoldItalic", 30, align="c")
    p.t(297, 360, "the degree of" if d["kind"] == "degree" else "the certificate in", "Times-Roman", 14, align="c")
    y = 405
    for ln in textwrap.wrap(d["title"], 34):
        p.t(297, y, ln, "Times-Bold", 22, align="c")
        y += 30
    p.t(297, y + 30, "with all the rights and privileges pertaining thereto", "Times-Italic", 12, align="c")
    dt = d["issue"]
    p.t(297, y + 100, f"Given this {ordinal(dt.day)} day of {dt.strftime('%B')}, {dt.year}", "Times-Roman", 13, align="c")
    p.line(200, 700, 395, 700)
    p.t(297, 718, {"degree": "Vice Chancellor", "school": "Principal"}.get(d["kind"], "Programme Director"), "Times-Roman", 11, align="c")
    p.t(297, 780, "Serial No. " + d["cid"], "Helvetica", 9, GRAY, "c")
    return p


def cert_c(rng, d):
    p, c = Page(842, 595), d["color"]
    D_, dl = cert_vars(rng, d)
    p.rect(0, 0, 230, 595, fill=c)
    for i, ln in enumerate(textwrap.wrap(d["inst"], 16)):
        p.t(115, 120 + i * 24, ln, "Helvetica-Bold", 17, WHITE, "c")
    p.t(270, 110, "Certificate", "Helvetica-Bold", 38)
    p.t(270, 140, "of " + ("Completion" if d["kind"] == "course" else "Achievement"), "Helvetica", 16, GRAY)
    p.t(270, 200, "Presented to", "Helvetica", 11, GRAY)
    p.t(270, 240, d["holder"], "Helvetica-Bold", 28)
    p.line(270, 252, 790, 252, c, 1.5)
    p.t(270, 292, "for successfully completing" if d["kind"] == "course" else "for being awarded", "Helvetica", 12)
    for i, ln in enumerate(textwrap.wrap(d["title"], 38)):
        p.t(270, 325 + i * 26, ln, "Helvetica-Bold", 19, c)
    p.t(270, 440, dl, "Helvetica", 10, GRAY)
    p.t(270, 458, D_(d["issue"]), "Helvetica-Bold", 13)
    p.t(520, 440, "Certificate ID", "Helvetica", 10, GRAY)
    p.t(520, 458, d["cid"], "Helvetica-Bold", 13)
    p.t(270, 540, "Issued by " + d["inst"], "Helvetica-Oblique", 10, GRAY)
    return p


def certs(rng, rows, fmts):
    out = []
    for (who, title, inst, kind, issue), fmt in zip(rows, fmts):
        verb = {"course": "has successfully completed the course", "degree": "has fulfilled the requirements for",
                "school": "has passed the examination for"}[kind]
        head = "CERTIFICATE OF COMPLETION" if kind == "course" else "DEGREE CERTIFICATE" if kind == "degree" else "CERTIFICATE"
        d = dict(holder=who, title=title, inst=inst, kind=kind, issue=issue, verb=verb, head=head,
                 cid=f"{rng.choice('ABCDEFGH')}{rng.choice('KLMNPQ')}-{digits(rng, 6)}",
                 color=rng.choice([(.1, .25, .5), (.5, .1, .15), (.15, .4, .3), (.3, .2, .5)]))
        layout = rng.choice([cert_a, cert_b] if kind != "course" else [cert_a, cert_b, cert_c, cert_c])
        f = {"holder_name": who, "title": title, "institution": inst, "issue_date": issue.isoformat()}
        out.append(dict(type="certificate", fmt=fmt, fields=f, d=d, dt=issue, layout=layout,
                        meta=dict(person=who, kind=kind, title=title, inst=inst, cid=d["cid"])))
    return out


def course_dates(rng, n):
    return sorted((date(2018, 1, 1) + timedelta(days=rng.randint(0, 2850)) for _ in range(n)))


def cert_rows(rng, spec):
    """spec: person -> (degree title, institution, kind, issue) + [(course, institution), ...]"""
    rows = []
    for who, (deg, courses) in spec.items():
        rows.append((who, deg[0], deg[1], deg[2], deg[3]))
        for (t, i), dt in zip(courses, course_dates(rng, len(courses))):
            rows.append((who, t, i, "course", dt))
    return rows


TEST_CERTS = {
    "Sara Khan": (("Bachelor of Science in Computer Science", "Lakeshore University", "degree", D(2025, 7, 10)),
                  [("Introduction to Machine Learning", "Summit Online Learning"), ("Python Programming", "Summit Online Learning"),
                   ("First Aid and CPR", "Lifeline Training Center"), ("English Language Proficiency", "Brightpath Academy"),
                   ("Data Analysis with SQL", "Summit Online Learning"), ("Technical Writing Workshop", "Metro Institute of Technology")]),
    "Ali Raza Khan": (("Master of Business Administration", "Northfield College", "degree", D(2008, 6, 30)),
                      [("Project Management Fundamentals", "Metro Institute of Technology"),
                       ("Leadership and Team Management", "Summit Online Learning"), ("Defensive Driving Course", "Lifeline Training Center"),
                       ("Excel for Business", "Summit Online Learning"), ("Workplace Safety Training", "Lifeline Training Center")]),
    "Ayesha Khan": (("Bachelor of Education", "Northfield College", "degree", D(2004, 7, 15)),
                    [("Classroom Management", "Brightpath Academy"), ("Early Childhood Teaching Methods", "Brightpath Academy"),
                     ("First Aid and CPR", "Lifeline Training Center"), ("Digital Teaching Tools", "Summit Online Learning"),
                     ("Child Psychology Basics", "Summit Online Learning")]),
    "Hamza Khan": (("Secondary School Certificate (Science)", "Brightpath School", "school", D(2023, 6, 20)),
                   [("Python Programming", "Summit Online Learning"), ("Robotics Workshop", "Metro Institute of Technology"),
                    ("Typing and Computer Basics", "Brightpath Academy")]),
    "Nusrat Khan": (("Sewing and Tailoring Course", "Lifeline Training Center", "course", D(2012, 3, 9)),
                    [("Basic Computer Literacy", "Brightpath Academy"), ("First Aid and CPR", "Lifeline Training Center")]),
}
DEV_CERTS = {
    "Bilal Ahmed": (("Bachelor of Commerce", "Lakeshore University", "degree", D(2007, 8, 2)),
                    [("Digital Marketing Essentials", "Summit Online Learning"), ("Introduction to Cloud Computing", "Summit Online Learning")]),
    "Zainab Malik": (("Bachelor of Fine Arts", "Northfield College", "degree", D(2022, 7, 21)),
                     [("Graphic Design Basics", "Brightpath Academy")]),
    "Omar Farooq": (("Diploma in Electrical Engineering", "Metro Institute of Technology", "degree", D(1992, 6, 18)),
                    [("Welding Safety Certification", "Lifeline Training Center")]),
}


# ---------------------------------------------------------------- receipts
ELEC, HOME = "Metro Electronics Mart", "HomeEase Appliances"
GROC = [("Basmati Rice 5kg", 1400, 1900), ("Cooking Oil 3L", 1800, 2400), ("Milk 1L", 280, 340), ("Eggs Dozen", 380, 460),
        ("Bread", 150, 220), ("Tea 950g", 1100, 1500), ("Sugar 2kg", 300, 380), ("Chicken 1kg", 600, 800),
        ("Yogurt 1kg", 300, 400), ("Onions 2kg", 200, 300), ("Tomatoes 1kg", 120, 260), ("Flour 10kg", 1500, 1900)]
ACC = [("USB-C Cable", 800, 1500), ("Screen Guard", 500, 900), ("Laptop Sleeve", 2500, 4500)]
# cat -> (group, merchants, items, warranty-eligible)
RCATS = {
    "laptop": ("electronics", [ELEC], [("Laptop i5 16GB 512GB", 95000, 160000), ("Laptop Ryzen 7 16GB", 110000, 175000)], True),
    "phone": ("electronics", [ELEC], [("Smartphone 8/256GB", 45000, 120000)], True),
    "earbuds": ("electronics", [ELEC], [("Wireless Earbuds", 6000, 22000), ("Headphones BT", 5000, 18000)], True),
    "monitor": ("electronics", [ELEC], [("Monitor 24in IPS", 28000, 45000)], True),
    "printer": ("electronics", [ELEC], [("Laser Printer", 32000, 52000)], True),
    "microwave": ("appliance", [HOME], [("Microwave Oven 25L", 24000, 42000)], True),
    "airfryer": ("appliance", [HOME], [("Air Fryer 5L", 14000, 26000)], True),
    "fridge": ("appliance", [HOME], [("Refrigerator 14cft", 85000, 140000)], True),
    "ac": ("appliance", [HOME], [("Split AC 1.5 Ton", 120000, 190000)], True),
    "dispenser": ("appliance", [HOME], [("Water Dispenser", 22000, 38000)], True),
    "grocery": ("grocery", ["FreshBasket Grocers", "Daily Needs Mart"], GROC, False),
    "pharmacy": ("pharmacy", ["Green Leaf Pharmacy"], [("Paracetamol 500mg", 120, 260), ("Vitamin C Tablets", 450, 900),
                 ("Cough Syrup", 280, 520), ("Bandages Pack", 150, 300), ("Hand Sanitizer", 200, 380), ("Antacid Tablets", 120, 240)], False),
    "fuel": ("fuel", ["CityFuel Station"], [("Petrol", 6000, 12000)], False),
    "clothing": ("clothing", ["Urban Threads"], [("Cotton Kurta", 3500, 7000), ("Denim Jeans", 4500, 8500),
                 ("Winter Jacket", 9000, 18000), ("Sneakers", 6000, 14000)], False),
    "books": ("books", ["BookNook"], [("Novel Paperback", 900, 2200), ("Textbook", 3500, 9000), ("Notebook Set", 400, 900)], False),
    "restaurant": ("restaurant", ["Spice Garden Restaurant"], [("Chicken Karahi", 1800, 2800), ("Garlic Naan", 100, 200),
                   ("Mint Margarita", 350, 500), ("Biryani Plate", 700, 1100)], False),
}
TEST_RCATS = dict(laptop=5, phone=6, earbuds=4, monitor=3, printer=2, microwave=3, airfryer=3, fridge=2, ac=2, dispenser=2,
                  grocery=16, pharmacy=7, fuel=7, clothing=6, books=4, restaurant=4)
DEV_RCATS = ["laptop", "phone", "microwave", "airfryer", "earbuds", "grocery", "grocery", "grocery", "pharmacy", "pharmacy",
             "fuel", "fuel", "clothing", "clothing", "books", "restaurant"]
BUCKETS = ["soon", "expired", "far", "far", "expired", "soon", "far"]
TAXES = {"electronics": ("GST 17%", .17), "appliance": ("GST 17%", .17), "restaurant": ("Service Tax 16%", .16)}
ADDR = {m: f"{digits(random.Random(m), 2)} {s}, Lakeshore City" for m, s in [
    (ELEC, "Tech Plaza, Main Boulevard"), (HOME, "Market Road"), ("FreshBasket Grocers", "Garden Town Market"),
    ("Daily Needs Mart", "Block C Commercial"), ("Green Leaf Pharmacy", "Clinic Road"), ("CityFuel Station", "Ring Road"),
    ("Urban Threads", "Mall Avenue"), ("BookNook", "University Road"), ("Spice Garden Restaurant", "Food Street")]}


def in_bucket(b, until):
    return until < TODAY if b == "expired" else TODAY < until <= WINDOW if b == "soon" else until > WINDOW


def rec_vars(rng, d, labels):
    return mix(rng), money(rng), rng.choice(labels), rng.choice(["Warranty valid until", "Warranty till", "Guarantee expires",
                                                              "Warranty expires on"])


def rec_thermal(rng, d):
    D_, fm, tl, wl = rec_vars(rng, d, ["TOTAL", "GRAND TOTAL", "AMOUNT PAID", "NET PAYABLE"])
    n = len(d["items"])
    p = Page(226, 230 + 13 * n + (36 if d["until"] else 0))
    p.t(113, 28, d["merchant"].upper(), "Courier-Bold", 10, align="c")
    p.t(113, 41, d["addr"], "Courier", 7, align="c")
    p.t(113, 52, "Tel: 042-" + d["phone"], "Courier", 7, align="c")
    p.t(10, 66, "-" * 42, "Courier", 8)
    if d["date"]:
        p.t(10, 80, "Date: " + D_(d["date"]), "Courier", 8)
    p.t(216, 80, "Time: " + d["time"], "Courier", 8, align="r")
    p.t(10, 92, "Receipt #" + d["no"], "Courier", 8)
    p.t(10, 104, "-" * 42, "Courier", 8)
    y = 118
    for q, name, u in d["items"]:
        p.t(10, y, f"{q} x {name}", "Courier", 8)
        p.t(216, y, fm(q * u), "Courier", 8, align="r")
        y += 13
    p.t(10, y - 4, "-" * 42, "Courier", 8)
    y += 12
    if d["tax"]:
        for lb, v in [("Subtotal", d["sub"]), (d["taxlbl"], d["tax"])]:
            p.t(10, y, lb, "Courier", 8)
            p.t(216, y, fm(v), "Courier", 8, align="r")
            y += 12
    p.t(10, y + 3, tl, "Courier-Bold", 10)
    p.t(216, y + 3, fm(d["total"]), "Courier-Bold", 10, align="r")
    y += 20
    if d["pay"] == "Cash":
        tend = -(-d["total"] // 500) * 500
        for lb, v in [("Cash tendered", tend), ("Change", round(tend - d["total"], 2))]:
            p.t(10, y, lb, "Courier", 8)
            p.t(216, y, fm(v), "Courier", 8, align="r")
            y += 12
    else:
        p.t(10, y, "Paid by: Card ****" + d["no"][-4:], "Courier", 8)
        y += 12
    if d["until"]:
        y += 8
        p.t(113, y, "*** WARRANTY ***", "Courier-Bold", 8, align="c")
        p.t(113, y + 12, f"{wl} {D_(d['until'])}", "Courier", 8, align="c")
        y += 12
    p.t(113, y + 28, "THANK YOU FOR SHOPPING WITH US", "Courier", 8, align="c")
    return p


def rec_invoice(rng, d):
    D_, fm, tl, wl = rec_vars(rng, d, ["Total", "Amount Due", "Net Total"])
    c = rng.choice([(.15, .25, .45), (.4, .15, .2), (.1, .4, .35)])
    p = Page(420, 270 + 20 * len(d["items"]) + (36 if d["tax"] else 0) + (60 if d["until"] else 0))
    p.rect(0, 0, 420, 62, fill=c)
    p.t(20, 28, d["merchant"], "Helvetica-Bold", 17, WHITE)
    p.t(20, 46, f"{d['addr']} | Tel 042-{d['phone']}", "Helvetica", 8, WHITE)
    p.t(20, 94, rng.choice(["SALES INVOICE", "CASH MEMO", "TAX INVOICE"]), "Helvetica-Bold", 13)
    p.t(400, 94, "No. " + d["no"], "Helvetica", 10, align="r")
    if d["date"]:
        p.t(20, 114, "Date: " + D_(d["date"]), "Helvetica", 10)
    p.t(400, 114, "Payment: " + d["pay"], "Helvetica", 10, align="r")
    p.rect(20, 132, 380, 20, fill=(.9, .9, .9))
    p.t(26, 146, "Qty", "Helvetica-Bold", 9)
    p.t(60, 146, "Description", "Helvetica-Bold", 9)
    p.t(300, 146, "Unit Price", "Helvetica-Bold", 9, align="r")
    p.t(394, 146, "Amount", "Helvetica-Bold", 9, align="r")
    y = 172
    for q, name, u in d["items"]:
        p.t(26, y, str(q), "Helvetica", 10)
        p.t(60, y, name, "Helvetica", 10)
        p.t(300, y, fm(u), "Helvetica", 10, align="r")
        p.t(394, y, fm(q * u), "Helvetica", 10, align="r")
        y += 20
    p.line(20, y - 8, 400, y - 8)
    y += 8
    if d["tax"]:
        for lb, v in [("Subtotal", d["sub"]), (d["taxlbl"], d["tax"])]:
            p.t(230, y, lb, "Helvetica", 10)
            p.t(394, y, fm(v), "Helvetica", 10, align="r")
            y += 18
    p.rect(220, y, 180, 28, fill=c)
    p.t(230, y + 19, tl, "Helvetica-Bold", 11, WHITE)
    p.t(394, y + 19, fm(d["total"]), "Helvetica-Bold", 12, WHITE, "r")
    y += 60
    if d["until"]:
        p.rect(20, y, 380, 44, stroke=c)
        p.t(30, y + 19, f"{wl}: {D_(d['until'])}", "Helvetica-Bold", 10)
        p.t(30, y + 34, "Please keep this invoice for warranty claims.", "Helvetica", 8, GRAY)
        y += 60
    p.t(20, y + 10, "Goods once sold will not be exchanged without this invoice.", "Helvetica", 8, GRAY)
    return p


def rec_order(rng, d):
    D_, fm, tl, wl = rec_vars(rng, d, ["Order Total", "Total Paid", "Total Amount"])
    p = Page(420, 300 + 22 * len(d["items"]) + (36 if d["tax"] else 0) + (40 if d["until"] else 0))
    p.t(20, 40, d["merchant"], "Helvetica-Bold", 18)
    p.t(20, 68, "Order Confirmation", "Helvetica", 13, GRAY)
    p.t(20, 100, "Order No: " + d["no"], "Helvetica", 10)
    if d["date"]:
        p.t(20, 116, "Placed on: " + D_(d["date"]), "Helvetica", 10)
    p.t(20, 132, "Ship to: " + d["addr"], "Helvetica", 10)
    p.line(20, 146, 400, 146, GRAY)
    y = 170
    for q, name, u in d["items"]:
        p.t(20, y, f"{name} (x{q})", "Helvetica", 10)
        p.t(400, y, fm(q * u), "Helvetica", 10, align="r")
        y += 22
    p.line(20, y - 10, 400, y - 10, GRAY)
    y += 8
    for lb, v in ([("Subtotal", d["sub"]), (d["taxlbl"], d["tax"])] if d["tax"] else []):
        p.t(20, y, lb, "Helvetica", 10)
        p.t(400, y, fm(v), "Helvetica", 10, align="r")
        y += 18
    p.t(20, y, "Delivery", "Helvetica", 10)
    p.t(400, y, "Free", "Helvetica", 10, align="r")
    y += 28
    p.t(20, y, tl, "Helvetica-Bold", 13)
    p.t(400, y, fm(d["total"]), "Helvetica-Bold", 13, align="r")
    y += 24
    p.t(20, y, "Paid via " + d["pay"], "Helvetica", 9, GRAY)
    if d["until"]:
        y += 34
        p.t(20, y, f"{wl}: {D_(d['until'])}", "Helvetica-Bold", 10)
    p.t(20, y + 40, "Thank you for shopping with us!", "Helvetica-Oblique", 9, GRAY)
    return p


def receipts(rng, cats, fmts, dev):
    out, bi = [], 0
    for cat, fmt in zip(cats, fmts):
        group, merchants, items, elig = RCATS[cat]
        merchant = rng.choice(merchants)
        lines = []
        if cat == "fuel":
            u = rng.randint(*items[0][1:])
            lines = [(1, f"Petrol {u / 285:.1f} L", u)]
        elif cat == "grocery":
            lines = [(rng.randint(1, 3), n, rng.randint(lo, hi)) for n, lo, hi in rng.sample(items, rng.randint(3, 7))]
        elif cat in ("pharmacy", "restaurant", "books", "clothing"):
            lines = [(rng.randint(1, 2), n, rng.randint(lo, hi)) for n, lo, hi in rng.sample(items, rng.randint(1, min(3, len(items))))]
        else:
            n, lo, hi = rng.choice(items)
            lines = [(1, n, rng.randint(lo // 100, hi // 100) * 100)]
            if group == "electronics" and rng.random() < .4:
                n, lo, hi = rng.choice(ACC)
                lines.append((1, n, rng.randint(lo // 10, hi // 10) * 10))
        sub = sum(q * u for q, _, u in lines)
        taxlbl, rate = TAXES.get(group, (None, 0))
        tax = round(sub * rate, 2) if rate else 0
        total = round(sub + tax, 2)
        until = None
        if elig and rng.random() < .8:
            if dev:
                dt = date(2025, 1, 1) + timedelta(days=rng.randint(0, 364))
                until = add_months(dt, rng.choice([6, 12, 12, 24, 36]))
            else:
                b, bi = BUCKETS[bi % len(BUCKETS)], bi + 1
                while True:
                    dt = TODAY - timedelta(days=rng.randint(2, 700))
                    until = add_months(dt, rng.choice([6, 12, 12, 24, 24, 36]))
                    if in_bucket(b, until):
                        break
        else:
            dt = (date(2025, 1, 1) + timedelta(days=rng.randint(0, 364))) if dev else (
                TODAY - timedelta(days=rng.randint(2, 330 if rng.random() < .8 else 700)))
        d = dict(merchant=merchant, addr=ADDR[merchant], phone=digits(rng, 7), date=dt, items=lines, sub=sub, tax=tax,
                 taxlbl=taxlbl, total=total, until=until, no=digits(rng, 7), pay=rng.choice(["Cash", "Card"]),
                 time=f"{rng.randint(9, 21):02d}:{rng.randint(0, 59):02d}")
        f = {"merchant": merchant, "date": dt.isoformat(), "subtotal": float(sub) if tax else None, "tax": float(tax) if tax else None,
             "total_amount": float(total), "warranty_until": until.isoformat() if until else None}
        out.append(dict(type="receipt", fmt=fmt, fields=f, d=d, dt=dt, layout=rng.choice([rec_thermal, rec_invoice, rec_order]),
                        meta=dict(cat=cat, group=group, merchant=merchant, warranty=bool(until), items=[n for _, n, _ in lines])))
    return out


# ---------------------------------------------------------------- "other" documents
OTHER = {
    "notes_os": ("lecture_notes", "Operating Systems - Lecture 5: CPU Scheduling", [
        "Instructor: Dr. Farooq        Week 5", "", "Scheduling decides which ready process runs next on the CPU.", "",
        "1. First Come First Served (FCFS)", "   - simplest policy, non-preemptive", "   - convoy effect: short jobs wait behind one long job",
        "2. Shortest Job First (SJF)", "   - gives the minimum average waiting time", "   - problem: the burst length must be predicted",
        "3. Round Robin", "   - each process gets a time quantum, e.g. 4 ms, then goes to the back of the queue",
        "   - if the quantum is too small, context switch overhead dominates", "",
        "Example: P1 = 24 ms, P2 = 3 ms, P3 = 3 ms arrive together. With FCFS the average waiting time is 17 ms; with SJF it drops to 3 ms.",
        "", "To do: practise the Gantt chart questions at the end of the chapter."], dict(topic="cpu scheduling", first="First Come First Served (FCFS)")),
    "notes_db": ("lecture_notes", "Database Systems - Notes on Normalization", [
        "Why normalize? To remove redundancy and update anomalies.", "",
        "1NF: every column holds atomic values, no repeating groups.", "2NF: 1NF plus no partial dependency on part of a composite key.",
        "3NF: 2NF plus no transitive dependency between non-key columns.", "",
        "Functional dependency: A -> B means each value of A determines exactly one value of B.",
        "Example: in Orders(order_id, customer_id, customer_city) the city depends on customer_id, so move it to a Customers table.", "",
        "BCNF is a stricter version of 3NF: every determinant must be a candidate key.", "Remember to discuss the denormalization trade-off in the exam."],
        dict(topic="database normalization", first="1NF")),
    "notes_net": ("lecture_notes", "Computer Networks - TCP Congestion Control", [
        "TCP limits how fast a sender transmits using a congestion window (cwnd).", "",
        "Slow start: cwnd starts at 1 segment and doubles every round trip until it reaches ssthresh.",
        "Congestion avoidance: after ssthresh, cwnd grows by 1 segment per round trip (additive increase).",
        "On loss: ssthresh is halved (multiplicative decrease).", "Fast retransmit: three duplicate ACKs trigger a resend without waiting for a timeout.", "",
        "AIMD = additive increase, multiplicative decrease. It makes competing flows share a link fairly."], dict(topic="tcp congestion control", first="Slow start")),
    "notes_ml": ("lecture_notes", "Machine Learning - Gradient Descent", [
        "Goal: find parameters w that minimise the loss L(w).", "", "Update rule: w = w - alpha * gradient of L at w, where alpha is the learning rate.", "",
        "- learning rate too large: the loss bounces or diverges", "- learning rate too small: training is very slow",
        "- batch gradient descent uses all examples per step", "- stochastic gradient descent uses one example per step (noisy but fast)",
        "- mini-batch is the usual compromise, 32 to 256 examples", "", "Tip: plot the loss curve after every epoch to spot problems early."],
        dict(topic="gradient descent", first="Goal: find parameters w that minimise the loss")),
    "notes_la": ("lecture_notes", "Linear Algebra - Eigenvalues and Eigenvectors", [
        "Definition: A v = lambda v for a non-zero vector v. lambda is the eigenvalue, v the eigenvector.", "",
        "Find eigenvalues by solving det(A - lambda I) = 0 (the characteristic equation).", "Then solve (A - lambda I) v = 0 for each eigenvalue.", "",
        "Facts: the trace equals the sum of the eigenvalues; the determinant equals their product.", "A symmetric matrix has real eigenvalues and orthogonal eigenvectors."],
        dict(topic="eigenvalues", first="Definition")),
    "cover_sara": ("cover_letter", "Sara Khan", [
        "House 14, Street 7, Garden Town", "Lakeshore City", "", "Hiring Manager", "Brightwave Systems", "", "Dear Hiring Manager,", "",
        "I am writing to apply for the position of Junior Data Analyst at Brightwave Systems. I am a recent computer science graduate with hands-on experience in Python, SQL and building dashboards.", "",
        "During my university projects I cleaned survey data, built reports for a student society and presented the results to non-technical audiences. I enjoy turning messy data into clear answers and would like to learn from your analytics team.", "",
        "I have attached my CV and would welcome the chance to discuss how I can contribute. Thank you for your time.", "", "Yours sincerely,", "Sara Khan"],
        dict(company="Brightwave Systems", role="Junior Data Analyst")),
    "cover_hamza": ("cover_letter", "Hamza Khan", [
        "House 14, Street 7, Garden Town", "Lakeshore City", "", "The Internship Coordinator", "Orchard Software", "", "Dear Sir or Madam,", "",
        "I would like to apply for the Summer Intern position at Orchard Software. I am a college student who enjoys robotics and has built several small programs in Python.", "",
        "I am a quick learner, I work well in a team and I can commit to the full eight weeks of the programme. I would be grateful for an opportunity to learn from your engineers.", "",
        "Thank you for considering my application.", "", "Kind regards,", "Hamza Khan"], dict(company="Orchard Software", role="Summer Intern")),
    "cover_zainab": ("cover_letter", "Zainab Malik", [
        "Flat 3B, Block C, Orchard Heights", "Lakeshore City", "", "The Manager", "Cedar Books", "", "Dear Manager,", "",
        "I am applying for the Library Assistant job advertised at Cedar Books. I love reading, I am organised and I have two years of experience helping customers at a campus bookshop.", "",
        "I am available on weekends and evenings. Please find my CV attached.", "", "Sincerely,", "Zainab Malik"], dict(company="Cedar Books", role="Library Assistant")),
    "agenda_kick": ("agenda", "Project Kickoff Meeting - Agenda", [
        "Date: 2026-03-04     Time: 10:00 - 11:00", "Location: Meeting Room B", "Attendees: Sara, Ali, Hamza, Ayesha", "",
        "1. Welcome and introductions (10 min)", "2. Project goals and scope (15 min)", "3. Roles and responsibilities (15 min)",
        "4. Timeline and milestones (15 min)", "5. Questions and next steps (5 min)", "", "Please read the project brief before the meeting."],
        dict(title="Project Kickoff Meeting", first="Welcome and introductions")),
    "agenda_family": ("agenda", "Family Planning Meeting - Agenda", [
        "Sunday after lunch, living room", "", "1. Review of last month's household tasks", "2. Plan for the Eid gathering and guest list",
        "3. Who drives whom to school and work next week", "4. Any other business"], dict(title="Family Planning Meeting", first="Review of last month's household tasks")),
    "agenda_sync": ("agenda", "Weekly Team Sync - Agenda", [
        "Every Monday, 09:30 - 10:00", "", "1. Quick round: what did you finish last week?", "2. Blockers and requests for help", "3. Priorities for this week"],
        dict(title="Weekly Team Sync", first="Quick round: what did you finish last week?")),
    "recipe_biryani": ("recipe", "Chicken Biryani", [
        "Serves 6", "", "Ingredients: 1 kg chicken, 3 cups basmati rice, 2 onions, 1 cup yogurt, ginger-garlic paste, biryani masala, saffron milk, fresh mint.", "",
        "1. Marinate the chicken in yogurt, ginger-garlic paste and biryani masala for 2 hours.", "2. Fry the sliced onions until golden and set half aside.",
        "3. Cook the chicken with the remaining onions until tender.", "4. Boil the rice until 70 percent done and layer it over the chicken with mint and saffron milk.",
        "5. Cover tightly and steam on low heat for 20 minutes."], dict(dish="Chicken Biryani", marinate="2 hours")),
    "recipe_tandoori": ("recipe", "Tandoori Chicken (oven version)", [
        "Ingredients: 8 chicken legs, 1 cup yogurt, lemon juice, tandoori masala, mustard oil.", "",
        "1. Cut deep slits in the chicken and rub with lemon juice and salt.", "2. Mix yogurt, masala and mustard oil. Marinate the chicken overnight in the fridge.",
        "3. Bake at 220 C for 35 minutes, turning once.", "4. Serve with mint chutney and sliced onions."], dict(dish="Tandoori Chicken", marinate="overnight")),
    "recipe_brownie": ("recipe", "Chocolate Brownies", [
        "Ingredients: 200 g dark chocolate, 150 g butter, 3 eggs, 1 cup sugar, 3/4 cup flour, pinch of salt.", "",
        "1. Melt the chocolate and butter together and let it cool.", "2. Whisk the eggs and sugar, then fold in the chocolate mixture and flour.",
        "3. Bake at 175 C for 25 minutes. Cool before cutting into squares."], dict(dish="Chocolate Brownies", marinate=None)),
    "trip_murree": ("itinerary", "Trip to Murree - 3 Day Plan", [
        "Day 1: Drive from Lakeshore City, lunch on the way, check in at Pine View Resort.", "Day 2: Morning walk at Mall Road, chairlift in the afternoon, bonfire dinner.",
        "Day 3: Breakfast, short hike to the viewpoint, drive back before evening.", "", "Hotel: Pine View Resort", "Pack: warm jackets, power bank, first aid kit."],
        dict(dest="Murree", hotel="Pine View Resort")),
    "trip_hunza": ("itinerary", "Hunza Holiday Itinerary", [
        "Day 1: Fly to Gilgit and drive to Karimabad.", "Day 2: Visit Baltit Fort and Altit Fort.", "Day 3: Attabad Lake boat ride and Passu cones.",
        "Day 4: Return flight.", "", "Stay: Riverside Inn, Karimabad", "Remember to carry warm clothes and cash for the road."], dict(dest="Hunza", hotel="Riverside Inn")),
    "todo_hamza": ("todo", "Hamza - To Do This Week", [
        "[ ] finish chemistry lab report", "[ ] return library books", "[ ] practice python exercises", "[ ] charge the robot battery",
        "[ ] ask Sir about the science fair project", "[x] buy graph paper"], dict(person="Hamza")),
    "todo_ayesha": ("todo", "Things to do before Saturday", [
        "- call the plumber about the kitchen tap", "- prepare worksheets for class 4", "- pick up the dry cleaning", "- plan dinner for Sunday",
        "- water the plants", "- sort the old clothes for donation"], dict(person="Ayesha")),
    "time_sara": ("timetable", "Weekly Timetable - Sara", [
        "Mon  09:00 - 10:30  Calculus II        Room 204", "Mon  11:00 - 12:30  Physics Lab         Lab 3", "Tue  10:00 - 11:30  Technical Writing   Room 118",
        "Wed  09:00 - 10:30  Calculus II        Room 204", "Thu  14:00 - 16:00  Physics Lab         Lab 3", "Fri  10:00 - 11:00  Seminar            Hall A"], dict(person="Sara")),
    "time_hamza": ("timetable", "Class Timetable - Hamza", [
        "Mon  08:00  Mathematics", "Mon  09:00  Chemistry", "Tue  08:00  English", "Tue  10:00  Physics", "Wed  08:00  Mathematics",
        "Thu  09:00  Computer Lab", "Fri  08:00  Islamiat", "Fri  09:30  Sports"], dict(person="Hamza")),
    "letter_a": ("letter", "Letter to Aunt Hira", [
        "Dear Aunt Hira,", "", "I hope you are well. The weather here has turned cold and Hamza has been busy with his exams. We all miss you and hope you can visit during the winter break.",
        "Please send my salaam to everyone at home.", "", "With love,", "Nusrat"], dict(person="Nusrat")),
    "letter_b": ("letter", "Note to the neighbours", [
        "Dear neighbours,", "", "We are having a small family gathering this Saturday evening and there may be some extra cars on the street. Sorry in advance for any inconvenience.",
        "You are welcome to join us for tea.", "", "Regards,", "The Khan family"], dict(person="Khan family")),
}
TEST_OTHER = ["notes_os", "notes_db", "notes_net", "notes_ml", "cover_sara", "cover_hamza", "agenda_kick", "agenda_family",
              "recipe_biryani", "recipe_tandoori", "trip_murree", "trip_hunza", "todo_hamza", "todo_ayesha", "time_sara",
              "time_hamza", "letter_a", "letter_b"]
DEV_OTHER = ["notes_la", "cover_zainab", "recipe_brownie", "agenda_sync"]


def other_doc(rng, d):
    font = d["font"]
    p = Page(595, 842)
    p.t(50, 70, d["title"], font.replace("-Roman", "") + "-Bold", 16)
    y, w = 108, {"Helvetica": 86, "Times-Roman": 96, "Courier": 70}[font]
    for ln in d["lines"]:
        for part in (textwrap.wrap(ln, w, subsequent_indent="   ") if ln else [""]):
            if part:
                p.t(50, y, part, font, 11)
            y += 17
    return p


def others(rng, keys, fmts):
    out = []
    for k, fmt in zip(keys, fmts):
        sub, title, lines, meta = OTHER[k]
        d = dict(title=title, lines=lines, font=rng.choice(["Helvetica", "Times-Roman", "Courier"]))
        out.append(dict(type="other", fmt=fmt, fields={}, d=d, dt=TODAY - timedelta(days=rng.randint(5, 600)), layout=other_doc,
                        meta=dict(sub=sub, key=k, **meta)))
    return out


# ---------------------------------------------------------------- rendering (scans and photos)
DESKS = [(120, 90, 60), (70, 70, 75), (190, 180, 165), (40, 60, 50), (200, 200, 205)]


def raster(pdf, dpi):
    pix = pymupdf.open("pdf", pdf)[0].get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def degrade(img, rng, photo):
    """Small rotation, uneven light, brightness, blur, noise and JPEG compression, like a phone photo or a scanner."""
    w, h = img.size
    bg = rng.choice(DESKS) if photo else (255, 255, 255)
    if photo:
        pad = int(.07 * max(w, h))
        c = Image.new("RGB", (w + 2 * pad, h + 2 * pad), bg)
        c.paste(img, (pad, pad))
        img = c
    if photo and max(img.size) > 1500:
        img.thumbnail((1500, 1500), Image.LANCZOS)
    img = img.rotate(rng.uniform(-4, 4), expand=True, resample=Image.BILINEAR, fillcolor=bg)
    k = .3 if photo else .08
    g = Image.linear_gradient("L").rotate(rng.choice([0, 90, 180, 270])).resize(img.size).point(lambda v: 255 - int(v * k))
    img = ImageChops.multiply(img, g.convert("RGB"))
    img = ImageEnhance.Contrast(ImageEnhance.Brightness(img).enhance(rng.uniform(.85, 1.1))).enhance(rng.uniform(.9, 1.1))
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(.5, 1.0) if photo else rng.uniform(.3, .6)))
    a = rng.uniform(.12, .22)
    noise = Image.frombytes("L", img.size, rng.randbytes(img.width * img.height)).point(lambda v: 128 + int((v - 128) * a))
    img = ImageChops.add(img, noise.convert("RGB"), 1.0, -128)
    if not photo and rng.random() < .5:
        img = img.convert("L")
    q = rng.randint(35, 70) if photo else rng.randint(50, 80)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    return buf.getvalue(), img.size


def render(s, rr, path):
    """Draw spec s into path. Returns the exact text drawn."""
    p = s["layout"](rr, s["d"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if s["fmt"] == "pdf_text":
        path.write_bytes(p.pdf())
        return p.text()
    pdf = p.pdf()
    if s["fmt"] == "pdf_scan":
        dpi = 150
        img = raster(pdf, dpi)
        if s["type"] == "id_card":  # a card photocopied on an A4 sheet
            img.thumbnail((1050, 1050))
            sheet = Image.new("RGB", (1240, 1754), (255, 255, 255))
            sheet.paste(img, (rr.randint(60, 250), rr.randint(80, 400)))
            img = sheet
        jpg, (w, h) = degrade(img, rr, photo=False)
        doc = pymupdf.open()
        doc.new_page(width=w * 72 / dpi, height=h * 72 / dpi).insert_image(pymupdf.Rect(0, 0, w * 72 / dpi, h * 72 / dpi), stream=jpg)
        doc.set_metadata({})
        path.write_bytes(doc.tobytes(deflate=True, no_new_id=True))
        return p.text()
    jpg, _ = degrade(raster(pdf, rr.randint(130, 170)), rr, photo=True)
    if path.suffix == ".png":
        Image.open(io.BytesIO(jpg)).save(path, "PNG")
    else:
        path.write_bytes(jpg)
    return p.text()


def file_path(rng, s, used):
    fmt, dt = s["fmt"], min(max(s["dt"], date(2019, 1, 1)), TODAY)  # file names look like real device names
    while True:
        n, k = rng.randint(1, 9999), rng.randint(1, 30)
        if fmt == "image":
            folder = rng.choices(["WhatsApp Images", "Downloads", "Desktop", "Documents/old"], [4, 2, 2, 2])[0]
            if folder == "WhatsApp Images":
                name = f"IMG-{(dt + timedelta(days=rng.randint(0, 3))).strftime('%Y%m%d')}-WA{n % 1000:04d}.jpg"
            else:
                name = rng.choice([f"IMG_{n:04d}.jpg", f"PXL_{dt.strftime('%Y%m%d')}_{rng.randint(80000, 215959)}.jpg",
                                   f"photo_{k}.png", f"Screenshot {dt.isoformat()} at {rng.randint(8, 22)}.{rng.randint(10, 59)}.{rng.randint(10, 59)}.png"])
        else:
            folder = rng.choice(["Downloads", "Documents/old", "Desktop"])
            if fmt == "pdf_scan":
                name = rng.choice([f"scan_{n:04d}.pdf", f"Scan {k}.pdf", f"Document ({k}).pdf",
                                   f"CamScanner {dt.strftime('%m-%d-%Y')} {rng.randint(8, 21)}.{rng.randint(10, 59)}.pdf"])
            else:
                name = rng.choice([f"Document ({k}).pdf", f"download ({k}).pdf", f"{rng.getrandbits(32):08x}.pdf",
                                   f"Print_{dt.isoformat()}.pdf", f"file_{n}.pdf"])
        if f"{folder}/{name}" not in used:
            used.add(f"{folder}/{name}")
            return f"{folder}/{name}"


def alloc(rng, **counts):
    out = [k for k, n in counts.items() for _ in range(n)]
    rng.shuffle(out)
    return out


def build(split, seed):
    rng = random.Random(f"{seed}-{split}")
    if split == "test":
        rows, people = TEST_IDS, TEST_PEOPLE
        s = ids(rng, rows, people, alloc(rng, image=11, pdf_scan=4))
        s += bills(rng, split, 2026, lambda p: range(1, 13), alloc(rng, pdf_text=26, pdf_scan=10))
        s += certs(rng, cert_rows(rng, TEST_CERTS), alloc(rng, pdf_text=16, pdf_scan=10))
        cats = [c for c, n in TEST_RCATS.items() for _ in range(n)]
        rng.shuffle(cats)
        s += receipts(rng, cats, alloc(rng, image=56, pdf_text=20), dev=False)
        s += others(rng, TEST_OTHER, alloc(rng, pdf_text=14, pdf_scan=2, image=2))
    else:
        s = ids(rng, DEV_IDS, DEV_PEOPLE, alloc(rng, image=5, pdf_scan=2))
        s += bills(rng, split, 2025, lambda p: sorted(rng.sample(range(1, 13), 2)), alloc(rng, pdf_text=4, pdf_scan=2))
        s += certs(rng, cert_rows(rng, DEV_CERTS), alloc(rng, pdf_text=4, pdf_scan=3))
        s += receipts(rng, DEV_RCATS, alloc(rng, image=11, pdf_text=5), dev=True)
        s += others(rng, DEV_OTHER, alloc(rng, pdf_text=3, image=1))
    rng.shuffle(s)
    if split == "test":
        incomplete(s)
    near = [(i, dict(sp, fmt="pdf_text" if sp["fmt"] != "pdf_text" else "image")) for i, sp in enumerate(s) if split == "test" and near_dup(sp)]
    used, recs = set(), []
    for i, sp in enumerate(s):
        rel = file_path(rng, sp, used)
        text = render(sp, random.Random(f"{seed}-{split}-doc{i}"), OUT / split / rel)
        recs.append(dict(file=f"{split}/{rel}", split=split, type=sp["type"], format=sp["fmt"], fields=sp["fields"],
                         text=text, meta=sp["meta"], duplicate_of=None, duplicate_kind=None))
    for i, sp in near:  # the same document again in another format; same seed, so the same printed text
        rel = file_path(rng, sp, used)
        text = render(sp, random.Random(f"{seed}-{split}-doc{i}"), OUT / split / rel)
        recs.append(dict(recs[i], file=f"{split}/{rel}", format=sp["fmt"], text=text, duplicate_of=recs[i]["file"], duplicate_kind="near"))
    for r in [r for r in recs if split == "test" and not r["duplicate_of"] and exact_copy(r)]:
        rel = f"Work/Backup/{Path(r['file']).name}"
        while rel in used:  # two originals can share a file name
            rel = rel.replace("Backup/", "Backup/Copy of ", 1)
        used.add(rel)
        (OUT / split / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(OUT / r["file"], OUT / split / rel)
        recs.append(dict(r, file=f"{split}/{rel}", duplicate_of=r["file"], duplicate_kind="exact"))
    return recs


def incomplete(specs):
    """A few test records with a field that is not printed; the label is null."""
    def blank(sp, key, field):
        sp["d"] = dict(sp["d"], **{key: None})
        sp["fields"] = dict(sp["fields"], **{field: None})
    groceries = 0
    for sp in specs:
        m = sp["meta"]
        if sp["type"] == "utility_bill" and (m["cat"], m["month"]) == ("gas", 9):
            blank(sp, "due", "due_date")
        elif sp["type"] == "id_card" and (m["person"], m["kind"]) == ("Ali Raza Khan", "staff"):
            blank(sp, "expiry", "expiry_date")
        elif sp["type"] == "receipt" and m["cat"] == "grocery" and groceries < 2:
            groceries += 1
            blank(sp, "date", "date")


def near_dup(sp):
    m = sp["meta"]
    return {"utility_bill": lambda: (m["cat"], m["month"]) in {("electricity", 3), ("gas", 7), ("internet", 2)},
            "receipt": lambda: m["cat"] in ("laptop", "phone") and NEAR_PICK.setdefault(m["cat"], m) is m,
            "id_card": lambda: (m["person"], m["kind"]) == ("Hamza Khan", "national"),
            "certificate": lambda: (m["person"], m["title"]) == ("Sara Khan", "Python Programming")}.get(sp["type"], lambda: False)()


NEAR_PICK = {}  # the first laptop and phone receipt seen get a second copy


def exact_copy(r):
    m = r["meta"]
    return {"utility_bill": lambda: (m["cat"], m["month"]) in {("electricity", 5), ("internet", 8)},
            "receipt": lambda: m["cat"] in ("fuel", "pharmacy") and COPY_PICK.setdefault(m["cat"], m) is m,
            "id_card": lambda: (m["person"], m["kind"]) == ("Sara Khan", "licence"),
            "certificate": lambda: m["title"] == "Master of Business Administration"}.get(r["type"], lambda: False)()


COPY_PICK = {}


# ---------------------------------------------------------------- draft test sets
def draft_eval(T, seed):
    rng = random.Random(f"{seed}-eval")
    R = lambda f: sorted(r["file"] for r in T if f(r) and r["duplicate_kind"] != "exact")  # an exact copy is the same document
    of = lambda t: [r for r in T if r["type"] == t]
    MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    PW = {"electricity": ["electricity bill", "power bill", "electric supply charges", "light bill"],
          "gas": ["gas bill", "natural gas payment", "gas utility statement"], "internet": ["internet bill", "broadband invoice", "wifi subscription bill"]}
    bl = lambda cat, m=None: R(lambda r: r["type"] == "utility_bill" and r["meta"]["cat"] == cat and (m is None or r["meta"]["month"] == m))
    S = []
    add = lambda q, r: S.append({"q": q, "relevant": r}) if r else None
    # bills
    for cat, m in [("electricity", 3), ("gas", 1), ("internet", 8), ("electricity", 7), ("gas", 12), ("internet", 5), ("electricity", 10), ("gas", 6)]:
        add(rng.choice([f"{rng.choice(PW[cat])} for {MONTH[m - 1]}", f"{MONTH[m - 1]} {rng.choice(PW[cat])}", f"{rng.choice(PW[cat])} {MONTH[m - 1]} 2026"]), bl(cat, m))
    for cat, q in [("electricity", "all my electricity bills"), ("gas", "gas connection statements"), ("internet", "wifi subscription invoices")]:
        add(q, bl(cat))
    for prov in PROVIDERS:
        add(prov["name"] + " statement", bl(prov["cat"]))
    acct = [r for r in T if r["meta"].get("cat") == "electricity"][0]["fields"]["account_number"]
    add("bill for account " + acct, bl("electricity"))
    # ID cards
    kindw = {"national": "national identity card", "licence": "driving licence", "student": "student card", "staff": "employee ID card"}
    idc = lambda who=None, kind=None: R(lambda r: r["type"] == "id_card" and (who is None or r["meta"]["person"] == who) and (kind is None or r["meta"]["kind"] == kind))
    for who, kind, q in [("Ali Raza Khan", "national", "Ali's national identity card"), ("Nusrat Khan", None, "Nusrat's identity card"),
                         ("Ayesha Khan", "licence", "Ayesha's driving license"), ("Sara Khan", "licence", "Sara's driving licence"),
                         ("Hamza Khan", "student", "Hamza's student card"), ("Sara Khan", "student", "Sara's university ID"),
                         ("Ali Raza Khan", "staff", "Ali's work ID badge"), ("Ayesha Khan", "staff", "Ayesha's school employee card"),
                         (None, "licence", "driving licences"), (None, "national", "national ID cards")]:
        add(q, idc(who, kind))
    # certificates
    cc = lambda f: R(lambda r: r["type"] == "certificate" and f(r["meta"]))
    for q, f in [("Sara's degree", lambda m: m["person"] == "Sara Khan" and m["kind"] == "degree"),
                 ("Ali's MBA", lambda m: m["person"] == "Ali Raza Khan" and m["kind"] == "degree"),
                 ("Ayesha's education degree", lambda m: m["person"] == "Ayesha Khan" and m["kind"] == "degree"),
                 ("Hamza's school certificate", lambda m: m["person"] == "Hamza Khan" and m["kind"] == "school"),
                 ("machine learning course certificate", lambda m: m["title"] == "Introduction to Machine Learning"),
                 ("first aid training certificate", lambda m: m["title"] == "First Aid and CPR"),
                 ("python course completion", lambda m: m["title"] == "Python Programming"),
                 ("project management certificate", lambda m: m["title"] == "Project Management Fundamentals"),
                 ("Nusrat's tailoring certificate", lambda m: m["title"] == "Sewing and Tailoring Course"),
                 ("all of Sara's certificates", lambda m: m["person"] == "Sara Khan"),
                 ("certificates from Summit Online Learning", lambda m: m["inst"] == "Summit Online Learning"),
                 ("diploma issued by Lifeline Training Center", lambda m: m["inst"] == "Lifeline Training Center")]:
        add(q, cc(f))
    # receipts
    rc = lambda f: R(lambda r: r["type"] == "receipt" and f(r["meta"]))
    for q, f in [("laptop receipt with warranty", lambda m: m["cat"] == "laptop" and m["warranty"]),
                 ("phone purchase receipt", lambda m: m["cat"] == "phone"),
                 ("microwave oven warranty", lambda m: m["cat"] == "microwave" and m["warranty"]),
                 ("receipts that include warranty information", lambda m: m["warranty"]),
                 ("grocery shopping bills", lambda m: m["cat"] == "grocery"),
                 ("medicine purchase from the pharmacy", lambda m: m["cat"] == "pharmacy"),
                 ("petrol fuel receipt", lambda m: m["cat"] == "fuel"),
                 ("headphones I bought", lambda m: m["cat"] == "earbuds"),
                 ("fridge purchase invoice", lambda m: m["cat"] == "fridge"),
                 ("clothes shopping receipt", lambda m: m["cat"] == "clothing"),
                 ("restaurant dinner bill", lambda m: m["cat"] == "restaurant"),
                 ("book store purchase", lambda m: m["cat"] == "books"),
                 ("everything I bought at Metro Electronics Mart", lambda m: m["merchant"] == "Metro Electronics Mart"),
                 ("computer monitor receipt", lambda m: m["cat"] == "monitor"),
                 ("air fryer purchase", lambda m: m["cat"] == "airfryer"),
                 ("air conditioner invoice", lambda m: m["cat"] == "ac")]:
        add(q, rc(f))
    # other
    oc = lambda f: R(lambda r: r["type"] == "other" and f(r["meta"]))
    for q, f in [("lecture notes on CPU scheduling", lambda m: m.get("topic") == "cpu scheduling"),
                 ("notes about database normalization", lambda m: m.get("topic") == "database normalization"),
                 ("TCP congestion lecture", lambda m: m.get("topic") == "tcp congestion control"),
                 ("gradient descent notes", lambda m: m.get("topic") == "gradient descent"),
                 ("cover letter for a job application", lambda m: m["sub"] == "cover_letter"),
                 ("meeting agenda", lambda m: m["sub"] == "agenda"),
                 ("biryani recipe", lambda m: m.get("dish") == "Chicken Biryani"),
                 ("recipes with a marinade", lambda m: m["sub"] == "recipe"),
                 ("holiday trip plan to Murree", lambda m: m.get("dest") == "Murree"),
                 ("travel itinerary", lambda m: m["sub"] == "itinerary"),
                 ("my weekly to-do list", lambda m: m["sub"] == "todo"),
                 ("class timetable", lambda m: m["sub"] == "timetable")]:
        add(q, oc(f))
    assert len(S) >= 60, len(S)
    keep = sorted(rng.sample(range(len(S)), 60))
    search = [S[i] for i in keep]

    # ---- ask: every gold answer is computed here by code from the labels. Each document counts once: exact copies
    # and near-duplicates are folded into their original; both sides of a conflict are kept.
    A = []
    U = [r for r in T if not r["duplicate_of"]]
    copies = {}
    for r in T:
        if r["duplicate_of"]:
            copies.setdefault(r["duplicate_of"], []).append(r["file"])
    srcs = lambda rs: sorted({f for r in rs for f in [r["file"], *copies.get(r["file"], [])]})  # any copy is a right source

    def ask(q, route, ans, typ, rs, cat):
        A.append(dict(q=q, route=route, answer=ans, answer_type=typ, sources=srcs(rs), category=cat))
    of = lambda t: [r for r in U if r["type"] == t]
    F = lambda r, k: r["fields"][k]
    first = lambda who: who.split()[0]
    day = lambda iso: (lambda d: f"{d.day} {MONTH[d.month - 1]} {d.year}")(date.fromisoformat(iso))
    bills_ = of("utility_bill")
    B = lambda cat=None, m=None, lo=1, hi=12: [r for r in bills_ if (cat is None or r["meta"]["cat"] == cat) and lo <= r["meta"]["month"] <= hi and (m is None or r["meta"]["month"] == m)]
    spend = lambda rs: round(sum(F(r, "current_charges") for r in rs), 2)  # never amount_due: it repeats unpaid arrears
    due = lambda r: F(r, "amount_due")
    X, S1, EW, DU, CF, SE, UN = "cross_doc_total", "single_fact", "expiring_window", "duplicates", "conflict", "semantic", "unanswerable"

    # the original 40 facts questions, gold answers under the new rules
    for cat in PW:
        ask(f"How much did we pay for {cat} in 2026?", "facts", num(spend(B(cat))), "number", B(cat), X)
    ask("What is the total of all our utility bills for 2026?", "facts", num(spend(B())), "number", B(), X)
    for cat, lo, hi, q in [("electricity", 6, 8, "How much did we pay for electricity from June to August 2026?"),
                           ("gas", 1, 3, "What was the total gas bill for January to March 2026?")]:
        ask(q, "facts", num(spend(B(cat, None, lo, hi))), "number", B(cat, None, lo, hi), X)
    mx = max(B("electricity"), key=due)
    ask("What was our highest electricity bill in 2026?", "facts", num(due(mx)), "number", [mx], X)
    mn = min(B("electricity"), key=due)
    ask("What was the lowest electricity bill amount in 2026?", "facts", num(due(mn)), "number", [mn], X)
    mg = max(B("gas"), key=due)
    ask("In which month was the gas bill the highest in 2026?", "facts", F(mg, "billing_month"), "text", [mg], X)
    mg = min(B("gas"), key=due)
    ask("What was the smallest gas bill in 2026?", "facts", num(due(mg)), "number", [mg], X)
    for cat, m in [("electricity", 3), ("gas", 2), ("internet", 9), ("electricity", 7)]:
        r = B(cat, m)[0]
        ask(f"How much was the {cat} bill for {MONTH[m - 1]} 2026?", "facts", num(due(r)), "number", [r], S1)
    for cat, m in [("gas", 6), ("electricity", 4), ("internet", 11)]:
        r = B(cat, m)[0]
        ask(f"What is the due date of the {cat} bill for {MONTH[m - 1]} 2026?", "facts", F(r, "due_date"), "date", [r], S1)
    rcs = of("receipt")
    y = lambda r: (F(r, "date") or "")[:4]
    r26, r25 = [r for r in rcs if y(r) == "2026"], [r for r in rcs if y(r) == "2025"]
    ask("How many receipts do I have from 2026?", "facts", str(len(r26)), "number", r26, X)
    ask("How many receipts do I have from 2025?", "facts", str(len(r25)), "number", r25, X)
    wr = [r for r in rcs if F(r, "warranty_until")]
    ask("How many receipts include a warranty?", "facts", str(len(wr)), "number", wr, X)
    ask("How many ID cards do I have?", "facts", str(len(of("id_card"))), "number", of("id_card"), X)
    ask("How many certificates do I have?", "facts", str(len(of("certificate"))), "number", of("certificate"), X)
    ask("How many utility bills do we have for 2026?", "facts", str(len(bills_)), "number", bills_, DU)
    tot = lambda rs: num(round(sum(F(r, "total_amount") for r in rs), 2))
    ask("How much did I spend in total on receipts in 2026?", "facts", tot(r26), "number", r26, X)
    for m in ("FreshBasket Grocers", "CityFuel Station"):
        rs = [r for r in r26 if F(r, "merchant") == m]
        ask(f"How much did I spend at {m} in 2026?", "facts", tot(rs), "number", rs, X)
    mx = max(rcs, key=lambda r: F(r, "total_amount"))
    ask("What was my most expensive purchase?", "facts", num(F(mx, "total_amount")), "number", [mx], X)
    mn = min(rcs, key=lambda r: F(r, "total_amount"))
    ask("What was the cheapest receipt amount?", "facts", num(F(mn, "total_amount")), "number", [mn], X)
    idl = of("id_card")
    ex = lambda r: F(r, "expiry_date")
    live = sorted((r for r in idl if ex(r) and ex(r) >= TODAY.isoformat()), key=ex)
    T_ = " Today is 2026-11-22."
    ask("Which ID card expires next?" + T_, "facts", ex(live[0]), "date", [live[0]], EW)
    ask("Whose ID card expires next?" + T_, "facts", F(live[0], "name"), "text", [live[0]], EW)
    soon = [r for r in live if ex(r) <= WINDOW.isoformat()]
    ask("Which people have an ID card expiring within the next 90 days?" + T_, "facts", ", ".join(sorted({F(r, "name") for r in soon})), "list", soon, EW)
    gone = [r for r in idl if ex(r) and ex(r) < TODAY.isoformat()]
    ask("How many ID cards have already expired?" + T_, "facts", str(len(gone)), "number", gone, EW)
    one = lambda who, kind: [r for r in idl if r["meta"]["person"] == who and r["meta"]["kind"] == kind]
    r = [r for r in idl if F(r, "name") == "Nusrat Khan"]
    assert len(r) == 1
    ask("When does Nusrat's ID card expire?", "facts", ex(r[0]), "date", r, S1)
    r = one("Hamza Khan", "national")
    ask("What is Hamza's national ID number?", "facts", F(r[0], "id_number"), "text", r, S1)
    r = one("Ayesha Khan", "licence")
    ask("When does Ayesha's driving licence expire?", "facts", ex(r[0]), "date", r, S1)
    r = [r for r in idl if F(r, "name") == "Ali Raza Khan"]
    ask("What is Ali's date of birth?", "facts", F(r[0], "date_of_birth"), "date", r, S1)
    r = one("Sara Khan", "licence")
    ask("When was Sara's driving licence issued?", "facts", F(r[0], "issue_date"), "date", r, S1)
    cl = of("certificate")
    sc = [r for r in cl if F(r, "holder_name") == "Sara Khan"]
    ask("How many certificates does Sara have?", "facts", str(len(sc)), "number", sc, X)
    r = [r for r in cl if F(r, "title") == "Master of Business Administration"]
    ask("When was Ali's Master of Business Administration issued?", "facts", F(r[0], "issue_date"), "date", r, S1)
    nw = min((r for r in wr if F(r, "warranty_until") > TODAY.isoformat()), key=lambda r: F(r, "warranty_until"))
    ask("Which receipt warranty expires next?" + T_, "facts", F(nw, "warranty_until"), "date", [nw], EW)
    assert len(A) == 40, len(A)

    # ---- new: single facts, one document each
    for cat, m in [("electricity", 1), ("electricity", 6), ("electricity", 8), ("electricity", 11), ("gas", 3), ("gas", 5),
                   ("gas", 10), ("gas", 12), ("internet", 1), ("internet", 4), ("internet", 7), ("internet", 12)]:
        r = B(cat, m)[0]
        ask(f"What was the total payable on the {cat} bill for {MONTH[m - 1]} 2026?", "facts", num(due(r)), "number", [r], S1)
    for cat, m in [("electricity", 2), ("electricity", 9), ("gas", 4), ("gas", 11), ("internet", 3), ("internet", 10)]:
        r = B(cat, m)[0]
        ask(f"What were the current charges on the {cat} bill for {MONTH[m - 1]} 2026?", "facts", num(F(r, "current_charges")), "number", [r], S1)
    for cat, m in [("electricity", 5), ("electricity", 12), ("gas", 1), ("gas", 8), ("internet", 6), ("internet", 9)]:
        r = B(cat, m)[0]
        ask(f"When is the {cat} bill for {MONTH[m - 1]} 2026 due?", "facts", F(r, "due_date"), "date", [r], S1)
    for who in ("Ayesha Khan", "Sara Khan", "Hamza Khan", "Nusrat Khan"):
        r = [x for x in idl if F(x, "name") == who]
        ask(f"What is {first(who)}'s date of birth?", "facts", F(r[0], "date_of_birth"), "date", r, S1)
    KW = {"national": "national identity card", "licence": "driving licence", "student": "student card", "staff": "employee card"}
    for who, kind in [("Ali Raza Khan", "licence"), ("Sara Khan", "student"), ("Ayesha Khan", "staff"), ("Sara Khan", "national"),
                      ("Hamza Khan", "national")]:
        r = one(who, kind)
        assert len(r) == 1 and ex(r[0]), (who, kind)
        ask(f"When does {first(who)}'s {KW[kind]} expire?", "facts", ex(r[0]), "date", r, S1)
    for who, title in [("Sara Khan", "Python Programming"), ("Sara Khan", "First Aid and CPR"), ("Ayesha Khan", "Classroom Management"),
                       ("Hamza Khan", "Robotics Workshop"), ("Nusrat Khan", "Basic Computer Literacy"), ("Ali Raza Khan", "Excel for Business")]:
        r = [x for x in cl if F(x, "holder_name") == who and F(x, "title") == title]
        ask(f"When was {first(who)}'s {title} certificate issued?", "facts", F(r[0], "issue_date"), "date", r, S1)
    alone = [r for r in rcs if F(r, "date") and sum(F(x, "merchant") == F(r, "merchant") and F(x, "date") == F(r, "date") for x in rcs) == 1]
    for r in alone[::max(1, len(alone) // 6)][:6]:
        ask(f"How much did I pay at {F(r, 'merchant')} on {day(F(r, 'date'))}?", "facts", num(F(r, "total_amount")), "number", [r], S1)

    # ---- new: cross-document totals (duplicates present in the folder), tax and outstanding balance
    for cat in PW:
        for lo, hi in [(1, 3), (4, 6), (7, 9), (10, 12)]:
            rs = B(cat, None, lo, hi)
            ask(f"How much did we pay for {cat} from {MONTH[lo - 1]} to {MONTH[hi - 1]} 2026?", "facts", num(spend(rs)), "number", rs, X)
    tax = lambda rs: round(sum(F(r, "tax") or 0 for r in rs), 2)
    for cat in PW:
        ask(f"How much tax did we pay on {cat} bills in 2026?", "facts", num(tax(B(cat))), "number", B(cat), X)
    ask("How much tax did we pay on all utility bills in 2026?", "facts", num(tax(B())), "number", B(), X)
    latest = {cat: max(B(cat), key=lambda r: F(r, "billing_month")) for cat in PW}
    owed = lambda r: 0.0 if F(r, "payment_status") == "paid" else due(r)  # the latest bill already carries older arrears
    for cat, r in latest.items():
        ask(f"How much do we still owe on the {cat} bill?", "facts", num(owed(r)), "number", [r], X)
    ask("What is our total outstanding balance on utility bills?", "facts", num(round(sum(owed(r) for r in latest.values()), 2)), "number",
        [r for r in latest.values() if owed(r)], X)
    for m in sorted({F(r, "merchant") for r in r26} - {"FreshBasket Grocers", "CityFuel Station"}):
        rs = [r for r in r26 if F(r, "merchant") == m]
        if len(rs) >= 2:
            ask(f"How much did I spend at {m} in 2026?", "facts", tot(rs), "number", rs, X)

    # ---- new: everything expiring in a window (ID expiry and warranties; bill due dates are not expiries)
    exp = [(r, F(r, f)) for r in U for f in ("expiry_date", "warranty_until") if r["fields"].get(f)]
    for lo, hi in [(D(2026, 12, 1), D(2027, 1, 31)), (D(2027, 1, 1), D(2027, 6, 30)), (D(2027, 7, 1), D(2027, 12, 31)),
                   (D(2026, 6, 1), D(2026, 11, 30)), (D(2028, 1, 1), D(2028, 12, 31)), (D(2025, 1, 1), D(2025, 6, 30))]:
        hit = sorted((x for x in exp if lo.isoformat() <= x[1] <= hi.isoformat()), key=lambda x: x[1])
        if hit:
            ask(f"What expires between {day(lo.isoformat())} and {day(hi.isoformat())}?", "facts", ";".join(v for _, v in hit), "list",
                [r for r, _ in hit], EW)

    # ---- new: counts that duplicates would inflate
    for cat in PW:
        ask(f"How many {cat} bills do we have for 2026?", "facts", str(len(B(cat))), "number", B(cat), DU)
    me = [r for r in rcs if F(r, "merchant") == ELEC]
    ask("How many receipts from Metro Electronics Mart do I have?", "facts", str(len(me)), "number", me, DU)
    nat = [r for r in idl if r["meta"]["kind"] == "national"]
    ask("How many national identity cards do we have?", "facts", str(len(nat)), "number", nat, DU)

    # ---- new: conflicts, where the answer must show both values
    for who in ("Ali Raza Khan", "Ayesha Khan"):
        r = one(who, "national")
        assert len(r) == 2 and F(r[0], "id_number") == F(r[1], "id_number"), who
        both = ";".join(sorted(ex(x) for x in r))
        ask(f"When does {first(who)}'s national identity card expire?", "facts", both, "list", r, CF)
        ask(f"What is the expiry date on ID card {F(r[0], 'id_number')}?", "facts", both, "list", r, CF)

    # ---- search route: what documents say beyond the facts table
    def sd(q, ans, typ, rs):
        ask(q, "search", ans, typ, rs, SE)
    f1 = lambda t, f: [r for r in of(t) if f(r)]
    r = f1("certificate", lambda r: r["meta"]["person"] == "Sara Khan" and r["meta"]["kind"] == "degree")
    sd("What institution issued Sara's degree?", F(r[0], "institution"), "text", r)
    sd("When was Sara's degree issued?", F(r[0], "issue_date"), "date", r)
    for cat, w in [("gas", "gas bill"), ("internet", "internet bill"), ("electricity", "electricity bill")]:
        sd(f"What is the account number on the {w}?", F(B(cat)[0], "account_number"), "text", B(cat))
    r = f1("certificate", lambda r: r["meta"]["kind"] == "school")
    sd("What is the title of Hamza's school certificate?", F(r[0], "title"), "text", r)
    r = f1("receipt", lambda r: r["meta"]["cat"] == "laptop")
    sd("Where did I buy the laptop?", F(r[0], "merchant"), "text", r)
    r = f1("id_card", lambda r: F(r, "name") == "Nusrat Khan")
    sd("What is the ID number on Nusrat's identity card?", F(r[0], "id_number"), "text", r)
    r = f1("certificate", lambda r: r["meta"]["person"] == "Ali Raza Khan" and r["meta"]["kind"] == "degree")
    sd("Which institution awarded Ali's MBA?", F(r[0], "institution"), "text", r)
    r = f1("id_card", lambda r: r["meta"]["person"] == "Ali Raza Khan" and r["meta"]["kind"] == "staff")
    sd("Which company does Ali work for?", r[0]["meta"]["inst"], "text", r)
    r = f1("other", lambda r: r["meta"].get("key") == "cover_sara")
    sd("Which company is Sara's cover letter addressed to?", r[0]["meta"]["company"], "text", r)
    r = f1("other", lambda r: r["meta"].get("key") == "recipe_biryani")
    sd("How long should the chicken marinate for the biryani?", "2 hours", "text", r)
    r = f1("other", lambda r: r["meta"].get("key") == "agenda_kick")
    sd("What is the first item on the project kickoff meeting agenda?", r[0]["meta"]["first"], "text", r)
    r = f1("other", lambda r: r["meta"].get("key") == "trip_murree")
    sd("Which hotel are we staying at in Murree?", "Pine View Resort", "text", r)
    r = f1("other", lambda r: r["meta"].get("key") == "notes_net")
    sd("What does TCP do when it sees three duplicate ACKs?", "fast retransmit", "text", r)
    for cat, months in [("electricity", range(1, 13)), ("gas", (1, 4, 7, 10, 11, 12))]:
        for m in months:
            r = B(cat, m)[0]
            sd(f"How many {r['meta']['unit']} of {cat} did we use in {MONTH[m - 1]} 2026?", str(r["meta"]["used"]), "number", [r])
    for r in cl[::max(1, len(cl) // 8)][:8]:
        sd(f"What is the certificate number on {first(F(r, 'holder_name'))}'s {F(r, 'title')} certificate?", r["meta"]["cid"], "text", [r])
    bought = [r for r in rcs if r["meta"]["group"] in ("electronics", "appliance") and F(r, "date")]
    for r in [r for r in bought if sum(F(x, "merchant") == F(r, "merchant") and F(x, "date") == F(r, "date") for x in rcs) == 1][:4]:
        sd(f"What did I buy at {F(r, 'merchant')} on {day(F(r, 'date'))}?", r["meta"]["items"][0], "text", [r])
    for q in ["What is my passport number?", "How much was our water bill?", "What is my health insurance policy number?",
              "When does my car registration expire?", "What is the salary on my payslip?", "When does my gym membership expire?",
              "How much is our monthly house rent?", "What is my bank account balance?", "When does my visa expire?",
              "How much was the hospital bill?"]:
        A.append(dict(q=q, route="search", answer="", answer_type="none", sources=[], category=UN))
    groups = Counter(a["category"] for a in A)
    assert groups[S1] >= 50 and groups[X] + groups[EW] + groups[DU] + groups[CF] >= 50 and groups[SE] + groups[UN] >= 50, groups
    return search, A


# ---------------------------------------------------------------- self-check
def self_check(recs, search, ask):
    import pymupdf as fz
    for r in recs:
        assert list(r["fields"]) == FIELDS[r["type"]], r["file"]
        p = OUT / r["file"]
        if r["format"] == "pdf_scan":
            with fz.open(p) as doc:
                assert len(doc) == 1 and not doc[0].get_text().strip(), f"text layer in scan {r['file']}"
        elif r["format"] == "pdf_text":
            with fz.open(p) as doc:
                got = "".join(doc[0].get_text().split())
            assert sorted(got) == sorted("".join(r["text"].split())), f"text mismatch {r['file']}"
        else:
            Image.open(p).verify()
        f = r["fields"]
        for k, v in f.items():
            if v and (k.endswith("_date") or k in ("date", "date_of_birth", "warranty_until")):
                date.fromisoformat(v)
        for k in MONEY & set(f):
            assert f[k] is None or isinstance(f[k], float), (r["file"], k)
        if r["type"] == "utility_bill":
            assert abs(f["current_charges"] + (f["previous_balance"] or 0) - f["amount_due"]) < .01, r["file"]
        if r["type"] == "receipt" and f["subtotal"] is not None:
            assert abs(f["subtotal"] + f["tax"] - f["total_amount"]) < .01, r["file"]
    assert len({r["file"] for r in recs}) == len(recs)
    for sp in ("dev", "test"):
        n = [r for r in recs if r["split"] == sp]
        print(sp, len(n))
    dev, test = [r for r in recs if r["split"] == "dev"], [r for r in recs if r["split"] == "test"]
    assert 35 <= len(dev) <= 45 and 175 <= len(test) <= 200, (len(dev), len(test))
    assert Counter(r["duplicate_kind"] for r in test) == {None: len(test) - 13, "near": 7, "exact": 6}, Counter(r["duplicate_kind"] for r in test)
    assert sum(r["type"] != "other" for r in test) >= 100
    assert sum(r["format"] != "pdf_text" for r in test) >= 50
    holder = {}
    for r in recs:  # one ID number belongs to one person; two cards with it are the planned conflicts
        if r["type"] == "id_card":
            assert holder.setdefault(r["fields"]["id_number"], r["fields"]["name"]) == r["fields"]["name"], r["file"]
    for t, e in (("id_card", "expiry_date"), ("receipt", "warranty_until")):
        ex = [r["fields"][e] for r in test if r["type"] == t and r["fields"][e]]
        assert any(x < TODAY.isoformat() for x in ex) and any(TODAY.isoformat() <= x <= WINDOW.isoformat() for x in ex) \
            and any(x > WINDOW.isoformat() for x in ex), f"{t} expiry buckets"
    blob = " ".join(r["text"] for r in recs).lower()
    for w in ("passport", "water bill", "insurance", "salary", "registration", "gym", "house rent", "visa", "hospital", "account balance"):
        assert w not in blob, w
    assert len(search) == 60 and len(ask) >= 150
    assert all(set(s["relevant"]) <= {r["file"] for r in test} and s["relevant"] for s in search)
    assert all(a["answer_type"] == "none" or a["sources"] for a in ask)


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../data/synth/out")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    OUT = Path(a.out).resolve()
    for sub in ("dev", "test"):
        shutil.rmtree(OUT / sub, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    recs = build("dev", a.seed) + build("test", a.seed)
    with open(OUT / "labels.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in recs:
            fh.write(json.dumps({k: r[k] for k in ("file", "split", "type", "format", "fields", "text", "duplicate_of", "duplicate_kind")},
                                ensure_ascii=False) + "\n")
    search, ask = draft_eval([r for r in recs if r["split"] == "test"], a.seed)
    ev = Path(__file__).resolve().parent.parent / "eval"
    ev.mkdir(parents=True, exist_ok=True)
    for name, rows in (("search.jsonl", search), ("ask.jsonl", ask)):
        with open(ev / name, "w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in rows)
    self_check(recs, search, ask)
    print("ok", len(recs), "files")


if __name__ == "__main__":
    main()
