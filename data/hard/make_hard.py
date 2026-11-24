"""Harder copies of the synthetic test documents (feedback point 12), with labels.

    cd backend && uv run python ../data/hard/make_hard.py            # seed 7, ~125 documents
    cd backend && uv run python ../data/hard/make_hard.py --limit 8  # quick check

Reads data/synth/out/labels.jsonl (split "test") and writes data/hard/out/hard/<variant>/... plus
data/hard/out/labels.jsonl with rows {file, type, fields, split: "hard", source, variant, effects, text}.
Each source document is used at most once, so hard copies are never near-duplicates of each other.

Variants:
- photo:       phone photo: perspective, rotation, shadow, low light, blur, JPEG artefacts (a random mix)
- lowres:      a low-resolution grey scan inside a PDF
- handwriting: a receipt written out in a handwriting font (Windows fonts Ink Free, Bradley Hand, Segoe Script)
- cutoff:      a text PDF photographed with part of the page missing. Fields whose printed value is only on
               the missing part are set to null; the page's word positions decide this, not guesswork.
Deterministic for a given seed. Re-run it after the generator changes: it deletes and rebuilds out/hard/.
"""
import argparse
import io
import json
import random
import re
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "synth" / "out"
OUT = Path(__file__).resolve().parent / "out"
DPI = 150
COUNTS = {"cutoff": 25, "handwriting": 20, "lowres": 35, "photo": 45}
FONTS = [Path(r"C:\Windows\Fonts") / f for f in ("Inkfree.ttf", "BRADHITC.TTF", "segoesc.ttf")]
DESKS = [(120, 90, 60), (70, 70, 75), (150, 140, 120), (40, 60, 50), (200, 195, 185)]


# ---------------------------------------------------------------- images
def page_image(path: Path) -> Image.Image | None:
    """Page 1 as an RGB image; None for multi-page files (the copy would silently drop pages)."""
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(str(path)) as doc:
            if len(doc) != 1:
                return None
            pix = doc[0].get_pixmap(dpi=DPI, alpha=False)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return Image.open(path).convert("RGB")


def jpeg(img: Image.Image, q: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    return buf.getvalue()


def perspective(img, rng, bg):
    w, h = img.size
    pad = int(.08 * max(w, h))
    c = Image.new("RGB", (w + 2 * pad, h + 2 * pad), bg)
    c.paste(img, (pad, pad))
    W, H = c.size
    src = [(0, 0), (W, 0), (W, H), (0, H)]
    j = lambda: rng.uniform(0, .07)
    dst = [(W * j(), H * j()), (W * (1 - j()), H * j()), (W * (1 - j()), H * (1 - j())), (W * j(), H * (1 - j()))]
    # PIL maps each output pixel back to the input: solve for the 8 coefficients of dst -> src
    a, b = [], []
    for (x, y), (u, v) in zip(dst, src):
        a += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        b += [u, v]
    coeffs = np.linalg.solve(np.array(a, float), np.array(b, float))
    return c.transform(c.size, Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC, fillcolor=bg)


def shadow(img, rng):
    """A soft dark band across part of the page, like a hand or phone shadow."""
    w, h = img.size
    mask = Image.new("L", img.size, 0)
    x0 = rng.uniform(-.2, .6) * w
    ImageDraw.Draw(mask).polygon([(x0, -10), (x0 + rng.uniform(.3, .6) * w, -10),
                                  (x0 + rng.uniform(.1, .5) * w, h + 10), (x0 - rng.uniform(.1, .3) * w, h + 10)], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(w, h) / 25))
    dark = ImageEnhance.Brightness(img).enhance(rng.uniform(.45, .65))
    return Image.composite(dark, img, mask)


def low_light(img, rng):
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(.5, .7))
    tint = Image.new("RGB", img.size, (255, rng.randint(215, 235), rng.randint(170, 200)))  # warm indoor light
    return ImageChops.multiply(img, tint)


def noise(img, rng, amount):
    n = Image.frombytes("L", img.size, rng.randbytes(img.width * img.height)).point(lambda v: 128 + int((v - 128) * amount))
    return ImageChops.add(img, n.convert("RGB"), 1.0, -128)


