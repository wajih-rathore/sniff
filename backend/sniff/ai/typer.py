"""Document type without the chat model: a logistic regression on the MiniLM vectors phase 1 already stored, checked
against keyword rules. It answers only when the two agree and the classifier is sure; otherwise it abstains (None)
and the caller asks the model, as before. When no model can sort the document, guess() gives the classifier's top type
above a floor, and the caller sends that document's facts to review.

The weights live in type_weights.py, written by `eval/run_fastindex.py train-types` from the synthetic training split
(never dev or test). Training is plain full-batch gradient descent from zero weights, so the same data gives the same
weights every time.
"""
import base64
import re

import numpy as np

# words that point to one type; a document's rule type is the one with the most hits
CUES = {
    "id_card": r"identity card|id card|national identity|driving licen[cs]e|licen[cs]e no|student card|staff card|employee card|cnic",
    "utility_bill": r"\bbill\b|amount due|amount payable|billing month|consumer no|units|kwh|meter|last date of payment|electricity|\bgas\b|internet",
    "certificate": r"certify|(?<!warranty )certificate|diploma|awarded|degree of|presented to|successfully completed|completion",
    "receipt": r"receipt|invoice|cash memo|order confirmation|\bqty\b|subtotal|sub total|thank you for shopping|grand total",
    "passport": r"passport|nationality|place of birth|\bp<",
    "transcript": r"transcript|\bcgpa\b|\bsgpa\b|credit hours|semester gpa|grade",
    "bank_statement": r"account statement|e-statement|opening balance|closing balance|balance b/f|balance c/f|\biban\b|total credits|total debits",
    "warranty_card": r"warranty card|warranty certificate|serial no|serial number|\bs/n\b|dealer|warranty valid until",
    "offer_letter": r"offer of employment|offer letter|joining date|date of joining|gross salary|designation|probation",
    "insurance_policy": r"insurance|assurance|takaful|policy no|policy schedule|premium|sum insured|sum assured",
    "tenancy_agreement": r"tenancy|rent agreement|lease agreement|landlord|tenant|lessee|lessor|premises|security deposit",
    "fee_voucher": r"fee voucher|challan|tuition fee|fee for|payable after due date|bank copy|student copy",
}
_CUES = {t: re.compile(p, re.I) for t, p in CUES.items()}
# guess(): on dev the top guess was right for all 6 abstained documents at 0.30 to 0.50 sure, and wrong once at 0.25;
# 0.50 stays clear of that zone (eval/run_fastindex.py guess, docs/EVAL.md)
GUESS_FLOOR = 0.5

try:
    from .type_weights import BIAS, CLASSES, THRESHOLD, WEIGHTS
    _W = np.frombuffer(base64.b64decode(WEIGHTS), dtype=np.float32).reshape(len(CLASSES), -1)
    _B = np.asarray(BIAS, dtype=np.float32)
except ImportError:  # not trained yet: always abstain, so every document goes to the model as before
    CLASSES, THRESHOLD, _W, _B = [], 1.0, None, None


def rule_type(text: str) -> str | None:
    """The type with the most cue words, or None if no cue is printed."""
    hits = {t: len(p.findall(text)) for t, p in _CUES.items()}
    best = max(hits, key=hits.get)
    return best if hits[best] else None


def doc_vector(conn, file_id: int) -> np.ndarray | None:
    """The mean of the document's chunk vectors, unit length; None if no chunk has a vector."""
    rows = conn.execute("SELECT embedding FROM chunk_vec WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id=?)", (file_id,)).fetchall()
    if not rows:
        return None
    v = np.mean([np.frombuffer(r[0], dtype=np.float32) for r in rows], axis=0)
    n = np.linalg.norm(v)
    return v / n if n else None


def softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def probabilities(vec: np.ndarray, w=None, b=None) -> np.ndarray:
    w = _W if w is None else w
    b = _B if b is None else b
    return softmax(vec @ w.T + b)


def decide(probs: np.ndarray, classes: list[str], text: str, threshold: float) -> str | None:
    """The classifier's type if it is at least `threshold` sure and the keyword rules agree (for "other": no cue word
    printed, or the cue type is far less likely). Otherwise None."""
    top = int(np.argmax(probs))
    t, rule = classes[top], rule_type(text)
    if probs[top] < threshold:
        return None
    if t == "other":
        return t if rule is None or probs[classes.index(rule)] < 0.05 else None
    return t if rule == t else None


def _probs(conn, file_id: int) -> np.ndarray | None:
    if _W is None or (vec := doc_vector(conn, file_id)) is None or vec.shape[0] != _W.shape[1]:
        return None
    return probabilities(vec)


def classify(conn, file_id: int, pages: list[tuple[int, str]]) -> str | None:
    """The document's type, or None when the caller should ask the model."""
    probs = _probs(conn, file_id)
    return None if probs is None else decide(probs, CLASSES, "\n".join(t for _, t in pages), THRESHOLD)


def guess(conn, file_id: int) -> str | None:
    """For a document classify() abstained on and no model can sort (AI off, a provider off this laptop, a failed
    call): the classifier's top type if it is at least GUESS_FLOOR sure, whatever the keyword rules say. None below the
    floor, and for "other" (the document stays unclassified either way)."""
    probs = _probs(conn, file_id)
    if probs is None:
        return None
    top = int(np.argmax(probs))
    return CLASSES[top] if probs[top] >= GUESS_FLOOR and CLASSES[top] != "other" else None


def train(x: np.ndarray, y: np.ndarray, n_classes: int, l2: float = 1e-3, lr: float = 2.0, steps: int = 1500):
    """Softmax regression by full-batch gradient descent from zero weights: deterministic for the same data."""
    w = np.zeros((n_classes, x.shape[1]), dtype=np.float64)
    b = np.zeros(n_classes, dtype=np.float64)
    onehot = np.eye(n_classes)[y]
    for _ in range(steps):
        p = softmax(x @ w.T + b)
        g = (p - onehot) / len(x)
        w -= lr * (g.T @ x + l2 * w)
        b -= lr * g.sum(axis=0)
    return w.astype(np.float32), b.astype(np.float32)
