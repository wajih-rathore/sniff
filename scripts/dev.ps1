# Start everything for local development on Windows: two model servers, the API and the UI.
# Usage (from the repo root):  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
$root = Split-Path -Parent $PSScriptRoot
$m = Join-Path $root "models"
$server = Join-Path $m "llama\llama-server.exe"

Start-Process -NoNewWindow $server "-m `"$m\qwen3-1.7b-q4_k_m.gguf`" --port 8080 --jinja --reasoning-budget 0 -c 8192"
# all-MiniLM (384 dims, config.EMBED_DIM): Ollama's EmbeddingGemma GGUF does not load in llama.cpp (2 tensors missing)
Start-Process -NoNewWindow $server "-m `"$m\all-minilm-l6-v2.gguf`" --embedding --port 8081 -c 2048 -b 2048 -ub 2048"
Start-Process -NoNewWindow -WorkingDirectory (Join-Path $root "backend") "uv" "run uvicorn sniff.api:app --port 8000"
Start-Process -NoNewWindow -WorkingDirectory (Join-Path $root "frontend") "npm.cmd" "run dev"
Write-Host "Model servers on 8080/8081, API on http://127.0.0.1:8000, UI on http://localhost:5173"