def photo(img, rng, effects=None):
    """Phone-photo look. Returns (jpeg bytes, list of effects applied)."""
    bg = rng.choice(DESKS)
    effects = effects or ["perspective", "rotation", "blur", "jpeg"] + rng.sample(["shadow", "low_light"], rng.randint(0, 2))
    if max(img.size) > 1400:
        img = img.copy()
        img.thumbnail((1400, 1400), Image.LANCZOS)
    if "perspective" in effects:
        img = perspective(img, rng, bg)
    if "rotation" in effects:
        img = img.rotate(rng.uniform(-8, 8), expand=True, resample=Image.BICUBIC, fillcolor=bg)
    if "shadow" in effects:
        img = shadow(img, rng)
    if "low_light" in effects:
        img = low_light(img, rng)
    img = noise(img, rng, rng.uniform(.1, .25))
    if "blur" in effects:
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(.8, 1.8)))
    return jpeg(img, rng.randint(18, 40) if "jpeg" in effects else 85), effects


def lowres_pdf(img, rng) -> bytes:
    scale = rng.uniform(.4, .55)  # 150 dpi -> about 60-80 dpi
    small = img.convert("L").resize((int(img.width * scale), int(img.height * scale)), Image.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(rng.uniform(.3, .7)))
    data = jpeg(small.convert("RGB"), rng.randint(25, 45))
    w, h = img.width * 72 / DPI, img.height * 72 / DPI
    doc = pymupdf.open()
    doc.new_page(width=w, height=h).insert_image(pymupdf.Rect(0, 0, w, h), stream=data)
    doc.set_metadata({})
    return doc.tobytes(deflate=True, no_new_id=True)


def handwriting(text: str, rng) -> tuple[Image.Image, str]:
    """Write the receipt's lines in a handwriting font with uneven lines. Separator rows are dropped."""
    lines = [l for l in text.splitlines() if l.strip() and not re.fullmatch(r"[-=_*.\s]+", l)]
    font = ImageFont.truetype(str(rng.choice([f for f in FONTS if f.exists()])), rng.randint(30, 36))
    lh = int(font.size * 1.6)
    w = max(int(font.getlength(l)) for l in lines) + 120
    img = Image.new("RGB", (w, lh * len(lines) + 120), (rng.randint(235, 250), rng.randint(232, 245), rng.randint(215, 235)))
    d = ImageDraw.Draw(img)
    ink = rng.choice([(20, 30, 90), (25, 25, 25), (10, 40, 120)])
    y = 60
    for l in lines:
        d.text((60 + rng.randint(-8, 12), y + rng.randint(-4, 4)), l, font=font, fill=ink)
        y += lh
    return img, "\n".join(lines)


# ---------------------------------------------------------------- cut-off pages
DATE_FORMATS = ["%d/%m/%Y", "%d %b %Y", "%Y-%m-%d", "%d.%m.%Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d-%b-%Y",
                "%d-%m-%Y", "%d/%m/%y"]
MONTH_FORMATS = ["%B %Y", "%b %Y", "%b-%Y", "%m/%Y", "%Y-%m", "%b-%y", "%B, %Y"]


