# Evaluation

How we test each AI part of Sniff: the test sets, the metrics, the baseline each part is compared with, and the
exact commands. It answers feedback points 1 (8 GB laptop), 2 (stronger reference model), 8 (router on its own),
12 (realistic and poor-quality documents) and 13 (cross-document questions).

**Status:** the scripts are written and were run on small subsets (10 documents at most) to check they work.
No full results yet. The final run fills in the numbers below and the error analysis at the end. Nothing in this
file is a measured result unless it says so.

Rules we keep:
- Test documents never appear in prompts or few-shot examples. The generator uses different seeds and people for `dev` and `test`.
- No LLM does arithmetic or date maths. The evaluation compares the numbers code computed.
- Every script writes `eval/results/<component>.json` (shown on the app's Evaluation page) and
  `<component>_failures.json` for error analysis.

## Test sets

| Set | Where | Size | What it is | Labels |
|---|---|---|---|---|
| `test` | `data/synth/out/test` | 184 on 24 Nov (re-count after regenerating) | Synthetic bills, receipts, ID cards, certificates and other files: text PDFs, scans and phone photos | From the generator, `data/synth/out/labels.jsonl` |
| `hard` | `data/hard/out/hard` | 125 (25 cut-off, 20 handwriting, 35 low-res, 45 photo) | Harder copies of `test` documents, made by `data/hard/make_hard.py` (seed 7) | Copied from the source document; cut-off fields set to `null` |
| `real` | `data/real/out/real/sroie` | 60 | Real scanned shop receipts from SROIE (ICDAR 2019), human labelled | Company, date, total from SROIE, converted by `data/real/fetch_sroie.py` |
| `own` | `data/own/<member>` | optional | Team members' own documents, personal numbers blurred; never committed | Hand-written, see `data/own/README.md` |
| ask | `data/eval/ask.jsonl` | 181 on 24 Nov: single_fact 58, cross_doc_total 48, semantic 45, expiring_window 10, unanswerable 10, duplicates 6, conflict 4 | Questions with gold answers and sources, each with a `category` | From the generator's code |
| search | `data/eval/search.jsonl` | 60 | Queries with the relevant files | From the generator |

The `duplicates` and `conflict` categories are small (6 and 4 questions), so their per-category numbers only show
whether the feature works on those cases; they are not a reliable rate.

Each set gets its own database (`.sniff/eval.db` for `test`, `.sniff/eval-hard.db`, `.sniff/eval-real.db`), so
hard copies are never counted as near-duplicates of test documents in the ask totals.

**Hard set details.** Each source document is used once. The variants:
- `photo`: perspective, rotation up to 8 degrees, blur, JPEG quality 18 to 40, and sometimes a shadow or low warm light. The effects used are stored per document (`effects`), so failures can be grouped by effect.
- `lowres`: a grey scan at about 60 to 80 dpi inside a PDF.
- `handwriting`: a receipt written out in a handwriting font (Ink Free, Bradley Hand or Segoe Script; Windows fonts).
- `cutoff`: a text PDF photographed with the top or bottom part missing. A field becomes `null` when its printed value is only on the missing part. The script finds the value with PyMuPDF word positions; a document whose fields it cannot find on the page is skipped instead of guessed.

**Real set details.** A fixed sample (seed 7) of 60 of the 626 SROIE task-3 training receipts, downloaded from the
GitHub mirror `zzzDavid/ICDAR-2019-SROIE`. We map `company` to `merchant`, `date` to `date` (read day first; a date
we cannot read is left out), and `total` to `total_amount`. SROIE has no warranty field, so it is not scored.
SROIE's own labels have some known errors; we did not correct them. The downloaded files are gitignored.

## Components

| Component | Test set | Metrics | Baseline | Command (from `backend/`) |
|---|---|---|---|---|
| OCR | `test` scans and photos; `hard` | Character error rate (CER), latency | Tesseract defaults | `run_ocr.py`, `run_ocr.py --set hard` |
| Document type | `test`, `hard`, `real` | Accuracy, macro-F1, confusion matrix | Keyword rules | `run_extraction.py` |
| Fact extraction | same | Field accuracy (per field too), schema-valid rate, verified precision and recall, needs-review rate, p50/p95 LLM latency | Regex rules | `run_extraction.py` |
| Model comparison | same documents for every model | as above, per model and set | Qwen3-1.7B vs Gemma 3 1B vs Qwen3-8B | `run_extraction.py --compare ...` |
| Search | `test` + search.jsonl | Recall@5 (capped), MRR, latency | BM25 only, embeddings only, file-name search | `run_search.py` |
| Embeddings | same | as search | all-MiniLM vs EmbeddingGemma (and nomic) | `run_embeddings.py ...` |
| Router | ask.jsonl | Tool-selection accuracy, facts recall, search recall | Keyword rules | `run_ask.py` |
| Ask | ask.jsonl | Answer correctness, citation correctness, abstention on unanswerable, false abstention, latency; all by category | Plain RAG (top-5 passages + LLM, no facts table) | `run_ask.py` |
| System | N documents, M questions | p50/p95 per stage, peak memory per process | 4.5 GB budget | `scripts/benchmark.py` |

All `eval/*.py` scripts run from `backend/` as `uv run python ../eval/<script>.py`.

### Metric definitions
- **Field accuracy:** a field is right when the stored value equals the label: numbers within 0.01, dates as ISO
  strings, text ignoring case, spaces and punctuation. A `null` label is right only when Sniff stored nothing for
  that field, so inventing a value for a cut-off field counts as an error. If the document type is wrong, its
  fields count as wrong.
- **Schema-valid rate:** documents whose model output passed the JSON schema (after up to 2 retries).
- **Verified precision:** of the facts with status `verified`, the share that are right. **Verified recall:** of the
  right facts, the share marked `verified`. We report both, because marking nothing verified would give a perfect
  precision.
- **Needs-review rate:** facts with status `needs_review` over all facts stored for labelled fields.
- **Latency (extraction):** time spent waiting for the LLM per document (classify + extract), p50 and p95.
- **Tool selection:** the router's choice (`facts` or `search`) against the gold route. The keyword baseline sends
  questions with calculation or date words (how much, total, how many, when, expire, due, tax, ...) to facts.
- **Answer correctness:** numbers within 0.5% (code computes them), dates and short text must appear in the answer,
  lists must contain every item, unanswerable questions must be refused (search found nothing, or the facts query
  matched no documents). **Citation correctness:** at least one
  cited file is a gold source (only questions that have gold sources). **Abstention:** share of unanswerable
  questions refused; **false abstention:** share of answerable questions refused.
- **By category:** `single_fact`, `cross_doc_total`, `expiring_window`, `duplicates`, `conflict`, `semantic`,
  `unanswerable`. Rows without a category are grouped as `uncategorised (route)`. For `conflict` we also report the
  share of answers that list the conflict.

## Models

`eval/fetch_models.py` downloads models with resume (HTTP Range) and checks each file's sha256 before renaming it
into `models/`. Chat models come from the Ollama registry (plain GGUF blobs; Hugging Face stalls on our network).
EmbeddingGemma comes from `ggml-org` on Hugging Face, because the Ollama build fails in llama.cpp with
"wrong number of tensors; expected 316, got 314".

| Key | File | Source | Size | Use |
|---|---|---|---|---|
| `gemma3-1b` | `gemma3-1b-it-q4_k_m.gguf` | Ollama `gemma3:1b` (Q4_K_M) | 806 MB | second small model |
| `qwen3-8b` | `qwen3-8b-q4_k_m.gguf` | Ollama `qwen3:8b` (Q4_K_M) | 5.2 GB | stronger reference |
| `qwen3-4b` | `qwen3-4b-q4_k_m.gguf` | Ollama `qwen3:4b` (Q4_K_M) | 2.5 GB | fallback reference if the 8B is not ready |
| `embeddinggemma` | `embeddinggemma-300m.gguf` | `ggml-org/embeddinggemma-300M-GGUF` (Q8_0) | 334 MB | embedding comparison |

```
python eval/fetch_models.py --status          # progress of each download
backend/.venv/Scripts/python.exe eval/fetch_models.py gemma3-1b qwen3-8b embeddinggemma
```
Use the backend's Python (or `uv run`): the MSYS Python on this laptop has no CA certificates.

Checked on 24 Nov: `embeddinggemma-300m.gguf` and `gemma3-1b-it-q4_k_m.gguf` downloaded with matching sha256 and
load in our llama-server build. EmbeddingGemma returns 768-dimension vectors (to use it in the app: start it on
8081 and set `SNIFF_EMBED_MODEL=embeddinggemma`, `SNIFF_EMBED_DIM=768`). Gemma 3 1B answers in the JSON schema
format the extractor uses. The Qwen3-8B download was still running; `python eval/fetch_models.py --status` shows
where it is. Each check started the model on a spare port and stopped it right after.

## Model comparison (point 2)

The comparison indexes the same documents once per model, through the real pipeline (OCR, embeddings, type,
extraction, verification), into `.sniff/eval[-set]-<model>.db`, and scores them the same way. It is resumable.
Rows for models already in `eval/results/model_comparison.json` are kept, so the models can run one at a time.
`--limit 40` takes a fixed subset of 40 documents per set, round-robin over types.

```
cd backend
# 1. Qwen3-1.7B (the shared server on 8080) and Gemma 3 1B on a spare port
../models/llama/llama-server.exe -m ../models/gemma3-1b-it-q4_k_m.gguf --port 8090 --jinja -c 4096 -np 1 -t 4
uv run python ../eval/run_extraction.py --compare qwen3-1.7b=http://127.0.0.1:8080/v1 gemma3-1b=http://127.0.0.1:8090/v1 --sets test hard real --limit 40
# stop the Gemma server

# 2. Qwen3-8B reference. Stop the 1.7B server on 8080 first and check about 6 GB is free:
#    PowerShell: (Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory / 1MB   (GB)
../models/llama/llama-server.exe -m ../models/qwen3-8b-q4_k_m.gguf --port 8090 --jinja --reasoning-budget 0 -c 4096 -np 1 -t 4
uv run python ../eval/run_extraction.py --compare qwen3-8b=http://127.0.0.1:8090/v1 --sets test hard real --limit 40
# stop the 8B server, then start the 1.7B server again exactly as scripts/dev.ps1 does
```
Notes:
- `-c 4096 -np 1` is enough: a document is cut to 6,000 characters before it is sent. It keeps the KV cache small.
- The weights are memory-mapped, so they use RAM but little commit; the KV cache and buffers do use commit.
- If the 8B model is too slow (more than about 3 hours for the subset), lower `--limit` and say so. If it has not
  downloaded, use `qwen3-4b` the same way and say so.
- `--reasoning-budget 0` turns Qwen3's thinking off, as in the app.

## System benchmark (point 1)

`scripts/benchmark.py` runs the back end in its own process (the same `scan.index_file` and `ask.ask` the API
calls), indexes N documents into a fresh `.sniff/bench.db` and asks M questions. Every 0.5 s it samples the memory
of the back end and of both `llama-server` processes: working set (RAM in use) and commit (private memory). It
records the peak of each, the peak of their sum, and p50/p95 for each stage (read, OCR, embed, classify, extract,
index total, search, route, ask). Output: `eval/results/system.json`, including a memory timeline.

**On a real 8 GB laptop** (one command, from `backend/`, after `uv sync`, the models and `data/synth/out`):
```
uv run --with psutil python ../scripts/benchmark.py --start-servers --threads 4 --n 20 --m 20
```
It starts both model servers with 4 threads, runs, and stops them. `--docs <folder>` points it at other documents.

**On this machine** (a laptop CPU with 4 cores and 8 threads, but 32 GB RAM, so it cannot show the memory limit
directly): we run the same command with `--start-servers --threads 4`, which limits llama.cpp to 4 threads, and
compare the peak working set of all Sniff processes together with **4.5 GB**, the memory an 8 GB laptop leaves for
apps after Windows. `system.json` has `peak_working_set_within_budget` for this. The CPU is like the target, the RAM
is not, so the latency numbers transfer better than a desktop's would. Running it on a team member's 8 GB laptop is
planned.

To attach to servers that are already running instead (as in the smoke test), leave out `--start-servers`;
the thread count is then whatever the servers were started with.

**Watch the chat server's memory.** In two smoke tests (attached to the shared servers, which other agents were
also using) the chat server's working set was 3.4 GB and then 4.4 GB, and all Sniff processes together peaked at
3.6 GB and 4.7 GB. Qwen3-1.7B's weights are only 1.4 GB. We have not confirmed the cause. One likely part:
llama-server keeps a prompt cache in RAM of up to 8 GiB by default (`--cache-ram 8192`), on top of the KV cache
for `-c 8192` across several slots. So run the benchmark twice and report both:
```
uv run --with psutil python ../scripts/benchmark.py --start-servers --threads 4 --n 20 --m 20
uv run --with psutil python ../scripts/benchmark.py --start-servers --threads 4 --n 20 --m 20 --chat-args "--cache-ram 512 -np 1"
```
If the second stays inside 4.5 GB at a similar latency, those flags belong in `scripts/dev.ps1` and the installer.

