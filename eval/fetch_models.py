"""Resumable model downloader with sha256 checks (standard library only).

Hugging Face stalls on big files on our network, so the chat models come from the
Ollama registry (plain GGUF blobs, addressed by their sha256). EmbeddingGemma comes
from ggml-org on Hugging Face: the Ollama build of it fails to load in llama.cpp
("wrong number of tensors; expected 316, got 314").

    python eval/fetch_models.py all-minilm                    # what the app needs (46 MB)
    python eval/fetch_models.py qwen3-1.7b                    # optional: AI on this laptop (1.4 GB)
    python eval/fetch_models.py gemma3-1b embeddinggemma      # small ones
    python eval/fetch_models.py qwen3-8b                       # ~5.2 GB
    python eval/fetch_models.py --status                       # progress of every model

A stopped download resumes from the .part file (HTTP Range). The file is renamed to
models/<name> only after its sha256 matches.
"""
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
OLLAMA = "https://registry.ollama.ai/v2/library"
HF = "https://huggingface.co"

# key: (file name in models/, url, sha256, size in bytes)
MODELS = {
    # the two models the app runs on (scripts/dev.ps1)
    "qwen3-1.7b": ("qwen3-1.7b-q4_k_m.gguf",
                   f"{OLLAMA}/qwen3/blobs/sha256:3d0b790534fe4b79525fc3692950408dca41171676ed7e21db57af5c65ef6ab6",
                   "3d0b790534fe4b79525fc3692950408dca41171676ed7e21db57af5c65ef6ab6", 1359279776),
    "all-minilm": ("all-minilm-l6-v2.gguf",
                   f"{OLLAMA}/all-minilm/blobs/sha256:797b70c4edf85907fe0a49eb85811256f65fa0f7bf52166b147fd16be2be4662",
                   "797b70c4edf85907fe0a49eb85811256f65fa0f7bf52166b147fd16be2be4662", 45949216),
    # models under comparison
    "gemma3-1b": ("gemma3-1b-it-q4_k_m.gguf",
                  f"{OLLAMA}/gemma3/blobs/sha256:2f8c3d137f2496dcb4019e17a50f2974b89c1be1a742db3c80cc86f91cafeeef",
                  "2f8c3d137f2496dcb4019e17a50f2974b89c1be1a742db3c80cc86f91cafeeef", 806058400),
    "qwen3-8b": ("qwen3-8b-q4_k_m.gguf",
                 f"{OLLAMA}/qwen3/blobs/sha256:a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f",
                 "a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f", 5225374496),
    "qwen3-0.6b": ("qwen3-0.6b-q4_k_m.gguf",
                   f"{OLLAMA}/qwen3/blobs/sha256:7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa",
                   "7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa", 522640096),
    "qwen3-4b": ("qwen3-4b-q4_k_m.gguf",
                 f"{OLLAMA}/qwen3/blobs/sha256:3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f",
                 "3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f", 2497280480),
    "embeddinggemma": ("embeddinggemma-300m.gguf",
                       f"{HF}/ggml-org/embeddinggemma-300M-GGUF/resolve/main/embeddinggemma-300M-Q8_0.gguf",
                       "b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63", 333590944),
}
CHUNK = 1 << 20


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(CHUNK):
            h.update(b)
    return h.hexdigest()


def fetch(key: str) -> None:
    name, url, digest, size = MODELS[key]
    dest, part = MODELS_DIR / name, MODELS_DIR / (name + ".part")
    if dest.exists():
        print(f"{name}: already there", flush=True)
        return
    while True:
        have = part.stat().st_size if part.exists() else 0
        if have >= size:
            break
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-", "User-Agent": "sniff-fetch"})
        try:
            # timeout = stall detector: no byte for 60 s means reconnect and resume
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab") as f:
                if have and r.status != 206:
                    raise RuntimeError(f"server ignored Range (status {r.status})")
                t0, got = time.time(), 0
                while b := r.read(CHUNK):
                    f.write(b)
                    got += len(b)
                    if got % (64 * CHUNK) < CHUNK:
                        mbps = got / max(time.time() - t0, 1e-3) / 1e6
                        print(f"{name}: {(have + got) / 1e6:.0f}/{size / 1e6:.0f} MB, {mbps:.2f} MB/s", flush=True)
        except Exception as e:  # network errors: wait and resume
            print(f"{name}: {type(e).__name__}: {e}; retrying in 10 s", flush=True)
            time.sleep(10)
    got = sha256(part)
    if got != digest:
        part.rename(part.with_suffix(".bad"))
        raise SystemExit(f"{name}: sha256 mismatch ({got}); kept as .bad, run again to restart")
    part.rename(dest)
    print(f"{name}: done, sha256 ok", flush=True)


def status() -> None:
    out = {}
    for key, (name, _, _, size) in MODELS.items():
        dest, part = MODELS_DIR / name, MODELS_DIR / (name + ".part")
        out[key] = "done" if dest.exists() else (
            f"{part.stat().st_size / size:.0%} of {size / 1e9:.2f} GB" if part.exists() else "not started")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args == ["--status"]:
        status()
    else:
        for k in args:
            fetch(k)
