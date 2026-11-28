# Evaluation

How we test each AI part of Sniff: the test sets, the metrics, the baseline each part is compared with, and the
exact commands. It answers feedback points 1 (8 GB laptop), 2 (stronger reference model), 8 (router on its own),
12 (realistic and poor-quality documents) and 13 (cross-document questions).

**Status:** the final run on the `test`, `hard` and `real` sets was on 28 Nov 2026; its numbers are in
[Results](#results) and [Error analysis](#error-analysis). Anything that was not run says so. The model comparison
(Gemma 3 1B, Qwen3-8B), the embedding comparison and the A4 checks below have not been run yet.

Rules we keep:
- Test documents never appear in prompts or few-shot examples. The generator uses different seeds and people for `dev` and `test`.
- No LLM does arithmetic or date maths. The evaluation compares the numbers code computed.
- Every script writes `eval/results/<component>.json` (shown on the app's Evaluation page) and
  `<component>_failures.json` for error analysis.

## Test sets

| Set | Where | Size | What it is | Labels |
|---|---|---|---|---|
| `test` | `data/synth/out/test` | 378: receipt 80, utility_bill 41, certificate 28, bank_statement 24, and 22 or 23 of each other type (id_card, passport, transcript, warranty_card, offer_letter, insurance_policy, tenancy_agreement, fee_voucher, other). 168 text PDFs, 80 scans, 130 photos | Synthetic documents of all 12 types plus `other` files | From the generator, `data/synth/out/labels.jsonl` |
| `dev` | `data/synth/out/dev` | 72 | Same kinds, other seeds and people. The only set used for prompt changes | Same |
| `hard` | `data/hard/out/hard` | 155 (60 photo, 45 low-res, 30 cut-off, 20 handwriting), all 13 types; 109 fields set to `null` by cuts | Harder copies of `test` documents, made by `data/hard/make_hard.py` (seed 7) | Copied from the source document; cut-off fields set to `null` |
| `real` | `data/real/out/real/sroie` | 60 | Real scanned shop receipts from SROIE (ICDAR 2019), human labelled | Company, date, total from SROIE, converted by `data/real/fetch_sroie.py` |
| `own` | `data/own/<member>` | optional | Team members' own documents, personal numbers blurred; never committed | Hand-written, see `data/own/README.md` |
| ask | `data/eval/ask.jsonl` | 261: single_fact 92, cross_doc_total 62, semantic 54, expiring_window 18, duplicates 13, conflict 12, unanswerable 10 | Questions with gold answers and sources, each with a `category` | From the generator's code |
| search | `data/eval/search.jsonl` | 78 | Queries with the relevant files | From the generator |
| injection | `data/injection/out` | 120 files: 20 visible, 20 hidden and 20 retrieved attacks, each with a clean twin; 20 questions | Prompt-injection set, see the A4 section | From `data/injection/make_injection.py` (seed 11) |

The `duplicates`, `conflict` and `unanswerable` categories are small (13, 12 and 10 questions), so their per-category
numbers show whether the feature works on those cases; they are not a precise rate.

Each set gets its own database (`.sniff/eval.db` for `test`, `.sniff/eval-hard.db`, `.sniff/eval-real.db`), so
hard copies are never counted as near-duplicates of test documents in the ask totals.

**Hard set details.** Each source document is used once. The variants:
- `photo`: perspective, rotation up to 8 degrees, blur, JPEG quality 18 to 40, and sometimes a shadow or low warm light. The effects used are stored per document (`effects`), so failures can be grouped by effect.
- `lowres`: a grey scan at about 60 to 80 dpi inside a PDF.
- `handwriting`: a receipt or warranty card written out in a handwriting font (Ink Free, Bradley Hand or Segoe Script; Windows fonts).
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
| Ask | ask.jsonl | Answer correctness, citation correctness, abstention on unanswerable, false abstention, latency; all by category | Plain RAG (top-3 passages + LLM, no facts table) | `run_ask.py` |
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

Every runner that can take more than a few minutes (`run_index`, `run_extraction`, `run_ocr`, `run_ask`,
`run_embeddings`, `run_injection`, `run_mcp_vs_direct`, `ratings.py make` and `scripts/benchmark.py`) calls
`common.stay_awake()`, which asks Windows not to sleep until the script exits. This does not help when the lid is closed:
a closed lid still puts the laptop to sleep, so keep it open (or set "When I close the lid" to "Do nothing" on mains power).

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

## Fast indexing (dev only)

`eval/run_fastindex.py` measures the deterministic first pass on the 72 dev documents. `prepare` reads and embeds
each document once into `.sniff/fast-dev.db`; the other steps reuse that copy. Nothing here touches the test split.

| Step | Result on dev | Command |
|---|---|---|
| Fields from rules alone (right type given) | accuracy 0.932 over 429 fields, filled 0.841, precision 0.997, 32 ms per document | `rules --split dev` |
| Type classifier (rules + logistic regression on MiniLM) | answers 65 of 72 (abstains on 9.7%), all 65 right; classifier alone 0.986, keyword rules alone 0.917 | `train-types`, `types --split dev` |
| Candidate-line window (label row + row below) | 99.7% of the 382 gold values on the page are inside their window; the window is 22% of the text | `window --split dev` |

The classifier is trained on the training split only (`generate.py --splits train --seed 11`, 144 documents; 61 of
them were embedded from the generator's text instead of OCR to save time). Its threshold is the lowest at which
5-fold out-of-fold decisions on the training split were at least 99% right (0.30). Rules alone on the training split
score 0.860 with precision 0.951; most wrong values there come from the 61 documents without layout, where two
columns merge into one line.

**With AI off, a guessed type** (`guess --split dev`, `run --split dev --mode off`). On the 7 dev documents the
classifier abstains on, its top guess is right for 6. The wrong one, a photo of a note read as a certificate, had
p = 0.254. Guesses at a floor of 0.3, 0.4 or 0.5 are 6 of 6 right; at 0.6, 5 of 5; at 0.7, 3 of 3. When no model
can sort a document (AI off, a provider off this laptop, or a failed call), Sniff now takes the top guess at 0.50 or
above (`typer.GUESS_FLOOR`). Its fields come from the rules only, and every fact goes to review with the reason
"type guessed". Until a person approves or corrects one of those facts, agents and AI providers treat the document as
unclassified (`files.type_guessed`). Below the floor the document stays unclassified. With AI off on dev, type accuracy goes from 0.917 to
1.000 and fields from 0.879 to 0.932 (filled 0.788 to 0.841, precision 0.997 both). 23 of 361 facts, from 6
documents, wait in review. A second check used the dev records rendered again with another seed
(`generate.py --splits dev --seed 8`, prepared into its own database, not committed). There the classifier abstained
on 6, and its top guess was right for 5; the wrong one was again a note, at p = 0.268. At 0.50 it made 3 guesses,
all right, and left two right guesses (p 0.32 and 0.33) unclassified: type 0.931 to 0.972, fields 0.830 to 0.853.
Dev allowed any floor from 0.3 to 0.5; 0.5 keeps clear of the wrong guesses at 0.25 to 0.27. These are only 13
abstained documents. The test split was not used.

**Phase 2 end to end** (`run --mode today|fast`, 72 dev documents, through the real `read_facts`; seconds are the
whole phase 2 per document, model calls included):

| Pipeline (Qwen3 Q4_K_M on the CPU) | Type | Fields | Seconds per document: mean / median / p95 | No model call | Model calls per document |
|---|---|---|---|---|---|
| Before: the model types and reads every field from the whole document (1.7B) | 1.000 | 0.904 | 29.7 / 28.0 / 48.8 | 0% | 1.94 |
| Rules + classifier first, model on the window for gaps (1.7B) | 1.000 | 0.963 | 5.5 / 0.1 / 28.1 | 78% | 0.26 |
| The same with Qwen3-0.6B | 0.986 | 0.960 | 2.5 / 0.1 / 12.5 | 78% | 0.33 |

Conditions: 27 and 28 Nov 2026, i5-8250U, one chat server per run started on demand on its own port. Another agent's
1.7B server on 8080 shared the CPU and took occasional questions. The two fast runs were on mains power. In the
"before" run 41 of 72 documents ran on battery; the 31 that ran on mains averaged 31.3 s (median 29.5 s), so it
was not slower on battery. Cold start of an unloaded model, from the first call: 1.7B ready in 13.0 s and first
answer in 37.8 s (6.8 s to ready on the next start, file in the disk cache); 0.6B ready in 3.2 s, first answer
12.5 s. Averaged over all 72 documents, the fast run sent 77 new prompt tokens per document to the model,
against 553 before.

The fields score rose because the rules only keep a value whose own label is next to it. Of the old pipeline's
41 field errors, 24 were a wrong value (often a neighbouring amount, such as the late-payment total as the amount
due) and 13 filled a field the document does not print (10 times a receipt's subtotal with its total). The 0.6B run got one more type wrong (a photo of a note read as an offer
letter), one ID wrong and one certificate title missing; 1.7B missed one receipt date that 0.6B read. Qwen3-0.6B
with no output cap once wrote 1,300 tokens into one value, so replies are now capped at 40 tokens per field.

Default: Qwen3-1.7B stays. 0.6B passes the bar for indexing (fields within 2 points of before; in fact above),
but the same chat server also writes answers, and answer quality with 0.6B was not measured. Switching is one
setting (`SNIFF_LLM_FILE=qwen3-0.6b-q4_k_m.gguf`, file from `eval/fetch_models.py qwen3-0.6b`) once answers are
checked on `data/eval/ask_dev.jsonl`. Its memory while loaded was not measured (with `-c 8192` most of it is the
KV cache, not the weights).

**If a fine-tuned 0.6B model is wanted later** (not started; it needs the coordinator's go):
- Data: the training split, grown with more households in `TRAIN_NAMES` to 500 to 1,000 documents, some read
  through OCR. Input: the candidate-line window for the fields the rules missed. Target: the value as printed,
  which the rules' matching code can recover from the gold value (`verify.needles`).
- Time on this laptop: not measured. Arithmetic only: about 6 x 0.6 billion x 600,000 tokens (1,000 examples, 200
  tokens, 3 epochs) is 2 x 10^15 operations, which at an assumed 50 to 100 GFLOP/s is 6 to 12 hours, and fp32
  weights alone take 2.4 GB of the 5 to 6 GB headroom. A free Colab T4 GPU is the practical place to train.
- After training: merge the adapter, convert to GGUF Q4_K_M with llama.cpp's scripts, and run `run --mode fast`
  on dev again.
- The rules already read 360 of the 389 printed fields on dev, so the model has little left to do and this is low
  priority.

**Memory** (`eval/backend_memory.py`, results in `eval/results/backend_memory.json`; MB of private bytes / working set):

| Back end | Before (main 353f647) | After |
|---|---|---|
| Idle, 30 s after start | 327 / 110 | 62 / 75 |
| Peak while indexing 10 dev documents (8 need OCR) | not measured | 645 / 672, plus the chat server 1,758 / 2,814 while it was up |
| 150 s after indexing | | 177 / 203; the chat server had stopped itself |

Most of the idle saving is one OpenBLAS thread: numpy reserved about 230 MB of commit for per-core buffers. The
PDF, Word and PowerPoint readers now load on first use. The OCR models (about 55 MB) stay for 5 minutes after the
last image, which is why the back end holds 177 MB right after indexing.

## Results

Final run on 28 Nov 2026, `main` at `33ab7ba`, on the `test` split (used for scoring only: no prompt or rule was
changed after seeing it), plus `hard` and `real`.

**Every number here is with AI off**: no language model at all, as Sniff starts on a new install. Types come from
the keyword rules and the logistic-regression classifier on MiniLM embeddings (an unsure type is guessed at p >= 0.5
and its facts go to review), fields from the label rules and the verifier, search from BM25 and embeddings, and every
total, count and date from code. Qwen3-1.7B is no longer the local model, so no model run is reported.

Machine: i5-8250U (4 cores, 8 threads), 32 GB RAM, Windows 11; all-MiniLM-L6-v2 on its own llama-server for
embeddings. Power: battery (40%) when the `test` index started at 11:36, mains from about 11:45 to the end. The `test`
index shared the CPU with another index run, so its per-document times are slower than one run alone. The latency
table comes from the system benchmark, which also shared the CPU (see there).

To run it again, from `backend/`, with only the embedding server up:
```
SNIFF_AI=off uv run python ../eval/run_index.py --fresh        # and --set hard --fresh, --set real --fresh
SNIFF_AI=off uv run python ../eval/run_extraction.py --sets test hard real
SNIFF_AI=off uv run python ../eval/run_search.py
SNIFF_AI=off uv run python ../eval/run_ask.py
uv run python ../eval/run_ocr.py                               # and --set hard
SNIFF_AI=off uv run --with psutil python ../scripts/benchmark.py --n 20 --m 20
SNIFF_AI=off uv run python ../eval/error_groups.py
```
This run built the three databases with `SNIFF_AI=off run_extraction.py --compare ai-off=<url> --sets ...`, which
indexes through the same `scan.index_file` into `.sniff/eval[-set]-ai-off.db`, and copied them to the names above
before scoring. With `SNIFF_AI=off` the scripts label their rows "AI off", and `run_ask.py` leaves out the plain-RAG
baseline, which needs a model.

### Document type

| Set | n | Sniff, AI off | Keyword rules (baseline) |
|---|---|---|---|
| `test` | 378 | 0.926 (macro-F1 0.918) | **0.955** (0.942) |
| `hard` | 155 | 0.600 (0.643) | **0.819** (0.830) |
| `real` (SROIE receipts) | 60 | 0.450 | **1.000** |

Accuracy, macro-F1 in brackets. On `real` every document is a receipt, so macro-F1 says nothing there.

The keyword rules win on all three sets. Sniff with AI off is never confidently wrong on `test`: all 28 wrong types
are documents it left as `other`, because the classifier was unsure and its top guess was under 0.5 (8 transcripts,
5 offer letters, 5 warranty cards, 5 fee vouchers, 3 bank statements, 1 bill, 1 ID card). On `hard`, 58 of the 62
wrong are `other` too and 4 are called receipts (by variant the 62 are photos 24, low-res 18, cut-off 12,
handwriting 8); on `real` all 33 are `other`. The keyword rules almost always pick a type, and on these sets a forced guess is
right more often than the classifier's abstention. An abstained document still gets text search; it gets no facts.

### Fact extraction

| Set | Documents / fields | Sniff, AI off | Regex rules (baseline) |
|---|---|---|---|
| `test` | 356 / 2,334 | **0.849** | 0.311 |
| `hard` | 143 / 917 | **0.302** | 0.188 |
| `real` | 60 / 180 | 0.222 | 0.222 |

Field accuracy: a field of a wrongly typed document counts as wrong, and a `null` label must stay empty.

| | `test` | `hard` | `real` |
|---|---|---|---|
| Schema-valid rate | 1.000 | 1.000 | 1.000 |
| Verified precision | 0.983 | 0.894 | 0.619 |
| Verified recall | 0.946 | 0.925 | 0.975 |
| Needs-review rate | 0.055 | 0.091 | 0.045 |

With no model there is no JSON to break, so schema-valid is 1.000 by construction. Half of the `test` errors (176 of
353) are fields of documents left as `other`. On `real`, 99 of the 140 wrong fields belong to receipts left as `other`,
15 were not found on typed receipts, and 26 are wrong values: 14 merchants taken from another line (a fax number, a
GST number, a footer) and 12 totals that are another amount on the receipt. 24 of those 26 were marked `verified`,
which is why verified precision on `real` is 0.619.

### Search

78 queries from `search.jsonl` on the `test` index.

| System | Recall@5 (capped) | MRR |
|---|---|---|
| Sniff (hybrid BM25 + embeddings, RRF) | 0.761 | 0.788 |
| Embeddings only | **0.783** | **0.805** |
| Keyword only (BM25) | 0.615 | 0.639 |
| File-name search (baseline) | 0.005 | 0.009 |

Embeddings alone beat the hybrid on this set by 2 points. Per query, embeddings alone do better on 13 and the hybrid
on 11. Embeddings win on a person's document or a kind of shop ("Ali's national identity card", "Ali's MBA", "book
store purchase"): BM25 ranks documents that share a word (other people's ID cards, transcripts, warranty cards that
say "purchase") and the fusion puts them above the right one. The hybrid wins on a bill for a named month ("August
internet bill", "natural gas payment for January"), which embeddings cannot tell apart. Latency over the 78 queries:
p50 33 ms, p95 205 ms.

### Router and ask

All 261 questions of `ask.jsonl` on the `test` index. With AI off the router is rules only, the facts route answers in
code, and a question that needs a written answer gets the documents that look related instead ("A written answer needs
AI"). The plain-RAG baseline needs a model to write its answer, so it was not run; the router is compared with the
keyword rules.

| Router | Tool-selection accuracy | Facts questions sent to facts | Search questions sent to search |
|---|---|---|---|
| Sniff rules (AI off) | 0.778 | **0.939** | 0.281 |
| Keyword rules (baseline) | **0.782** | 0.863 | **0.531** |

| Ask, AI off | Value |
|---|---|
| Answer correct | 0.406 |
| Citation correct (a gold source among the files shown) | 0.797 |
| Abstention on unanswerable questions | 0.600 (6 of 10) |
| False abstention on answerable questions | 0.167 |
| Latency, end to end | p50 76 ms, p95 384 ms |

By category (cross-document categories first, feedback point 13):

| Category | n | Correct | Citation correct | Router accuracy | Keyword router |
|---|---|---|---|---|---|
| cross_doc_total | 62 | 0.419 | 0.839 | 0.984 | 1.000 |
| expiring_window | 18 | 0.167 | 0.722 | 1.000 | 1.000 |
| duplicates | 13 | 0.154 | 1.000 | 1.000 | 1.000 |
| conflict | 12 | 0.500 (conflict shown in 0.333) | 0.833 | 0.833 | 0.750 |
| single_fact | 92 | 0.652 | 0.804 | 0.902 | 0.739 |
| semantic | 54 | 0.056 | 0.704 | 0.241 | 0.593 |
| unanswerable | 10 | 0.600 | n/a | 0.500 | 0.200 |

`semantic` questions need a written answer, which AI off cannot give, so their correct rate is near zero by design;
the right document is among the ones shown for 70% of them. The cross-document totals route correctly almost every
time (0.984), so their errors come from the facts. The one we traced: "How much did we pay for electricity in 2026?"
gave 92,695.32 from 11 documents against 98,766.32 from 12. The March bill (6,071) is missing from the sum; one of its
two copies, a scan, has no billing month read. The `duplicates`, `conflict` and `unanswerable`
categories are small (13, 12 and 10), so they show whether the feature works, not a precise rate.

### OCR

| Set | n | RapidOCR (Sniff) | Tesseract 5.4 defaults (baseline) |
|---|---|---|---|
| `test` scans and photos | 210 (80 scans, 130 photos) | **0.207** | 0.412 |
| `hard` (photos, low-res, cut-off, handwriting) | 155 | **0.430** | 0.650 |

Character error rate (lower is better), against the generator's text. OCR does not depend on the AI setting. The CER
counts more than misread letters: the generator's text has dot leaders ("Account.............") and `====` rules
that OCR reads as one dot or skips, OCR often drops the space between words ("DearAuntHira"), and a whole line can be
missing. So 0.207 overstates what search and the rules lose. On `hard`, cut-off pages are scored against the whole
page's text, so the part that was cut away counts as errors too. RapidOCR per page: p50 6.4 s, p95 16.5 s on `test`,
p50 6.3 s, p95 13.5 s on `hard`, both with the CPU shared.

### Latency, memory and cost

System benchmark (`scripts/benchmark.py`, AI off, attached to the running embedding server, mains): 20 `test`
documents (seed 7) indexed into a fresh database, then 20 questions. **Another job's OCR benchmark started on the same
CPU 4 seconds before this one** (found afterwards), so the OCR and indexing times below are upper bounds; a run on an
idle laptop is still to do. Latency per call:

| Stage | Calls | p50 | p95 |
|---|---|---|---|
| Read a file (no OCR) | 20 | 16 ms | 3.7 s |
| OCR, per image or scanned page | 10 | 12.6 s | 18.7 s |
| Embed, per file | 20 | 0.30 s | 1.04 s |
| Fields (rules and verifier) | 19 | 145 ms | 227 ms |
| Index one file, end to end | 20 | 6.7 s | 19.8 s |
| Route (rules) | 3 | 9 ms | 37 ms |
| Search, inside an answer | 3 | 213 ms | 489 ms |
| Ask, end to end | 20 | 5 ms | 229 ms |

The 10 OCR calls include the first, which also loads the OCR models (5.9 s for loading and the first page in
`backend_memory.json`). For comparison, OCR in the `test` OCR run took p50 6.4 s per page. From the full runs: ask over
261 questions p50 76 ms and p95 384 ms (an identical run a few minutes earlier, before the other job started: 81 ms
and 415 ms, so asking is not CPU-bound); search over 78 queries p50 33 ms and p95 205 ms (before the other job);
indexing the 378 `test` documents p50 6.3 s and p95 28.6 s per document (with the CPU shared). There are no model calls
with AI off.

Memory, peak working set / private bytes (commit), MB, sampled every 0.5 s. AI off runs no chat server.

| Phase | Back end | Embedding server | Total | Within 4.5 GB |
|---|---|---|---|---|
| Idle (a real `uvicorn` back end, 30 s after start, empty index) | 121 / 108 | 57 / 77 | 178 / 185 | yes |
| Indexing the 378 `test` documents | 959 / 1,067 | 65 / 77 | 1,019 / 1,144 | yes |
| Benchmark: 20 documents, then 20 questions | 750 / 724 | 57 / 77 | 807 / 801 | yes |
| Answering the 261 questions | 116 / 98 | 51 / 77 | 167 / 175 | yes |

Indexing is the peak. The indexing row misses the first 25 or so documents, which ran before the sampler started. The 4.5 GB budget is what an 8 GB laptop leaves for apps after Windows; this laptop has 32 GB, so
the totals are compared with the budget, not measured on an 8 GB machine.

Cost per request: **$0**. With AI off no request calls a model, local or remote. Electricity is not counted.

## Error analysis

Every failure in `eval/results/*_failures.json` is put in one group by fixed code rules (`eval/error_groups.py`,
written to `error_groups.json`); the counts below are its output for the final run (AI off). The examples and the
causes inside the big groups come from reading the failures and the indexed text. The fixes are planned, not made;
each will be tried on `dev`, never on `test`.

**Document type** (123 failures: `test` 28, `hard` 62, `real` 33)

| Cause | Count | Example | Planned fix |
|---|---|---|---|
| A typed document left as `other` (the classifier abstained, top guess under 0.5) | 119 | 8 transcripts and 5 offer letters on `test`; 33 SROIE receipts on `real` | Train the classifier on OCR text of photos and receipts too (61 of its 144 training documents were embedded from clean generator text); when it abstains, take the keyword rules' type if one keyword set clearly wins |
| Confused with another type | 4 | on `hard`: a cut-off bill, two handwritten warranty cards and a photo of an offer letter called receipts | Find which signal made them receipts (a type rule or the classifier) and tighten it, on `dev` |

**Fact extraction** (1,133 failures: `test` 353, `hard` 640, `real` 140)

| Cause | Count (`test` / `hard` / `real`) | Example | Planned fix |
|---|---|---|---|
| Document type was wrong, so no fields | 651 (176 / 376 / 99) | a scanned internet bill left as `other` | The type fixes above |
| Value missed | 399 (144 / 240 / 15) | receipt tax 23 times on `test`: 16 receipts print the rate in the label ("GST 17% Rs. 30,974", OCR "GST17% 11,390/-"), on 7 the tax line is not in the OCR text | Accept "GST <rate>%" and "Sales Tax <rate>%" as tax labels and take the amount after the rate |
| | | passport birth and issue dates (16): the label is misread ("Date olBirth", "Bith") | Read birth date, expiry and number from the passport's machine-readable lines (fixed positions, check digits) |
| | | bill payment status (7): a stamp "\*\*\*PAID\*\*\*Paidon10/11/2026" | A rule for a PAID stamp line |
| | | `hard`: photo 125, low-res 91, cut-off 14, handwriting 10 | Upscale pages under 150 dpi before OCR |
| Wrong value | 52 | a receipt's merchant stored as "Goods once sold will not be ex..." (`verified`); SROIE merchants taken from the fax or GST line | Merchant: the first line in large type, never a line with FAX, TEL, GST, a policy sentence or a long number |
| Text partly right | 25 | institution "Training Center" for "Lifeline Training Center" (`verified`) | Keep the whole printed line next to the label, not the words after the first match |
| Value for a field the document does not have | 5 | `hard`, cut-off: a merchant read from a warranty sentence when the shop name was cut away (`verified`) | Do not store a value whose own label was not found |
| Date read wrong | 1 | `hard`, low-res: payment date 2026-05-06 for 2026-05-08 (`verified`) | Upscaling, as above |

On `test`, 29 of the 33 wrong values were marked `verified`: certificate institutions cut short (7), tenancy property
addresses (5), receipt tax stored as the rate ("16.0" for a tax of 90.08; 3), security deposits (3) and 11 others.
These fixes come first, because a wrong `verified` value reaches the user unflagged.

**OCR, `test`** (145 of 210 pages with CER over 0.1)

| Cause | Count | Example | Planned fix |
|---|---|---|---|
| Phone photo | 95 | a photo of a letter, CER 0.378: spaces lost ("DearAuntHira") and one line missing | Straighten and sharpen photos before OCR; split glued words with the document's own word list |
| Printed scan misread | 49 | a scanned bill, CER 0.36: dot leaders and `====` rules read as one dot | Render scans at 300 dpi instead of 200; score CER without leader and rule characters, so the number shows real misreads |
| Spaces only | 1 | an ID card photo, CER 0.104, under 0.05 once spaces are ignored | As above |

**OCR, `hard`** (141 of 155 pages with CER over 0.1: photo 59, low-res 44, cut-off 21, handwriting 17)

| Cause | Count | Example | Planned fix |
|---|---|---|---|
| Phone photo | 59 | an insurance policy photo with perspective, rotation, blur and JPEG damage, CER 0.108 | Straighten and sharpen photos before OCR |
| Low-resolution scan | 44 | a 60 to 80 dpi scan, CER 0.563 | Upscale pages under 150 dpi two times before OCR |
| Printed page misread (cut-off pages) | 21 | a receipt with its top cut off, CER 0.331; the missing part counts as errors | Score cut-off pages on the part that is on the page |
| Handwriting | 9 | a handwritten warranty card, CER 0.577 | Out of scope for RapidOCR's printed-text models: send handwritten pages to review |
| Spaces only | 8 | a handwritten warranty card, CER 0.149, under 0.05 once spaces are ignored | Split glued words with the document's own word list |

**Search, hybrid** (32 of 78 queries below full recall)

| Cause | Count | Example | Planned fix |
|---|---|---|---|
| Some of several relevant documents found | 23 | "power bill March 2026": 1 of the 2 relevant bills, at rank 4 | List questions with many relevant files belong to the facts route, which reads the facts table, not the top 5 |
| Query words in the document, but it ranked lower | 8 | "gas bill June 2026" | Index the document type and the billing month as words, so BM25 can match them |
| No query word in the document | 1 | "air conditioner invoice": both receipts say "Split AC 1.5 Ton" | Index the type and its synonyms with each chunk |

Separately, the 13 queries where embeddings alone beat the hybrid (see Results) point at the fusion: try a lower BM25
weight, on `dev`.

**Router, rules only** (58 of 261)

| Cause | Count | Example | Planned fix |
|---|---|---|---|
| Search question sent to facts | 46 | "What institution issued Sara's degree?" (`semantic`) | A question that asks "what" or "which" for a name or an institution wants text: send it to search even when it has a date word such as "issued" (this one was answered with a date) |
| Facts question sent to search | 12 | "What is Ali's date of birth?" (`single_fact`) | Add the missed question forms to the rules, taken from `dev` questions |

**Ask** (155 of 261)

| Cause | Count | Example | Planned fix |
|---|---|---|---|
| Facts route, wrong result | 76 (cross_doc_total 32, single_fact 17, expiring_window 14, duplicates 11, conflict 2) | "How much did we pay for electricity in 2026?": 92,695.32 from 11 documents for 98,766.32 from 12 | Mostly missing facts upstream (the type and field fixes above); also list the documents that match the filter but lack the field, so the user sees what is left out |
| Refused an answerable question | 42 (semantic 18, single_fact 15, cross_doc_total 4, conflict 4, expiring_window 1) | "How much was the electricity bill for March 2026?": "No matching documents found" | When the facts query matches nothing, show the search results instead of only "not found" |
| Wrong route | 33 (all semantic) | "What institution issued Sara's degree?" answered with a date | The router fixes above |
| Answered a question that has no answer | 4 | "When does my gym membership expire?" listed the expiry dates of other documents | When the question names a thing no document type covers ("gym membership", "visa"), refuse instead of dropping the type filter |

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
