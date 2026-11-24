# Your own documents (private test set)

This folder is for real documents that team members test Sniff on. Everything here except this README
is gitignored, so nothing you put here reaches GitHub. Still, treat it as private: don't zip it, share
it, or copy it into a prompt.

## Add documents

1. Put files under a folder with your name, for example `data/own/ali/electricity-oct.pdf`.
   PDF, Word, PowerPoint and images (phone photos are fine) all work.
2. **Blur or cover personal numbers before you add a file**: CNIC and passport numbers, bank and card
   numbers, phone numbers, addresses. Use any image editor, or cover them with paper before taking the
   photo. Keep the field you want to test readable (for example the amount and due date on a bill).
3. Don't add other people's ID cards.

## Label them

Add one line per file to `data/own/labels.jsonl`. The `file` path is relative to `data/own`, and the
field names per type are in `docs/FIELDS.md`. Write values the way the evaluation compares them:

- dates as `YYYY-MM-DD`, a billing month as `YYYY-MM`;
- amounts as plain numbers without commas or currency (`4320.5`);
- text as printed (case and punctuation are ignored when scoring);
- `null` for a field the document should have but doesn't (a cut-off or incomplete page);
- leave a field out if you are not sure, or you blurred it. Fields left out are not scored.

```json
{"file": "ali/electricity-oct.pdf", "type": "utility_bill", "split": "own", "fields": {"provider": "Lakeshore Electric", "billing_month": "2026-10", "due_date": "2026-11-18", "amount_due": 6986}}
{"file": "ali/IMG_2041.jpg", "type": "receipt", "split": "own", "fields": {"merchant": "FreshBasket Grocers", "date": "2026-11-02", "total_amount": 3120}}
{"file": "ali/notes.pdf", "type": "other", "split": "own", "fields": {}}
```

Types: `id_card`, `utility_bill`, `certificate`, `receipt`, `other`.

## Run it

From `backend/`, with the model servers running (see `docs/EVAL.md`):

```
uv run python ../eval/run_index.py --set own
uv run python ../eval/run_extraction.py --sets own
```

The results go to `eval/results/document_type.json` and `fact_extraction.json` with `set: own`. Don't
commit results that quote your values: the failure files contain document text.
