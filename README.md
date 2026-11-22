# Sniff

Sniff is a desktop app that indexes the documents on your laptop (PDFs, Office files, scans and photos), works out what each one is, extracts key details such as dates, amounts and expiry dates, and answers questions about them with links to the source page. It can add expiry dates to Google Calendar, and AI tools such as Claude Code can query it through MCP. Everything runs locally.

AI-4009 Generative AI term project (FAST-NUCES). The build plan is in [docs/PLAN.md](docs/PLAN.md).

## How it works

| Step | What happens | AI or code |
|---|---|---|
| Read | PDF, DOCX, PPTX and text are read page by page; scanned pages and photos go through OCR (RapidOCR) | AI (OCR) |
| Index | Text is split into passages, embedded (EmbeddingGemma) and stored with a BM25 keyword index in SQLite | AI (embeddings) |
| Type | A small local LLM (Qwen3-1.7B) picks the document type | AI |
| Facts | The LLM extracts the type's fields as JSON that must match a schema; each value is then looked up on the page and marked verified or not | AI + code |
| Ask | The LLM routes the question: either it fills a fixed facts query, which code runs (sums, counts, dates), or hybrid search finds passages and the LLM answers from them with citations | AI + code |
| Remind | Expiry and due dates become Google Calendar events after the user clicks to approve | Code |
| Stay fresh | Each file's size, time and hash are stored; re-scans re-index only changed files, renames keep their data, deleted files are removed, and cited files are re-checked before an answer is shown | Code |

## Run it

Requirements: Python 3.11 with [uv](https://docs.astral.sh/uv/), Node 22, and about 2 GB for models.

```bash
# 1. model servers (llama.cpp, CPU). Put llama-server and the GGUF models in models/
models/llama/llama-server -m models/qwen3-1.7b-q4_k_m.gguf --port 8080 --jinja --reasoning-budget 0 -c 8192
models/llama/llama-server -m models/embeddinggemma-300m.gguf --embedding --port 8081 -c 2048 -b 2048 -ub 2048

# 2. backend
cd backend
uv sync
uv run uvicorn sniff.api:app --port 8000

# 3. frontend
cd frontend
npm install
npm run dev            # VITE_MOCK=1 npm run dev runs the UI on fixture data, without the backend
```

Open the app, go to Settings, and index a folder.

**Google Calendar:** create an OAuth client (Desktop app) in a Google Cloud project with the Calendar API enabled, and save its JSON as `backend/.secrets/google_client.json`. The first reminder opens a Google sign-in page.

**Use Sniff from Claude Code:**

```bash
claude mcp add sniff -- uv --directory /path/to/sniff/backend run python -m sniff.mcp_server
```

## Tests and evaluation

```bash
cd backend
uv run pytest                                            # functional tests

uv run python ../data/synth/generate.py --out ../data/synth/out --seed 7   # synthetic labelled documents
uv run python ../eval/run_index.py        # index the test split (needs the model server)
uv run python ../eval/run_extraction.py   # document type + fact extraction vs keyword/regex baselines
uv run python ../eval/run_search.py       # hybrid vs BM25 vs embeddings vs file-name search
uv run python ../eval/run_ocr.py          # RapidOCR vs Tesseract
uv run python ../eval/run_ask.py          # router + answers vs plain RAG
```

Results go to `eval/results/` and show up on the Evaluation page in the app. Failure examples are saved next to them for error analysis.

## Team

Ibraheem Farooq, Wajih ur Raza, Ali Aan. Supervisor: Dr. Akhtar Jamil.