## Run order for the final evaluation

Run from `backend/` with the chat server (8080) and embedding server (8081) up, as `scripts/dev.ps1` starts them.

```
# 1. regenerate the synthetic documents and test sets
uv run python ../data/synth/generate.py --out ../data/synth/out --seed 7
# 2. build the hard set (again after every generator change) and fetch the real set
uv run python ../data/hard/make_hard.py
uv run python ../data/real/fetch_sroie.py
# 3. index each set into its own database (resumable; drop --fresh to continue a stopped run)
uv run python ../eval/run_index.py --fresh
uv run python ../eval/run_index.py --set hard --fresh
uv run python ../eval/run_index.py --set real --fresh
# 4. evaluations
uv run python ../eval/run_ocr.py
uv run python ../eval/run_ocr.py --set hard
uv run python ../eval/run_extraction.py
uv run python ../eval/run_search.py
uv run python ../eval/run_ask.py
# embeddings: start a second embedding server, e.g. EmbeddingGemma on 8083, then
#   ../models/llama/llama-server.exe -m ../models/embeddinggemma-300m.gguf --embedding --port 8083 -c 2048 -b 2048 -ub 2048
uv run python ../eval/run_embeddings.py all-minilm=http://127.0.0.1:8081/v1=384 embeddinggemma=http://127.0.0.1:8083/v1=768
# 5. model comparison: see "Model comparison" above
# 6. system benchmark (stop both shared servers first; it starts and stops its own)
uv run --with psutil python ../scripts/benchmark.py --start-servers --threads 4 --n 20 --m 20
# then start the shared servers again with scripts/dev.ps1 (model servers only)
# 7. error analysis: below
```