def printed_forms(value) -> list[str]:
    """Ways the generator may have printed a label value, to find it on the page."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        a = float(value)
        # shortcut: a short whole amount like "500" can also match inside "1500"; fine for a test set label
        return sorted({f"{a:,.2f}", f"{a:.2f}", f"{a:,.0f}" if a == int(a) else f"{a:,.2f}"})
    s = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        d = date.fromisoformat(s)
        n = d.day
        suffix = "th" if 11 <= n <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return sorted({d.strftime(f) for f in DATE_FORMATS} | {f"{n}{suffix} {d:%B %Y}"})
    if re.fullmatch(r"\d{4}-\d{2}", s):
        d = date.fromisoformat(s + "-01")
        return sorted({d.strftime(f) for f in MONTH_FORMATS})
    return [s]


def cutoff(path: Path, label: dict, rng):
    """Crop a one-page text PDF between two text rows. Returns (image, fields, kept text) or None."""
    with pymupdf.open(str(path)) as doc:
        if len(doc) != 1:
            return None
        page = doc[0]
        words = page.get_text("words")
        # where each field's value is printed: list of y-ranges (points); a field we cannot find makes the label unsafe
        where = {}
        for field, v in label["fields"].items():
            if v is None:
                continue
            rects = [r for s in printed_forms(v) for r in page.search_for(s)]
            if not rects:
                return None
            where[field] = rects
        rows = _rows(words)
        side = "bottom" if rng.random() < .7 else "top"
        bands = [(min(w[1] for w in g), max(w[3] for w in g)) for g in rows]  # (top, bottom) of each text row
        gaps = [(a[1] + b[0]) / 2 for a, b in zip(bands, bands[1:]) if b[0] > a[1]]
        rng.shuffle(gaps)
        lo, hi = bands[0][0], bands[-1][1]
        for cut in gaps:
            frac = (cut - lo) / max(hi - lo, 1)
            if not (.45 <= frac <= .8 if side == "bottom" else .15 <= frac <= .4):
                continue
            keep = (lambda y0, y1: y1 <= cut) if side == "bottom" else (lambda y0, y1: y0 >= cut)
            fields = {f: (v if f not in where or any(keep(r.y0, r.y1) for r in where[f]) else None)
                      for f, v in label["fields"].items()}
            if all(fields[f] is not None for f in where):
                continue  # nothing labelled was cut; try another line
            pix = page.get_pixmap(dpi=DPI, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            y = int(cut * DPI / 72)
            img = img.crop((0, 0, img.width, y) if side == "bottom" else (0, y, img.width, img.height))
            kept = [w for w in words if keep(w[1], w[3])]
            text = "\n".join(" ".join(w[4] for w in sorted(g, key=lambda w: w[0])) for g in _rows(kept))
            return img, fields, text, side
    return None


def _rows(words) -> list[list]:
    """Group PyMuPDF words into text rows, top to bottom (words whose tops are within 3 pt)."""
    rows = []
    for w in sorted(words, key=lambda w: w[1]):
        if rows and w[1] - rows[-1][0][1] <= 3:
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


# ---------------------------------------------------------------- main
def main(seed: int, limit: int | None) -> None:
    rows = [json.loads(l) for l in (SRC / "labels.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    pool = [r for r in rows if r["split"] == "test" and (SRC / r["file"]).exists()]
    rng = random.Random(seed)
    rng.shuffle(pool)
    shutil.rmtree(OUT / "hard", ignore_errors=True)
    used, out = set(), []
    counts = {k: (min(v, limit) if limit else v) for k, v in COUNTS.items()}

    def emit(src, variant, data: bytes, ext, fields, effects, text, fmt):
        rel = Path(src["file"]).relative_to("test").with_suffix(ext).as_posix()
        dest = OUT / "hard" / variant / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        used.add(src["file"])
        out.append({"file": f"hard/{variant}/{rel}", "type": src["type"], "fields": fields, "split": "hard",
                    "source": src["file"], "variant": variant, "effects": effects, "format": fmt, "text": text})

    for variant in ("cutoff", "handwriting", "lowres", "photo"):
        n = 0
        for src in pool:
            if n >= counts[variant] or src["file"] in used:
                continue
            r = random.Random(f"{seed}-{variant}-{src['file']}")
            path = SRC / src["file"]
            if variant == "cutoff":
                if src.get("format") != "pdf_text" or not any(v is not None for v in src["fields"].values()):
                    continue
                got = cutoff(path, src, r)
                if not got:
                    continue
                img, fields, text, side = got
                data, effects = photo(img, r, ["rotation", "blur", "jpeg"])
                emit(src, variant, data, ".jpg", fields, [f"cut_{side}"] + effects, text, "image")
            elif variant == "handwriting":
                if src["type"] != "receipt" or not any(f.exists() for f in FONTS):
                    continue
                img, text = handwriting(src["text"], r)
                data, effects = photo(img, r, ["rotation", "blur", "jpeg"])
                emit(src, variant, data, ".jpg", src["fields"], ["handwriting"] + effects, text, "image")
            else:
                img = page_image(path)
                if img is None:
                    continue
                if variant == "lowres":
                    emit(src, variant, lowres_pdf(img, r), ".pdf", src["fields"], ["low_resolution", "grey", "jpeg"],
                         src["text"], "pdf_scan")
                else:
                    data, effects = photo(img, r)
                    emit(src, variant, data, ".jpg", src["fields"], effects, src["text"], "image")
            n += 1
        print(f"{variant}: {n}")
    (OUT / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    print(f"{len(out)} hard documents -> {OUT / 'labels.jsonl'}")


if __name__ == "__main__":
    assert "21/03/2026" in printed_forms("2026-03-21") and "March 2026" in printed_forms("2026-03")
    assert "4,320" in printed_forms(4320.0) and "4320.00" in printed_forms(4320.0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, help="at most this many per variant (for a quick check)")
    a = ap.parse_args()
    main(a.seed, a.limit)
