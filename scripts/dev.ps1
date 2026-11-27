# Start everything for local development on Windows: the embedding server, the API and the UI. The API starts the chat
# model server (port 8080) itself when a model call needs it, and stops it after 120 s idle (SNIFF_LLM_IDLE).
# Usage (from the repo root):  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
$root = Split-Path -Parent $PSScriptRoot
$m = Join-Path $root "models"
$server = Join-Path $m "llama\llama-server.exe"

# all-MiniLM (384 dims, config.EMBED_DIM): Ollama's EmbeddingGemma GGUF does not load in llama.cpp (2 tensors missing)
Start-Process -NoNewWindow $server "-m `"$m\all-minilm-l6-v2.gguf`" --embedding --port 8081 -c 2048 -b 2048 -ub 2048"
Start-Process -NoNewWindow -WorkingDirectory (Join-Path $root "backend") "uv" "run uvicorn sniff.api:app --port 8000"
Start-Process -NoNewWindow -WorkingDirectory (Join-Path $root "frontend") "npm.cmd" "run dev"
Write-Host "Embeddings on 8081, chat model on 8080 when needed, API on http://127.0.0.1:8000, UI on http://localhost:5173"