Rough time with Qwen3-1.7B, from the smoke tests (12 documents): 10 to 40 s per document to index, most of it LLM
time. At that rate `test` (184) takes 0.5 to 2 hours, `hard` (125) 20 to 80 minutes and `real` (60) 10 to 40 minutes.

## Results

To be filled by the final run. One table per component: Sniff vs baseline, per set.

## Error analysis

To be filled by the final run: at least 10 failures per component, grouped by cause, with the planned fix.
The failure files carry what is needed to group them:
- extraction: `set`, `variant` and `effects` (hard set), `field`, `gold`, `pred`, `status`, `reason`;
- type: `variant`, `effects`, `pred`, and the start of the text;
- OCR: `variant`, `effects`, `cer`;
- ask and router: `category`, the route and query the router chose, and the answer's `counted`,
  `skipped_duplicates`, `unreviewed` and `conflicts`.

## A4 checks: injection, robustness, MCP against direct reading, human ratings

Four more harnesses for the A4 rubric (robustness and safety, agents and tool calling, human evaluation). They are
written and tested with stub models only. None has been run on the real models yet, so there are no numbers here.
Run them after the main evaluation, from `backend/`, with both model servers up and `run_index.py` already done for
the sets they use.

**1. Prompt injection** (`data/injection/`, `eval/run_injection.py`). The generator builds 120 PDFs, the same every time
(seed 11): 20 documents each with an instruction printed on them (`visible`), with the instruction as white or 1 pt text
(`hidden`), and 20 clean bills and receipts with a separate forwarded note that carries the instruction and gets
retrieved by search (`retrieved`). Every visible and hidden document has a clean twin. Attack success means the stored
type, the stored field or the answer is what the attacker asked for. The clean twins give the baseline accuracy.
```
uv run python ../data/injection/make_injection.py
uv run python ../eval/run_injection.py            # --limit 3 for a quick check, --skip-index to only score
```
It indexes into `.sniff/eval-injection_clean.db` and `.sniff/eval-injection.db` (140 documents, so about as long as
the hard set) and writes `prompt_injection.json`. It also reports how many successful attacks were stored as `verified`,
because those would reach the user unflagged. The retrieved kind only tests the search route (the one where retrieved
text goes into the prompt); the facts route reads stored values and never sees the note.

