"""Real test documents: scanned shop receipts from SROIE (ICDAR 2019), with their human labels.

    cd backend && uv run python ../data/real/fetch_sroie.py            # 60 receipts, seed 7

Downloads a fixed, seeded sample of the SROIE task-3 training images and key files from the
GitHub mirror zzzDavid/ICDAR-2019-SROIE (Hugging Face stalls on our network), and writes
data/real/out/real/sroie/<id>.jpg and data/real/out/labels.jsonl in our format:
{file, type: "receipt", fields: {merchant, date, total_amount}, split: "real"}.
SROIE has no warranty field, so warranty_until is left out (not scored). A date that cannot be
read day-first is left out too, rather than guessed. Re-running skips files already downloaded.
"""
import argparse
import json
import random
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

RAW = "https://raw.githubusercontent.com/zzzDavid/ICDAR-2019-SROIE/master/data"
N_TRAIN = 626  # data/key/000.json ... 625.json, data/img/000.jpg ... 625.jpg
OUT = Path(__file__).resolve().parent / "out"
# SROIE receipts are Malaysian: day first. Two-digit years appear ("12-01-19").
DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y", "%Y-%m-%d", "%Y/%m/%d",
                "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d %b %y", "%d-%b-%y", "%b %d %Y", "%B %d %Y")


def get(url: str, tries: int = 5) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "sniff-eval"}), timeout=60) as r:
                return r.read()
        except Exception as e:  # the network here drops connections; back off and retry
            if i == tries - 1:
                raise
            print(f"  retry {url.rsplit('/', 1)[-1]}: {e}")
            time.sleep(3 * (i + 1))
    raise AssertionError("unreachable")


def parse_date(s: str) -> str | None:
    s = re.sub(r"\s+", " ", re.sub(r"[(),]", " ", s)).strip()
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            return d.isoformat() if 2000 <= d.year <= 2030 else None
        except ValueError:
            pass
    return None


def parse_total(s: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group()) if m else None


def to_fields(key: dict) -> dict:
    fields = {}
    if key.get("company", "").strip():
        fields["merchant"] = key["company"].strip()
    if (d := parse_date(key.get("date", ""))) is not None:
        fields["date"] = d
    if (t := parse_total(key.get("total", ""))) is not None:
        fields["total_amount"] = t
    return fields


def main(n: int, seed: int) -> None:
    ids = sorted(random.Random(seed).sample(range(N_TRAIN), n))
    folder = OUT / "real" / "sroie"
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in ids:
        name = f"{i:03d}"
        img = folder / f"{name}.jpg"
        try:
            key = json.loads(get(f"{RAW}/key/{name}.json").decode("utf-8") or "{}")
        except (ValueError, OSError) as e:
            print(f"skip {name}: key unreadable ({e})")
            continue
        fields = to_fields(key)
        if not fields:
            print(f"skip {name}: empty key")
            continue
        if not img.exists():
            img.write_bytes(get(f"{RAW}/img/{name}.jpg"))
        rows.append({"file": f"real/sroie/{name}.jpg", "type": "receipt", "fields": fields, "split": "real",
                     "source": "SROIE 2019 task 3 (train)", "raw": key})
        print(f"{name}: {fields}")
    (OUT / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} labelled receipts -> {OUT / 'labels.jsonl'}")


if __name__ == "__main__":
    assert parse_date("12-01-19") == "2019-01-12" and parse_date("25/12/2018") == "2018-12-25"
    assert parse_total("RM 1,060.00") == 1060.0
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    main(a.n, a.seed)