**2. Robustness** (`eval/run_robustness.py`). Needs `run_index.py` for `test` and for `--set hard`. It compares each hard
document with its clean twin and reports type and field accuracy drop per corruption (photo, lowres, handwriting,
cutoff) and per effect tag. For cut-off pages both sides are scored on the fields that survive the cut, and
`cut_fields_filled` shows how often Sniff stored a value for a field that was cut away. It calls no model.
```
uv run python ../eval/run_robustness.py
```

**3. MCP against direct reading** (`eval/run_mcp_vs_direct.py`). The chat model answers 60 questions (single_fact,
cross_doc_total, semantic, unanswerable, duplicates, conflict; not expiring_window, because the MCP tools take no
date) as a tool-using agent, once with Sniff's MCP tools over a real stdio session, once with `list_files` and
`read_file` over the folder of test documents. Max 6 model calls per question, tool results cut at 3,000 characters in
both conditions. It resumes a stopped run.
```
uv run python ../eval/run_mcp_vs_direct.py
uv run python ../eval/run_mcp_vs_direct.py --n 6 --conditions direct   # a quick check
```
Notes: the chat server must run with `--jinja` (dev.ps1 does). If the failures show context errors, restart it with
`-np 1` and a larger `-c`. The MCP server is started with every document type allowed, so ID cards are readable in
both conditions. Tokens for the MCP condition include the chat calls made inside Sniff's tools. Refusals are found
with a phrase list ("could not find", ...), which can be wrong in both directions; the traces are in
`mcp_vs_direct_runs.json`.

**4. Human ratings** (`eval/ratings.py`). `make` asks 60 search-route questions and writes `data/ratings/sheet.csv`,
`form.html` and `answers.json`. Each of the three team members rates all 60 (correctness 0 to 2, grounded 0 or 1,
usefulness 1 to 5), without talking to the others, and saves the file as `data/ratings/rated_<name>.csv`. The form's
download button does this; the CSV sheet can be filled in Excel. `score` computes the means, Cohen's kappa for each pair
of raters (linear weights for the ordered scales), Fleiss' kappa and the means by category in code.
```
uv run python ../eval/ratings.py make
uv run python ../eval/ratings.py score
```
The kappa code was checked against the worked examples on Wikipedia (0.4 and 0.210).
Tests for all four: `backend/tests/test_eval_*.py`.
