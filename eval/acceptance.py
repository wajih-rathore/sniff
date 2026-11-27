"""Timed acceptance test of every Sniff use case, against a real back end. The results are in docs/TESTING.md.

It tests and measures; it fixes nothing. Everything goes through the HTTP API, a real MCP client over stdio and the
real launcher build in headless Chromium, so the same script can be run again after fixes to show before and after.

Run from backend/ (any venv with the back end's dependencies):
    uv run python ../eval/acceptance.py docs  --docs <folder> --synth <data/synth/out> --real <data/real/out>
    uv run python ../eval/acceptance.py copy  --installed "%LOCALAPPDATA%\\Sniff\\backend" --to <dir> --port 8790
    uv run python ../eval/acceptance.py start --exe <dir>\\sniff-backend.exe --home <dir> --port 8790
    uv run python ../eval/acceptance.py run   --url http://127.0.0.1:8790 --docs <folder> --home <dir> --frontend <dist>
    uv run python ../eval/acceptance.py stop  --home <dir>

- `docs` builds the test folder (the dev set, a few real receipts and planted cases) and truth.json beside it.
- `copy` is needed because the installed back end always listens on 8765 (desktop.py), where the user's own Sniff
  runs. It copies the installed back end and changes only that port number inside the copy.
- `start` times start-up, and puts a small proxy between the back end and the model servers (8080, 8081) so model
  calls and model time can be counted. The back end is otherwise started the way the desktop app starts it.
- `run --steps index,facts,...` runs some steps only. Each step replaces its own section of eval/results/acceptance.json.
"""
import argparse
import asyncio
import http.client
import json
import os
import random
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import zlib
from collections import Counter
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results" / "acceptance.json"
PROXY = {18080: 8080, 18081: 8081}  # proxy port -> model server port (chat, embeddings)
TARGETS = {"search_ms": 100, "new_file_s": 2, "fact_answer_s": 2, "first_word_s": 3, "memory_gb": 4.5}
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB, so the shell that runs `start` can exit
STEPS = ["index", "search", "ui", "reminders", "overview", "ask", "mcp", "facts", "changes", "badinput"]  # facts and later steps change the index

# planted documents (paths inside the test folder)
GAS_SEP, GAS_OCT = "Bills/Metro Gas Sep 2026.pdf", "Bills/Metro Gas Oct 2026.pdf"
GAS_OCT_PHOTO, GAS_SEP_COPY = "WhatsApp Images/IMG-20261105-WA0412.jpg", "Backup/Metro Gas Sep 2026.pdf"
CNIC = "Documents/Zainab CNIC copy.pdf"
DOCX, PPTX = "Documents/Offer - Riverside Analytics.docx", "Documents/Solar PV certificate.pptx"
LICENCE = "Documents/old/Screenshot 2021-09-14 at 14.29.27.png"
TENANCIES = ["Desktop/CamScanner 03-01-2024 17.45.pdf", "Desktop/Document (4).pdf", "Documents/old/Print_2025-03-01.pdf",
             "WhatsApp Images/IMG-20250903-WA0341.jpg"]


# ---------------------------------------------------------------- small helpers
def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None


def stats(xs, nd=3):
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "p50": round(pct(xs, 50), nd), "p95": round(pct(xs, 95), nd), "min": round(min(xs), nd),
            "max": round(max(xs), nd)} if xs else {"n": 0}


def same_value(pred, gold) -> bool:
    """As eval/common.py: numbers within 0.01; text ignores case, spaces and punctuation."""
    if pred is None or gold is None:
        return pred is None and gold is None
    try:
        return abs(float(pred) - float(gold)) < 0.01
    except (TypeError, ValueError):
        return re.sub(r"[\W_]+", "", str(pred).lower()) == re.sub(r"[\W_]+", "", str(gold).lower())


def numbers(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "") if n.replace(",", "").replace(".", "").isdigit()]


def date_forms(iso: str) -> list[str]:
    d = date.fromisoformat(iso)
    return [iso, f"{d.day} {d:%b %Y}", f"{d.day} {d:%B %Y}", f"{d:%B} {d.day}, {d.year}", f"{d:%d/%m/%Y}", f"{d:%d.%m.%Y}",
            f"{d:%d %b %Y}", f"{d:%d %B %Y}"]


def has_date(text: str, iso: str) -> bool:
    low = (text or "").lower()
    return any(f.lower() in low for f in date_forms(iso))


def save(section: str, data: dict) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    allr = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    allr[section] = data
    RESULTS.write_text(json.dumps(allr, indent=1, default=str), encoding="utf-8")
    print(f"[{section}] saved", flush=True)


def conditions() -> dict:
    """Mains power (Win32_Battery.BatteryStatus 2), builds running, CPU load and free commit, noted beside each timing."""
    ps = ("$b=(Get-CimInstance Win32_Battery).BatteryStatus;"
          "$l=(Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average;"
          "$p=@(Get-CimInstance Win32_Process | ?{ $_.ProcessId -ne $PID -and ($_.Name -match '^(cargo|rustc|pyinstaller|docker|msbuild)\\.exe$'"
          " -or $_.CommandLine -match 'vite build|npm run build|pyinstaller|cargo build|tauri build') } | %{ $_.Name });"
          "$f=(Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory;"
          "ConvertTo-Json -Compress @{battery=$b; cpu=$l; builds=$p; free_commit_kb=$f}")
    try:
        c = json.loads(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout)
    except Exception as e:
        return {"error": str(e)}
    import ctypes

    class Power(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("pct", ctypes.c_ubyte), ("saver", ctypes.c_ubyte),
                    ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]
    p = Power()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p))
    c.update(ac_line=p.ac, charge=p.pct, mains=c.get("battery") == 2 and p.ac == 1, at=time.strftime("%Y-%m-%d %H:%M:%S"))
    return c


def pid_on(port: int) -> int | None:
    for c in psutil.net_connections("tcp"):
        if c.status == psutil.CONN_LISTEN and c.laddr.port == port and c.pid:
            return c.pid
    return None


def kill_tree(pid: int) -> None:
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    for x in p.children(recursive=True) + [p]:
        try:
            x.kill()
        except psutil.NoSuchProcess:
            pass


def spawn(cmd, env=None, cwd=None, log=None):
    out = open(log, "ab") if log else subprocess.DEVNULL
    try:
        return subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                creationflags=DETACHED | BREAKAWAY)
    except OSError:  # the job this shell runs in does not allow breakaway
        return subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, creationflags=DETACHED)


def child_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SNIFF_")}
    env.update(SNIFF_HOME=str(home), SNIFF_LLM_URL="http://127.0.0.1:18080/v1", SNIFF_EMBED_URL="http://127.0.0.1:18081/v1")
    return env


class Memory(threading.Thread):
    """Samples private bytes of the back end, both model servers (and extra pids, e.g. the MCP server) twice a second,
    and keeps the peak of each and of their sum per phase."""

    def __init__(self, ports: dict[str, int]):
        super().__init__(daemon=True)
        self.ports, self.extra, self.phase, self.peaks, self.pids = ports, {}, None, {}, {}
        self.stopping = threading.Event()

    def sample(self) -> dict:
        out = {}
        for name, pid in [(n, self.pid(n, p)) for n, p in self.ports.items()] + list(self.extra.items()):
            try:
                ps = [psutil.Process(pid)] if pid else []
                ps += ps[0].children(recursive=True) if ps else []
                out[name] = sum(p.memory_info().private for p in ps) / 2**20
            except psutil.Error:
                self.pids.pop(name, None)  # it restarted: look it up again next time
        return out

    def pid(self, name, port):
        if name not in self.pids:
            self.pids[name] = pid_on(port)
        return self.pids[name]

    def run(self):
        while not self.stopping.wait(0.5):
            if not self.phase:
                continue
            s = self.sample()
            pk = self.peaks.setdefault(self.phase, {})
            for k, v in list(s.items()) + [("total", sum(s.values()))]:
                pk[k] = round(max(pk.get(k, 0), v))

    def peak(self, phase):
        return self.peaks.get(phase, {})


# ---------------------------------------------------------------- copy: the installed back end on another port
def copy_backend(a):
    """Copy the installed back end and change only the port constant of desktop.py inside the PyInstaller archive."""
    src, dest = Path(os.path.expandvars(a.installed)), Path(a.to)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    exe = dest / "sniff-backend.exe"
    data = bytearray(exe.read_bytes())
    cookie = data.rfind(b"MEI\014\013\012\013\016")
    _, pkglen, tocoff, toclen, _, _ = struct.unpack("!8sIIii64s", data[cookie:cookie + 88])
    start = cookie + 88 - pkglen
    toc, pos = data[start + tocoff:start + tocoff + toclen], 0
    while pos < len(toc):
        size, off, clen, ulen, cflag, kind = struct.unpack("!IIIIBc", toc[pos:pos + 18])
        name = toc[pos + 18:pos + size].rstrip(b"\0").decode()
        if kind == b"s" and name == "desktop":
            code = zlib.decompress(data[start + off:start + off + clen])
            old, new = b"i" + struct.pack("<i", 8765), b"i" + struct.pack("<i", a.port)
            assert code.count(old) == 1, "the port constant 8765 was not found exactly once"
            packed = zlib.compress(code.replace(old, new), 9)
            assert len(packed) <= clen, f"port {a.port} compresses to {len(packed)} bytes, {clen} fit: try another port"
            data[start + off:start + off + clen] = packed + b"\0" * (clen - len(packed))  # zlib ignores bytes after the stream
            exe.write_bytes(bytes(data))
            print(f"copied to {dest}; desktop.py now listens on {a.port}")
            return
        pos += size
    sys.exit("desktop entry not found in the archive")


# ---------------------------------------------------------------- proxy: counts and times model calls
class _Forward(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        t0 = time.time()
        up = http.client.HTTPConnection("127.0.0.1", self.server.upstream, timeout=900)
        up.request(self.command, self.path, body, {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")})
        r = up.getresponse()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        if r.getheader("Content-Length") is not None:
            data = r.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:  # streamed answer: pass each piece on at once
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while chunk := r.read1(65536):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        up.close()
        with self.server.lock, open(self.server.log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t0": t0, "t1": time.time(), "port": self.server.upstream, "path": self.path, "status": r.status}) + "\n")

    do_GET = do_POST = _go


def proxy(a):
    log, lock = Path(a.home) / "model_calls.jsonl", threading.Lock()
    servers = []
    for listen, upstream in PROXY.items():
        s = ThreadingHTTPServer(("127.0.0.1", listen), _Forward)
        s.upstream, s.log, s.lock, s.daemon_threads = upstream, log, lock, True
        servers.append(s)
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print("proxy on", list(PROXY), flush=True)
    threading.Event().wait()


# ---------------------------------------------------------------- start and stop
def start(a):
    home = Path(a.home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{a.port}"
    if pid_on(a.port):
        sys.exit(f"Port {a.port} is already in use; stop that first.")
    pids = {}
    if not all(pid_on(p) for p in PROXY):
        pids["proxy"] = spawn([sys.executable, str(Path(__file__).resolve()), "proxy", "--home", str(home)], log=home / "proxy.log").pid
        while not all(pid_on(p) for p in PROXY):
            time.sleep(0.1)
    db = home / "data" / "sniff.db"
    indexed = db.exists() and sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("SELECT count(*) FROM files").fetchone()[0] > 0
    runs, p = [], None
    for i in range(a.runs):
        cond = conditions()
        t0 = time.perf_counter()
        p = spawn([a.exe], env=child_env(home), cwd=str(home), log=home / "backend.log")
        while True:
            try:
                if httpx.get(f"{url}/index/status", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if p.poll() is not None:
                sys.exit(f"The back end exited with {p.returncode}; see {home / 'backend.log'}")
            time.sleep(0.02)
        runs.append({"s": round(time.perf_counter() - t0, 3), "conditions": cond})
        print(f"start-up {i + 1}: {runs[-1]['s']} s", flush=True)
        if i < a.runs - 1:
            kill_tree(p.pid)
            while pid_on(a.port):
                time.sleep(0.1)
            time.sleep(1)
    pids["backend"] = p.pid
    old = json.loads((home / "pids.json").read_text()) if (home / "pids.json").exists() else {}
    (home / "pids.json").write_text(json.dumps({**old, **pids, "port": a.port, "exe": a.exe}))
    times = [r["s"] for r in runs]
    save("startup_indexed" if indexed else "startup_empty",
         {"exe": a.exe, "index_had_files": indexed, "runs": runs, "s": stats(times),
          "note": "from process start until GET /index/status answers"})


def stop(a):
    home = Path(a.home).resolve()
    pids = json.loads((home / "pids.json").read_text())
    for name in ("backend", "proxy"):
        pid = pids.get(name)
        try:
            p = psutil.Process(pid) if pid else None
        except psutil.NoSuchProcess:
            p = None
        # only what this script started: the copied exe, or this venv's python running the proxy
        if p and (name == "proxy" or Path(p.exe()).resolve() == Path(pids["exe"]).resolve()):
            kill_tree(pid)
            print("stopped", name, pid)


# ---------------------------------------------------------------- docs: the test folder and its truth
def make_docs(a):
    dest = Path(a.docs).resolve()
    if dest.exists():
        if any(dest.iterdir()) and not (dest / "truth.json").exists():
            sys.exit(f"{dest} is not empty and has no truth.json; refusing to delete it")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    synth, real = Path(a.synth), Path(a.real)
    files = {}
    for line in (synth / "labels.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["split"] != "dev":
            continue
        rel = r["file"].removeprefix("dev/")
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(synth / r["file"], dest / rel)
        files[rel] = dict(type=r["type"], format=r["format"], fields=r["fields"], text=r["text"], source="dev")
    reals = sorted((json.loads(x) for x in (real / "labels.jsonl").read_text(encoding="utf-8").splitlines()), key=lambda r: r["file"])[:3]
    for r in reals:
        rel = "Receipts/" + Path(r["file"]).name
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(real / r["file"], dest / rel)
        files[rel] = dict(type="receipt", format="image_real", fields=r["fields"], text=None, source="real SROIE")

    sys.path.insert(0, str(ROOT / "data" / "synth"))
    import generate as gen  # the synthetic generator's own layouts, so planted documents look like the rest
    hh, gas = gen.HOUSEHOLDS["dev"], gen.PROVIDERS[1]

    def bill(rel, fmt, month, issue, due, cur, prev, seed):
        rng = random.Random(seed)
        d = dict(prov=gas, acct="MG-6421-9595", cust=hh["cust"], addr=hh["addr"], month=month, issue=issue, due=due,
                 amount=cur + prev, cur=cur, prev=prev, paid_on=None, late=round((cur + prev) * 1.08, 2), comps=gen.charges(rng, gas, cur),
                 phone="4410-221", ref="550912734", usage=f"Consumption this month: {int(cur / 25)} m3")
        tax = next(x for lb, x in d["comps"] if "Tax" in lb)
        text = gen.render(dict(type="utility_bill", fmt=fmt, layout=gen.bill_b, d=d), random.Random(seed), dest / rel)
        return dict(type="utility_bill", format=fmt, text=text, source="planted",
                    fields={"provider": gas["name"], "account_number": "MG-6421-9595", "billing_month": f"{month:%Y-%m}",
                            "bill_date": issue.isoformat(), "due_date": due.isoformat(), "current_charges": cur,
                            "previous_balance": prev or None, "tax": tax, "amount_due": cur + prev, "payment_status": None, "payment_date": None})

    files[GAS_SEP] = bill(GAS_SEP, "pdf_text", date(2026, 9, 1), date(2026, 10, 3), date(2026, 10, 15), 3000.0, 0.0, "acc-gas-09")
    files[GAS_OCT] = bill(GAS_OCT, "pdf_text", date(2026, 10, 1), date(2026, 11, 4), date(2026, 12, 12), 5400.0, 3000.0, "acc-gas-10")
    files[GAS_OCT_PHOTO] = dict(bill(GAS_OCT_PHOTO, "image", date(2026, 10, 1), date(2026, 11, 4), date(2026, 12, 12), 5400.0, 3000.0, "acc-gas-10"),
                                near_of=GAS_OCT)  # the same bill again, as a phone photo
    (dest / GAS_SEP_COPY).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(dest / GAS_SEP, dest / GAS_SEP_COPY)
    files[GAS_SEP_COPY] = dict(files[GAS_SEP], copy_of=GAS_SEP)

    # the same CNIC as the dev card PXL_20190101_186672.jpg, with other dates: a conflict
    d = dict(name="Zainab Malik", id="99830-6729096-9", dob=date(1999, 12, 9), issue=date(2017, 1, 20), expiry=date(2027, 1, 20), inst=None, sub="national")
    text = gen.render(dict(type="id_card", fmt="pdf_text", layout=gen.id_national, d=d), random.Random("acc-cnic"), dest / CNIC)
    files[CNIC] = dict(type="id_card", format="pdf_text", text=text, source="planted",
                       fields={"name": "Zainab Malik", "id_number": "99830-6729096-9", "date_of_birth": "1999-12-09",
                               "issue_date": "2017-01-20", "expiry_date": "2027-01-20"})

    import docx
    lines = ["Riverside Analytics Pvt Ltd", "12 Canal View, Lakeshore City", "Date: 02 November 2026", "To: Zainab Malik",
             "Subject: Offer of Employment", "Dear Zainab Malik,",
             "We are pleased to offer you the position of Data Analyst at Riverside Analytics Pvt Ltd.",
             "Position: Data Analyst", "Monthly Salary: Rs. 185,000 (gross)", "Joining Date: 01 December 2026",
             "Please sign and return a copy of this letter by 15 November 2026.", "For Riverside Analytics Pvt Ltd", "Hina Javed, HR Manager"]
    doc = docx.Document()
    for x in lines:
        doc.add_paragraph(x)
    doc.save(dest / DOCX)
    files[DOCX] = dict(type="offer_letter", format="docx", text="\n".join(lines), source="planted",
                       fields={"employer": "Riverside Analytics Pvt Ltd", "candidate_name": "Zainab Malik", "position": "Data Analyst",
                               "monthly_salary": 185000.0, "offer_date": "2026-11-02", "start_date": "2026-12-01"})

    from pptx import Presentation
    from pptx.util import Inches, Pt
    lines = ["Brightpath Academy", "Certificate of Completion", "This is to certify that", "Omar Farooq",
             "has successfully completed the course", "Solar PV Installation and Maintenance", "Date of Issue: 12 October 2026",
             "Director, Brightpath Academy"]
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i, x in enumerate(lines):
        tf = slide.shapes.add_textbox(Inches(0.5), Inches(0.4 + i * 0.8), Inches(9), Inches(0.7)).text_frame
        tf.text = x
        tf.paragraphs[0].runs[0].font.size = Pt(28 if i in (1, 3) else 18)
    prs.save(dest / PPTX)
    files[PPTX] = dict(type="certificate", format="pptx", text="\n".join(lines), source="planted",
                       fields={"holder_name": "Omar Farooq", "title": "Solar PV Installation and Maintenance",
                               "institution": "Brightpath Academy", "issue_date": "2026-10-12"})
    (dest / "truth.json").write_text(json.dumps({"made": date.today().isoformat(), "files": files}, indent=1), encoding="utf-8")
    print(f"{len(files)} files in {dest}: {Counter(f['source'] for f in files.values())}")


# ---------------------------------------------------------------- the run
class Ctx:
    def __init__(self, a):
        self.a, self.url = a, a.url.rstrip("/")
        self.docs = Path(a.docs).resolve()
        self.home = Path(a.home).resolve() if a.home else None
        self.http = httpx.Client(base_url=self.url, timeout=900)
        self.truth = json.loads((self.docs / "truth.json").read_text(encoding="utf-8"))["files"]
        self.today = date.today()
        self.mem = Memory({"backend": int(self.url.rsplit(":", 1)[1]), "chat": 8080, "embed": 8081})
        self.mem.start()

    def rel(self, path: str) -> str:
        root = str(self.docs).lower() + os.sep  # the back end stores resolved paths; resolve() would open every file
        return path[len(root):].replace(os.sep, "/") if path.lower().startswith(root) else path

    def get(self, path, **params):
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def documents(self) -> dict[str, dict]:
        """Each document under every path that holds it: of two identical files, whichever was scanned first is the document."""
        return {self.rel(p): d for d in self.get("/documents") for p in d["paths"] or [d["path"]]}

    def calls(self, t0: float, t1: float) -> list[dict]:
        log = self.home / "model_calls.jsonl" if self.home else None
        if not log or not log.exists():
            return []
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [r for r in rows if t0 <= r["t0"] < t1]

    def db(self):
        conn = sqlite3.connect(f"file:{self.home / 'data' / 'sniff.db'}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def kept(self) -> dict[str, dict]:
        """Truth documents a calculation should use: no exact copies, no near-duplicates."""
        return {k: v for k, v in self.truth.items() if not v.get("copy_of") and not v.get("near_of")}

    def same_record(self, rel: str) -> str:
        t = self.truth.get(rel, {})
        return t.get("copy_of") or t.get("near_of") or rel

    def wait(self, cond, timeout=600, every=0.1) -> float | None:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if cond():
                    return round(time.time() - t0, 3)
            except httpx.HTTPError:
                pass
            time.sleep(every)
        return None


def model_split(calls: list[dict], t0: float, t1: float) -> dict:
    inside = [c for c in calls if t0 <= c["t0"] < t1]
    return {"model_s": round(sum(min(c["t1"], t1) - c["t0"] for c in inside), 3),
            "chat_calls": sum(c["port"] == 8080 for c in inside), "embed_calls": sum(c["port"] == 8081 for c in inside)}


def split_by_calls(calls: list[dict], order: list[str], truth: dict) -> list[dict]:
    """Per document: reading time and model time, from the proxy's call log. A new document is read, then embedded
    (one embeddings call per 32 chunks), then classified and its facts extracted. So each run of embeddings calls starts
    a document, and the idle time before it is that document's reading (and OCR). `order` is the scan order.
    Polling /index/status for the current file is not precise enough: it answers late while OCR runs."""
    calls = [x for x in calls if x["path"] != "/v1/models"]
    starts = [i for i, x in enumerate(calls) if x["port"] == 8081 and (i == 0 or calls[i - 1]["port"] != 8081)]
    if len(starts) != len(order) or sum(calls[i]["port"] == 8080 for i in range(len(calls))) < len(order) // 2:
        return [{"error": f"{len(starts)} embedding runs for {len(order)} scanned files, or facts read apart from reading: cannot split"}]
    out = []
    for k, i in enumerate(starts):
        mine = calls[i:starts[k + 1] if k + 1 < len(starts) else len(calls)]
        read = calls[i]["t0"] - calls[i - 1]["t1"] if i else None
        m = model_split(mine, mine[0]["t0"], mine[-1]["t1"] + 1)
        out.append({"file": order[k], "format": truth.get(order[k], {}).get("format"), "read_s": read and round(read, 3), **m,
                    "total_s": round((read or 0) + mine[-1]["t1"] - mine[0]["t0"], 3)})
    return out


def step_index(c: Ctx):
    if c.get("/documents"):
        sys.exit("The index is not empty. The index step needs a fresh SNIFF_HOME.")
    expected = len(c.truth)
    cond = conditions()
    c.mem.phase = "indexing"
    t0 = time.time()
    c.http.post("/watch", json={"folder": str(c.docs)}).raise_for_status()
    first = all_found = done = None
    cur, spans, last, polled, found = None, {}, {}, 0.0, 0
    while time.time() - t0 < c.a.timeout:
        now = time.time()
        s = c.get("/index/status")
        if s.get("current") != cur:
            if cur:
                spans[cur][1] = now
            cur = s.get("current")
            if cur:
                spans.setdefault(cur, [now, None])
        if not first or now - polled >= 1:  # the document list every 0.25 s until the first file, then every second
            polled = now
            docs = c.get("/documents")
            found = sum(len(d["paths"]) for d in docs if d["status"] == "indexed")
            first = first or (found and round(now - t0, 2))
        if found >= expected and all_found is None:
            all_found = round(now - t0, 2)
        last = s
        if s.get("total") and s.get("done") == s.get("total") and not s.get("running") and not s.get("queued") and now - t0 > 5:
            done = round(now - t0, 2)
            break
        time.sleep(0.25)
    c.mem.phase = None
    docs = c.get("/documents")
    found = sum(len(d["paths"]) for d in docs if d["status"] == "indexed")
    if found >= expected and all_found is None:  # the last file was committed between two polls
        all_found = done
    calls = c.calls(t0, time.time())
    main = {d["path"] for d in docs}  # of two identical files, the one scanned second is only a copy: no reading, no calls
    per_doc = split_by_calls(calls, [c.rel(p) for p in spans if p in main], c.truth)
    by_fmt = {}
    for d in per_doc:
        by_fmt.setdefault(d["format"], []).append(d)
    per_format = {f: {k: stats([d[k] for d in ds]) for k in ("total_s", "read_s", "model_s")} for f, ds in by_fmt.items()}
    text = {}
    if c.home:  # does each format give text? word recall against the text the generator drew
        with c.db() as conn:
            rows = conn.execute("SELECT f.path, f.status, f.error, count(c.id) n, group_concat(c.text, ' ') txt FROM files f "
                                "LEFT JOIN chunks c ON c.file_id=f.id WHERE f.copy_of IS NULL GROUP BY f.id").fetchall()
        for r in rows:
            t = c.truth.get(c.rel(r["path"]))
            if not t:
                continue
            got = set(re.findall(r"[a-z0-9]{3,}", (r["txt"] or "").lower()))
            gold = set(re.findall(r"[a-z0-9]{3,}", (t["text"] or "").lower()))
            e = text.setdefault(t["format"], {"files": 0, "with_text": 0, "recall": []})
            e["files"] += 1
            e["with_text"] += bool(r["txt"])
            if gold:
                e["recall"].append(len(gold & got) / len(gold))
        for e in text.values():
            e["word_recall_mean"] = round(sum(e["recall"]) / len(e["recall"]), 3) if e["recall"] else None
            e["word_recall_min"] = round(min(e["recall"]), 3) if e["recall"] else None
            del e["recall"]
    errors = [{"file": c.rel(d["path"]), "status": d["status"]} for d in c.get("/documents") if d["status"] != "indexed"]
    save("index", {"conditions": cond, "conditions_after": conditions(), "files": expected, "first_findable_s": first,
                   "all_findable_s": all_found, "all_facts_s": done, "last_status": last,
                   "total_model_calls": {"chat": sum(x["port"] == 8080 for x in calls), "embed": sum(x["port"] == 8081 for x in calls)},
                   "per_doc_all": {k: stats([d[k] for d in per_doc]) for k in ("total_s", "read_s", "model_s")},
                   "per_format": per_format, "text_by_format": text, "errors": errors, "memory_mb": c.mem.peak("indexing"),
                   "per_doc": per_doc})


# ---------------------------------------------------------------- asking
def ask(c: Ctx, q: str) -> dict:
    """POST /ask, or /ask/stream when the back end has it. Times the first byte or first token, and the whole answer."""
    t0 = time.time()
    first, ans = None, None
    if c.stream:
        with c.http.stream("POST", "/ask/stream", json={"question": q}) as r:
            kind = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    kind = line[6:].strip()
                elif line.startswith("data:"):
                    if kind == "token" and first is None:
                        first = time.time() - t0
                    elif kind == "answer":
                        ans = json.loads(line[5:])
                    elif kind == "error":
                        ans = {"answer": "ERROR " + line[5:], "route": "error", "sources": []}
    else:
        with c.http.stream("POST", "/ask", json={"question": q}) as r:
            body = b""
            for chunk in r.iter_bytes():
                first = first if first is not None else time.time() - t0
                body += chunk
            ans = json.loads(body) if r.status_code == 200 else {"answer": f"HTTP {r.status_code}", "route": "error", "sources": []}
    t1 = time.time()
    full = t1 - t0
    return {"q": q, "s": round(full, 3), "first_s": round(first if first is not None else full, 3), **model_split(c.calls(t0, t1 + 0.5), t0, t1),
            "route": ans.get("route"), "answer": ans.get("answer"), "query": ans.get("query"),
            "sources": [(c.rel(s["path"]), s["page"]) for s in ans.get("sources", [])],
            "counted": [c.rel(s["path"]) for s in ans.get("counted", [])],
            "skipped": [c.rel(s["path"]) for s in ans.get("skipped_duplicates", [])],
            "unreviewed": [f["id"] for f in ans.get("unreviewed", [])],
            "conflicts": [{"field": x["field"], "values": [v["value"] for v in x["values"]]} for x in ans.get("conflicts", [])]}


def expiring(c: Ctx, lo: date, hi: date, fields=("expiry_date", "warranty_until", "end_date")):
    """Truth: (file, date) of every kept document with an expiry-type date in [lo, hi]."""
    return [(rel, v) for rel, t in c.kept().items() for f, v in (t["fields"] or {}).items()
            if f in fields and v and lo <= date.fromisoformat(v) <= hi]


def questions(c: Ctx) -> dict[str, list]:
    T, gas = c.today, {"numbers": [8400], "counted_in": [GAS_SEP, GAS_OCT, GAS_OCT_PHOTO], "not_numbers": [11400, 13800, 14400, 16800], "largest": True}
    nxt = min((v, rel) for rel, v in expiring(c, T, date(2100, 1, 1)))
    win = lambda lo, hi, f=("expiry_date", "warranty_until", "end_date"): {"expiring": expiring(c, lo, hi, f)}  # noqa: E731
    return {
        "single_fact": [
            ("When does Bilal Ahmed's driving licence expire?", {"dates": ["2026-12-14"], "sources": [LICENCE]}),
            ("What is the due date of my October 2026 gas bill?", {"dates": ["2026-12-12"], "sources": [GAS_OCT, GAS_OCT_PHOTO]}),
            ("What is the monthly rent in Zainab Malik's tenancy agreement?", {"numbers": [21000], "sources": [TENANCIES[3]]}),
            ("When does Zainab Malik's passport expire?", {"dates": ["2026-08-09"], "sources": ["Downloads/Document (14).pdf"]}),
            ("What monthly salary does Riverside Analytics offer Zainab Malik?", {"numbers": [185000], "sources": [DOCX]}),
        ],
        "cross_doc_total": [
            ("How much did I spend on gas in 2026?", gas),
            ("What was my total gas spending this year?", gas),
            ("Add up my Metro Gas bills for 2026.", gas),
            ("How much did I spend on electricity in 2025?", {"numbers": [27668], "largest": True, "counted_in": ["Desktop/scan_3591.pdf", "Downloads/9f5d83e4.pdf"]}),
            ("How much did I spend at Metro Electronics Mart in 2025?", {"numbers": [231800.4], "largest": True, "counted_in": [
                "Documents/old/IMG_4953.jpg", "Documents/old/PXL_20251115_134335.jpg", "Downloads/Print_2025-03-16.pdf"]}),
        ],
        "expiring_window": [
            ("What expires in the next 90 days?", win(T, T + timedelta(days=90))),
            ("Is anything expiring in the next 30 days?", win(T, T + timedelta(days=30))),
            ("What expires between 1 December 2026 and 31 January 2027?", win(date(2026, 12, 1), date(2027, 1, 31))),
            ("Which document expires next?", {"dates": [nxt[0]], "sources": [nxt[1]]}),
            ("Which warranties end in 2027?", win(date(2027, 1, 1), date(2027, 12, 31), ("warranty_until",))),
        ],
        "gas_bill": [(q, {"numbers": [8400], "largest": True, "sources": [GAS_OCT, GAS_OCT_PHOTO]}) for q in (
            "How much is my gas bill?", "What is my gas bill?", "How much do I owe for gas?",
            "What is the amount on my latest gas bill?", "How much is the gas bill I need to pay?")],
        "conflict": [(q, {"conflict": True, "sources": [CNIC, "Desktop/PXL_20190101_186672.jpg"]}) for q in (
            "When does Zainab Malik's ID card expire?", "What is the expiry date on Zainab Malik's national identity card?",
            "When was Zainab Malik's identity card issued?", "Until when is Zainab Malik's CNIC valid?", "Zainab Malik ID card expiry date?")],
        "written": [
            ("How long do the brownies bake, and at what temperature?", {"words": ["175", "25"], "sources": ["Desktop/file_1814.pdf"]}),
            ("How do I find the eigenvalues of a matrix?", {"words_any": ["det", "characteristic"], "sources": ["Documents/old/download (14).pdf"]}),
            ("How much notice must be given to end the tenancy agreement?", {"words_any": ["one month", "1 month"], "sources": TENANCIES}),
            ("What does the Voltix UPS warranty not cover?", {"words_any": ["misuse", "surge", "unauthorised", "unauthorized"],
                                                               "sources": ["Desktop/CamScanner 08-30-2025 15.14.pdf"]}),
            ("When does the weekly team sync meeting happen?", {"words": ["monday"], "sources": ["Desktop/download (6).pdf"]}),
        ],
    }


def judge(c: Ctx, r: dict, exp: dict) -> dict:
    """Correct? Citations right? Each check is listed, so a failure says why."""
    text, why = r["answer"] or "", []
    got = numbers(text)
    for n in exp.get("numbers", []):
        if not any(abs(n - g) < 0.01 for g in got):
            why.append(f"missing {n:,}")
    if exp.get("largest") and exp.get("numbers") and (big := [g for g in got if g > max(exp["numbers"]) + 0.01]):
        why.append(f"a larger total in the answer: {big[0]:,}")  # the expected figure as one bill inside a wrong total
    for n in exp.get("not_numbers", []):
        if any(abs(n - g) < 0.01 for g in got):
            why.append(f"wrong total {n:,} (counted twice)")
    for d in exp.get("dates", []):
        if not has_date(text, d):
            why.append(f"missing date {d}")
    for w in exp.get("words", []):
        if w.lower() not in text.lower():
            why.append(f"missing '{w}'")
    if exp.get("words_any") and not any(w.lower() in text.lower() for w in exp["words_any"]):
        why.append(f"none of {exp['words_any']}")
    if "expiring" in exp:
        want = exp["expiring"]
        for rel, d in want:
            if not has_date(text, d):
                why.append(f"missing {d} ({rel})")
        conflicted = {CNIC, "Desktop/PXL_20190101_186672.jpg"}  # when one side of the conflict is due, the other may be named
        skip = conflicted if any(rel in conflicted for rel, _ in want) else set()
        others = {v for rel, t in c.kept().items() if rel not in skip for f, v in (t["fields"] or {}).items()
                  if f in ("expiry_date", "warranty_until", "end_date") and v and (rel, v) not in want}
        if extra := [d for d in others if has_date(text, d)]:
            why.append(f"dates outside the window: {extra}")
        exp = {**exp, "sources": [rel for rel, _ in want] + list(skip)}
        if not want:
            exp["sources"] = []
    if exp.get("conflict") and not (r["conflicts"] or "conflict" in text.lower()):
        why.append("conflict not shown")
    cite = "n/a"
    if exp.get("sources") is not None or exp.get("counted_in"):
        allowed = set(exp.get("sources") or []) | set(exp.get("counted_in") or [])
        srcs = [c.same_record(s) for s, _ in r["sources"]]
        bad = [s for s, p in r["sources"] if c.same_record(s) not in {c.same_record(x) for x in allowed} or p != 1]
        cite = "ok" if r["sources"] and not bad else ("no sources" if not r["sources"] else f"wrong: {bad}")
        if exp.get("counted_in"):
            if len(srcs) != len(set(srcs)):
                cite = f"one record counted twice: {r['sources']}"
        if allowed and cite != "ok":
            why.append(f"citations {cite}")
    return {"pass": not why, "why": why, "citations": cite}


def step_ask(c: Ctx):
    cond = conditions()
    qs = questions(c)
    c.mem.phase = "asking"
    out = {cat: [] for cat in qs}
    for i in range(5):  # round-robin, so the same prompt never runs twice in a row (llama-server keeps the last prompt cached)
        for cat, items in qs.items():
            q, exp = items[i]
            r = ask(c, q)
            r.update(judge(c, r, exp))
            out[cat].append(r)
            print(f"  {cat:16} {r['s']:6.2f}s {'PASS' if r['pass'] else 'FAIL'} {q} -> {(r['answer'] or '')[:90]!r} {r['why']}", flush=True)
    c.mem.phase = None
    summary = {cat: {"passed": sum(r["pass"] for r in rs), "of": len(rs), "s": stats([r["s"] for r in rs]),
                     "first_s": stats([r["first_s"] for r in rs]), "model_s": stats([r["model_s"] for r in rs]),
                     "citations_ok": sum(r["citations"] == "ok" for r in rs)} for cat, rs in out.items()}
    fact_times = [r["s"] for cat, rs in out.items() if cat != "written" for r in rs if r["route"] == "facts"]
    save("ask", {"conditions": cond, "conditions_after": conditions(), "streaming": c.stream, "summary": summary,
                 "fact_answer_s": stats(fact_times), "memory_mb": c.mem.peak("asking"), "runs": out})


# ---------------------------------------------------------------- facts, correction and approval
def step_facts(c: Ctx):
    docs = c.documents()
    tot = ok = absent = 0
    types, by_type, statuses, no_reason, wrong = Counter(), {}, Counter(), [], []
    ver = {"verified": [0, 0], "needs_review": [0, 0]}  # [correct, all] per status
    for rel, t in c.truth.items():
        if t.get("copy_of"):
            continue
        d = docs.get(rel)
        pred = (d or {}).get("doc_type") or ("missing" if not d else None)
        types[(t["type"], pred)] += 1
        facts = {}
        for f in (d or {}).get("facts", []):
            facts.setdefault(f["field"], f)
            statuses[f["status"]] += 1
            if f["status"] == "needs_review" and not f.get("reason"):
                no_reason.append((rel, f["field"]))
        bt = by_type.setdefault(t["type"], [0, 0])
        for field, gold in (t["fields"] or {}).items():
            p = facts.get(field)
            if gold is None:
                absent += p is not None
                continue
            good = bool(p) and same_value(p["value"], gold)
            tot, ok = tot + 1, ok + good
            bt[0], bt[1] = bt[0] + good, bt[1] + 1
            if p and p["status"] in ver:
                ver[p["status"]][0] += good
                ver[p["status"]][1] += 1
            if not good:
                wrong.append({"file": rel, "field": field, "gold": gold, "got": p and p["value"], "status": p and p["status"]})
    n = sum(types.values())
    type_ok = sum(v for (g, p), v in types.items() if g == p)
    # correcting and approving through the API changes what totals use
    corr = {}
    sep = docs.get(GAS_SEP)
    fact = next((f for f in (sep or {}).get("facts", []) if f["field"] == "current_charges"), None)
    total = lambda a: next((float(x.replace(",", "")) for x in re.findall(r"([\d,]+(?:\.\d+)?) in total", a["answer"] or "")), None)  # noqa: E731
    tried = []
    for q in ("How much did I spend on gas in 2026?", "What was my total gas spending this year?", "Add up my Metro Gas bills for 2026.",
              "What is the sum of the current charges on my Metro Gas bills in 2026?"):
        a0 = ask(c, q)  # the router is tested in the ask step; here a question is needed that reaches the facts route
        tried.append({"q": q, "route": a0["route"], "answer": (a0["answer"] or "")[:300]})
        if a0["route"] == "facts" and total(a0) is not None:
            corr["question"] = q
            break
    corr["tried"] = tried
    if fact and corr.get("question"):
        r1 = c.http.patch(f"/facts/{fact['id']}", json={"value": "Rs. 3,100"})
        a1 = ask(c, corr["question"])
        bad = c.http.patch(f"/facts/{fact['id']}", json={"value": "abc"})
        c.http.patch(f"/facts/{fact['id']}", json={"value": fact["value"]})
        r3 = c.http.post(f"/facts/{fact['id']}/approve")
        a2 = ask(c, corr["question"])
        t0, t1, t2 = total(a0), total(a1), total(a2)
        corr.update(fact={k: fact[k] for k in ("id", "value", "status", "reason")}, before=a0["answer"], after_correction=a1["answer"],
                    after_approval=a2["answer"], correction_status=r1.json().get("status") if r1.status_code == 200 else r1.status_code,
                    garbage_rejected=bad.status_code == 422, approve_status=r3.json().get("status") if r3.status_code == 200 else r3.status_code,
                    unreviewed_before=fact["id"] in a0["unreviewed"], unreviewed_after=fact["id"] in a2["unreviewed"])
        corr["pass"] = (None not in (t0, t1, t2) and abs((t1 - t0) - (3100 - float(fact["value"]))) < 0.01 and abs(t2 - t0) < 0.01
                        and corr["correction_status"] == "corrected" and corr["approve_status"] == "approved"
                        and corr["garbage_rejected"] and not corr["unreviewed_after"])
    else:
        corr["pass"], corr["why"] = False, ("no gas total question reached the facts route" if fact else
                                            "the planted September gas bill has no current_charges fact")
    # A near-duplicate whose key field was misread is counted as another record. Correcting that field in review
    # should fold it into the record. This changes the index, so the facts step runs after ask and mcp.
    sys.path.insert(0, str(ROOT / "backend"))
    from sniff.schemas import KEYS

    fold = {"pass": None, "why": "no near-duplicate with a misread key field"}
    for rel, t in c.truth.items():
        d = docs.get(rel)
        if not t.get("near_of") or not d or not corr.get("question"):
            continue
        have = {f["field"]: f for f in d["facts"]}
        misread = [(f, t["fields"][f]) for f in KEYS[t["type"]] if f in have and not same_value(have[f]["value"], t["fields"][f])]
        if not misread:
            continue
        b = ask(c, corr["question"])
        fixed = {f: c.http.patch(f"/facts/{have[f]['id']}", json={"value": str(g)}).status_code for f, g in misread}
        a = ask(c, corr["question"])
        want = sum(float(x["fields"]["current_charges"]) for x in c.kept().values()
                   if x["type"] == "utility_bill" and x["fields"].get("provider") == "Metro Gas Ltd" and x["fields"]["billing_month"] >= "2026")
        fold = {"file": rel, "misread": {f: have[f]["value"] for f, _ in misread}, "corrected_to": dict(misread), "patch_status": fixed,
                "before": b["answer"], "after": a["answer"], "skipped_after": a["skipped"], "expected_total": want,
                "pass": total(a) == want and {c.same_record(s) for s in a["skipped"]} == {t["near_of"]}}
    save("facts", {"documents": n, "type_accuracy": round(type_ok / n, 3), "type_confusions": {f"{g} -> {p}": v for (g, p), v in types.items() if g != p},
                   "field_accuracy": round(ok / tot, 3), "fields": tot, "values_for_absent_fields": absent,
                   "field_accuracy_by_type": {k: round(a / b, 3) for k, (a, b) in by_type.items() if b},
                   "statuses": dict(statuses), "needs_review_without_reason": no_reason,
                   "verified_precision": round(ver["verified"][0] / ver["verified"][1], 3) if ver["verified"][1] else None,
                   "needs_review_correct_share": round(ver["needs_review"][0] / ver["needs_review"][1], 3) if ver["needs_review"][1] else None,
                   "correct_and_approve": corr, "fix_misread_near_duplicate": fold, "wrong_fields": wrong})


# ---------------------------------------------------------------- search, and the real launcher
def searches(c: Ctx) -> list[tuple[str, list[str]]]:
    gas = [k for k, v in c.truth.items() if (v["fields"] or {}).get("provider") == "Metro Gas Ltd"]
    real = [(k, v) for k, v in c.truth.items() if v["source"] == "real SROIE"]
    by = lambda typ: [k for k, v in c.truth.items() if v["type"] == typ]  # noqa: E731
    return [
        ("Metro Gas bill", gas), ("driving licence", [LICENCE]), ("brownie recipe", ["Desktop/file_1814.pdf"]),
        ("eigenvalues", ["Documents/old/download (14).pdf"]), ("Voltix UPS warranty", ["Desktop/CamScanner 08-30-2025 15.14.pdf"]),
        ("Lakeshore University fee voucher", by("fee_voucher")), ("Omar Farooq passport", ["Desktop/photo_12.png", "Desktop/scan_9417.pdf"]),
        ("tenancy agreement Rubina Siddiqui", [TENANCIES[3]]), ("CareFirst health insurance", ["Desktop/PXL_20250201_151805.jpg"]),
        ("Orchard Bank statement March 2025", ["Downloads/CamScanner 03-31-2025 14.40.pdf"]),
        ("Cedar Books offer letter", ["Desktop/Document (6).pdf", "Downloads/7fac459b.pdf"]),
        ("Master of Fine Arts transcript", ["Documents/old/download (5).pdf", "Documents/old/photo_9.png"]),
        ("Daily Needs Mart receipt", ["Desktop/IMG_3063.jpg", "Documents/old/Document (8).pdf"]),
        ("CityFuel Station", ["Downloads/photo_6.png", "WhatsApp Images/IMG-20251107-WA0936.jpg"]),
        ("Bachelor of Commerce degree", ["Desktop/Scan 13.pdf"]), ("FastLink internet bill", ["Downloads/Scan 27.pdf", "Downloads/download (14).pdf"]),
        ("weekly team sync agenda", ["Desktop/download (6).pdf"]), ("Sunwave refrigerator warranty", ["WhatsApp Images/IMG-20250424-WA0861.jpg"]),
        ("welding safety certificate", ["Desktop/Print_2023-06-18.pdf"]),
        ("HomeEase Appliances receipt", ["Documents/old/IMG_7333.jpg", "WhatsApp Images/IMG-20250625-WA0678.jpg"]),
        ("Riverside Analytics offer", [DOCX]), ("Solar PV Installation certificate", [PPTX]),
        ("electricity bill June 2025", ["Desktop/scan_3591.pdf"]), ("Zainab Malik identity card", [CNIC, "Desktop/PXL_20190101_186672.jpg"]),
    ] + [(v["fields"]["merchant"].title(), [k]) for k, v in real]


def step_search(c: Ctx):
    cond = conditions()
    qs, ms, hits = searches(c), [], {}
    for run in range(c.a.runs):
        for q, want in qs:
            t = time.perf_counter()
            h = c.get("/search", q=q)
            ms.append((time.perf_counter() - t) * 1000)
            if run == 0:
                top = [c.same_record(c.rel(x["path"])) for x in h[:5]]
                hits[q] = {"hit_at_5": any(c.same_record(w) in top for w in want), "top": [c.rel(x["path"]) for x in h[:3]]}
    save("search", {"conditions": cond, "queries": len(qs), "runs": c.a.runs, "ms": stats(ms, 1),
                    "under_target": sum(m < TARGETS["search_ms"] for m in ms), "calls": len(ms),
                    "hit_at_5": sum(v["hit_at_5"] for v in hits.values()), "per_query": hits})


UI_JS = r"""
const { chromium } = require('playwright');
(async () => {
  const cfg = JSON.parse(process.env.UI_CFG);
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.error('pageerror', e.message));
  await page.goto(cfg.app);
  const box = page.getByRole('combobox');
  await box.waitFor({ timeout: 30000 });
  await page.evaluate(() => window.addEventListener('keydown', () => { window.__t = performance.now() }, true));
  const out = [];
  for (let run = 0; run <= cfg.runs; run++) for (const [q, expect] of cfg.queries) {
    await box.fill(''); await page.waitForTimeout(400);
    await page.evaluate((expect) => {
      window.__done = null;
      const obs = new MutationObserver(() => {
        const res = document.getElementById('results');
        if (res && [...res.querySelectorAll('[role=option]')].some((o) => o.textContent.includes(expect))) {
          obs.disconnect(); requestAnimationFrame(() => { window.__done = performance.now() });
        }
      });
      obs.observe(document.body, { childList: true, subtree: true, characterData: true });
    }, expect);
    await box.focus();
    await page.keyboard.type(q, { delay: 40 });
    await page.waitForFunction(() => window.__done, null, { timeout: 30000 });
    const ms = await page.evaluate(() => window.__done - window.__t);
    if (run > 0) out.push({ q, ms });  // run 0 warms up
  }
  console.log(JSON.stringify(out));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1) });
"""


def step_ui(c: Ctx):
    """Keystroke to results in the shipped launcher build, served as static files with its API base pointed at this back end."""
    dist = Path(c.a.frontend).resolve()
    api = c.url.encode()

    class Static(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = dist / (self.path.split("?")[0].lstrip("/") or "index.html")
            if not p.is_file():
                self.send_error(404)
                return
            body = p.read_bytes()
            if p.suffix == ".js":  # the browser build calls http://127.0.0.1:8000 (api.ts); this back end is elsewhere
                body = body.replace(b"http://127.0.0.1:8000", api)
            self.send_response(200)
            self.send_header("Content-Type", {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}.get(p.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Static)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    qs = [q for q, _ in searches(c)][:5]
    queries = [[q, c.get("/search", q=q)[0]["name"]] for q in qs]  # wait for the top result's file name to appear
    cond = conditions()
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, shell=True).stdout.strip()
    env = {**os.environ, "NODE_PATH": root, "UI_CFG": json.dumps({"app": f"http://127.0.0.1:{srv.server_port}/", "runs": c.a.runs, "queries": queries})}
    js = (c.home or Path.cwd()) / "ui_timing.cjs"
    js.write_text(UI_JS, encoding="utf-8")
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, env=env, timeout=900)
    srv.shutdown()
    if r.returncode:
        save("ui", {"error": r.stderr[-2000:]})
        return
    runs = json.loads(r.stdout.strip().splitlines()[-1])
    save("ui", {"conditions": cond, "frontend": str(dist), "ms": stats([x["ms"] for x in runs], 1), "runs": runs,
                "method": "headless Chromium (Playwright); from the last keydown to the first frame after the top result's file name "
                          "is in the results list; includes the launcher's 120 ms typing pause"})


# ---------------------------------------------------------------- reminders and overview
def ics_check(body: str, ctype: str, preview: dict) -> dict:
    why = []
    if not body.endswith("\r\n") or "\n" in body.replace("\r\n", ""):
        why.append("lines do not all end in CRLF")
    if long := [x for x in body.split("\r\n") if len(x.encode()) > 75]:
        why.append(f"{len(long)} lines over 75 octets")
    lines = body.replace("\r\n ", "").split("\r\n")[:-1]
    if lines[:1] != ["BEGIN:VCALENDAR"] or lines[-1:] != ["END:VCALENDAR"]:
        why.append("not wrapped in VCALENDAR")
    if [x for x in lines if x.startswith("BEGIN:")].__len__() != [x for x in lines if x.startswith("END:")].__len__():
        why.append("BEGIN/END not balanced")
    props = {}
    for x in lines:
        k, _, v = x.partition(":")
        props.setdefault(k, v)
    for k in ("VERSION", "PRODID", "UID", "DTSTAMP", "DTSTART;VALUE=DATE", "SUMMARY"):
        if k not in props:
            why.append(f"no {k}")
    alarms = body.count("BEGIN:VALARM")
    triggers = re.findall(r"TRIGGER:(-P\d+D)", body)
    if not alarms or len(triggers) != alarms or body.count("ACTION:DISPLAY") != alarms:
        why.append("alarms incomplete")
    unesc = lambda s: s.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")  # noqa: E731
    same = {"title": unesc(props.get("SUMMARY", "")) == preview["title"],
            "date": props.get("DTSTART;VALUE=DATE") == preview["date"].replace("-", ""),
            "description": unesc(props.get("DESCRIPTION", "")) == preview["description"]}
    if not all(same.values()):
        why.append(f"preview differs: {same}")
    if "text/calendar" not in ctype:
        why.append(f"content type {ctype}")
    if ":\\" in preview["description"]:
        why.append("description holds a full path")
    return {"pass": not why, "why": why, "alarms": triggers, "preview": preview}


def step_reminders(c: Ctx):
    up = c.get("/upcoming")
    T = c.today
    want = {(rel, f, v) for rel, t in c.kept().items() for f, v in (t["fields"] or {}).items()
            if f in ("expiry_date", "due_date", "warranty_until", "end_date") and v and -30 <= (date.fromisoformat(v) - T).days <= 90
            and not (f == "due_date" and (t["fields"] or {}).get("payment_status") == "paid")}
    items = [(c.same_record(c.rel(i["path"])), i["fact"]["field"], i["fact"]["value"]) for i in up]
    got = set(items)
    twice = [(i["document"], i["fact"]["field"]) for i in up if Counter((c.same_record(c.rel(x["path"])), x["fact"]["field"]) for x in up)[
        (c.same_record(c.rel(i["path"])), i["fact"]["field"])] > 1]  # one record listed twice, e.g. a bill and its photo
    ics = []
    for i in up:
        q = {"file_id": i["fact"]["file_id"], "field": i["fact"]["field"]}
        pv = c.http.post("/reminders/preview", json=q).json()
        r = c.http.get("/reminders/ics", params=q)
        ics.append({"file": c.rel(i["path"]), "field": q["field"], **ics_check(r.text, r.headers.get("content-type", ""), pv)})
    conflict = [{"file": c.rel(i["path"]), "value": i["fact"]["value"], "conflict": bool(i.get("conflict"))} for i in up if c.rel(i["path"]) == CNIC]
    save("reminders", {"upcoming_pass": got == want and not twice, "listed_twice": twice, "expected": sorted(want), "got": sorted(items),
                       "missing": sorted(want - got),
                       "extra": sorted(got - want), "conflict_shown": conflict, "ics": ics,
                       "ics_pass": bool(ics) and all(x["pass"] for x in ics), "google_connected": c.get("/calendar/status").get("connected")})


def step_overview(c: Ctx):
    sys.path.insert(0, str(ROOT / "backend"))
    from sniff.schemas import FIELDS, KEYS  # the field rules decide which fields are required and which are stable

    ov, T, kept = c.get("/overview"), c.today, c.kept()
    docs = {k: v for k, v in c.truth.items() if not v.get("copy_of")}
    norm = lambda v: re.sub(r"[^a-z0-9]", "", str(v).lower())  # noqa: E731
    groups = {}
    for rel, t in kept.items():
        if t["type"] in KEYS and all((t["fields"] or {}).get(k) for k in KEYS[t["type"]]):
            groups.setdefault((t["type"], *(norm(t["fields"][k]) for k in KEYS[t["type"]])), []).append(t)
    conflicts = sum(len({norm(t["fields"].get(f)) for t in g if t["fields"].get(f)}) > 1
                    for g in groups.values() if len(g) > 1 for f, s in FIELDS[g[0]["type"]].items() if s.stable and f not in KEYS[g[0]["type"]])
    exp = {"total_files": len(c.truth), "documents": len(docs), "by_type": dict(Counter(v["type"] for v in docs.values())),
           "exact_copies": sum(bool(v.get("copy_of")) for v in c.truth.values()),
           "near_duplicates": sum(bool(v.get("near_of")) for v in c.truth.values()),
           "expiring": {n: sum(0 <= (date.fromisoformat(v) - T).days <= int(n) for t in kept.values() for f, v in (t["fields"] or {}).items()
                               if f in ("expiry_date", "warranty_until", "end_date") and v) for n in ("30", "60", "90")},
           "missing_required": sum(any(s.required and f in (t["fields"] or {}) and t["fields"][f] is None for f, s in FIELDS.get(t["type"], {}).items())
                                   for t in kept.values()),  # SROIE labels have 3 fields: the others are unknown, not missing
           "conflicts": conflicts, "errors": 0}
    got = {**{k: ov[k] for k in exp if k != "missing_required"}, "missing_required": len(ov["missing_required"])}
    got["by_type"] = dict(ov["by_type"])
    check = {k: {"expected": exp[k], "got": got[k], "pass": exp[k] == got[k]} for k in exp}
    check["needs_review_matches_review_list"] = {"got": ov["needs_review"], "review_list": len(c.get("/review")),
                                                 "pass": ov["needs_review"] == len(c.get("/review"))}
    save("overview", {"pass": all(v["pass"] for v in check.values()), "checks": check, "raw": ov})


# ---------------------------------------------------------------- MCP, with a real client over stdio
def step_mcp(c: Ctx):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    cmd = c.a.mcp_cmd.split() if c.a.mcp_cmd else [sys.executable, "-m", "sniff.mcp_server"]
    env = {k: v for k, v in child_env(c.home).items() if k.startswith("SNIFF_")}
    s0 = c.get("/mcp/settings")
    default = s0["allow"]
    log0 = len(c.get("/mcp/log", limit=500))
    calls, timing, checks = [], {}, {}

    def put(enabled, allow):
        r = c.http.put("/mcp/settings", json={"enabled": enabled, "allow": allow})
        r.raise_for_status()

    def payload(res):
        sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)  # the name differs by SDK version
        if sc is not None:
            return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        out = [json.loads(x.text) for x in res.content if getattr(x, "text", None) and x.text.strip()[:1] in "[{"]
        return out[0] if len(out) == 1 else out

    async def go():
        async with stdio_client(StdioServerParameters(command=cmd[0], args=cmd[1:], env=env, cwd=str(c.home))) as (rd, wr):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                # direct children only: a venv's python.exe is a launcher whose child is the real server, and the sampler adds children
                c.mem.extra.update({f"mcp {p.pid}": p.pid for p in psutil.Process().children() if "python" in p.name().lower()})

                async def call(tool, **args):
                    t = time.perf_counter()
                    res = await s.call_tool(tool, args)
                    ms = (time.perf_counter() - t) * 1000
                    calls.append(tool)
                    timing.setdefault(tool, []).append(ms)
                    return res

                put(False, default)
                res = await call("list_documents")
                checks["refused_while_off"] = {"pass": bool(getattr(res, "is_error", None) or getattr(res, "isError", None)),
                                               "reason_shown_to_agent": "turned off" in str(res.content).lower(), "reply": str(res.content)[:200]}
                put(True, default)
                docs = payload(await call("list_documents"))
                hidden = [d for d in docs if d["type"] not in default]
                extra = [(d["file"], f) for d in docs for f in d["facts"] if f not in default.get(d["type"], [])]
                checks["allow_list_types_and_fields"] = {"pass": not hidden and not extra, "documents": len(docs), "hidden_types_seen": hidden[:5],
                                                         "fields_outside_list": extra[:5]}
                hits = payload(await call("search_documents", query="Zainab Malik identity card", k=5)) or []
                checks["search_text_only_for_full_types"] = {"pass": all("*" in default.get(h["type"], []) for h in hits), "hits": len(hits)}
                a = payload(await call("ask", question="When does Zainab Malik's ID card expire?"))
                checks["hidden_type_answer_withheld"] = {"pass": a["answer"].startswith("Withheld"), "answer": a["answer"][:200]}
                a = payload(await call("ask", question="How much did I spend on gas in 2026?"))
                checks["allowed_answer_correct"] = {"pass": any(abs(n - 8400) < 0.01 for n in numbers(a["answer"])), "answer": a["answer"][:200]}
                put(True, {**default, "id_card": ["name", "id_number", "expiry_date"]})
                cards = payload(await call("list_documents", doc_type="id_card"))
                ids = {t["fields"]["id_number"] for t in c.truth.values() if t["type"] == "id_card"}
                shown = [d["facts"].get("id_number") for d in cards if d["facts"].get("id_number")]
                checks["id_numbers_masked"] = {"pass": bool(shown) and all("•" in v and v not in ids for v in shown), "values": shown}
                put(True, default)
                for q in ("Metro Gas bill", "brownie recipe", "Voltix warranty", "fee voucher", "tenancy agreement"):
                    await call("search_documents", query=q, k=5)
                for _ in range(4):
                    await call("list_documents")
                for q in ("What was my total gas spending this year?", "How much did I spend on electricity in 2025?",
                          "What is the due date of my October 2026 gas bill?", "Which warranties end in 2027?"):
                    await call("ask", question=q)

    # which source the server runs: from home, `-m sniff.mcp_server` imports the installed package, not a sniff/ folder in the cwd
    source = subprocess.run(cmd[:1] + ["-c", "import sniff; print(sniff.__file__)"], capture_output=True, text=True, cwd=str(c.home)).stdout.strip()
    cond = conditions()
    c.mem.phase = "mcp"
    try:
        asyncio.run(go())
    finally:
        put(s0["enabled"], s0["allow"])
        c.mem.phase, c.mem.extra = None, {}
    log = c.get("/mcp/log", limit=500)
    log = log[: len(log) - log0]
    logged = [x["tool"] for x in reversed(log)]
    checks["every_call_logged"] = {"pass": logged == calls, "calls": len(calls), "logged": len(logged),
                                   "refused_logged": sum(x["refused"] for x in log)}
    save("mcp", {"conditions": cond, "command": cmd, "server_source": source, "pass": all(v["pass"] for v in checks.values()), "checks": checks,
                 "ms": {k: stats(v, 1) for k, v in timing.items()}, "memory_mb": c.mem.peak("mcp")})


# ---------------------------------------------------------------- change detection and bad input
def receipt(path: Path, token: str, extra: str | None = None, pages: int = 1) -> None:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        lines = ["QuickMart Superstore", "Shop 4, Mall Road, Lakeshore City", "Date: 27/11/2026  Time: 18:05", f"Order ref {token}",
                 "2 x Green Tea 100g   Rs. 1,100.00", "1 x Sugar 1kg   Rs. 150.00", "TOTAL   Rs. 1,250.00", "Paid by card. Thank you!"]
        if extra:
            lines.insert(4, f"Note {extra}")
        if pages > 1:
            lines = [f"Annual household report, page {i + 1} of {pages}"] + [
                f"Section {i + 1}.{k}: spending on groceries, fuel, school fees and utilities for the month, with notes on each bill." for k in range(30)]
        page = doc.new_page()
        for k, x in enumerate(lines):
            page.insert_text((50, 60 + k * 22), x, fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def step_changes(c: Ctx):
    cond = conditions()
    res = {k: [] for k in ("new", "edit", "rename", "copy", "delete_copy_kept", "delete_last")}
    found = lambda tok, rel: any(c.rel(h["path"]) == rel for h in c.get("/search", q=tok, mode="bm25"))  # noqa: E731
    paths = lambda: {c.rel(p) for d in c.get("/documents") for p in d["paths"]}  # noqa: E731
    for i in range(c.a.runs):
        tok, tok2 = f"acc{random.randrange(16**6):06x}", f"upd{random.randrange(16**6):06x}"
        p = c.docs / "Changes" / f"receipt {i + 1}.pdf"
        t = time.time()
        receipt(p, tok)
        s = c.wait(lambda: found(tok, c.rel(str(p))))
        res["new"].append({"s": s, **model_split(c.calls(t, time.time()), t, time.time())})
        t = time.time()
        receipt(p, tok, extra=tok2)
        s = c.wait(lambda: found(tok2, c.rel(str(p))))
        res["edit"].append({"s": s, **model_split(c.calls(t, time.time()), t, time.time())})
        q = p.with_name(f"receipt {i + 1} renamed.pdf")
        t = time.time()
        p.rename(q)
        s = c.wait(lambda: c.rel(str(q)) in (ps := paths()) and c.rel(str(p)) not in ps)
        res["rename"].append({"s": s, **model_split(c.calls(t, time.time()), t, time.time())})
        cp = c.docs / "Changes copy" / q.name
        cp.parent.mkdir(exist_ok=True)
        t = time.time()
        shutil.copy2(q, cp)
        s = c.wait(lambda: any(c.rel(str(q)) in {c.rel(x) for x in d["paths"]} and c.rel(str(cp)) in {c.rel(x) for x in d["paths"]}
                               for d in c.get("/documents")))
        res["copy"].append({"s": s, **model_split(c.calls(t, time.time()), t, time.time())})
        t = time.time()
        q.unlink()
        s = c.wait(lambda: c.rel(str(q)) not in (ps := paths()) and c.rel(str(cp)) in ps)
        res["delete_copy_kept"].append({"s": s, "still_found_by_search": found(tok, c.rel(str(cp)))})
        t = time.time()
        cp.unlink()
        s = c.wait(lambda: c.rel(str(cp)) not in paths() and not c.get("/search", q=tok, mode="bm25"))
        res["delete_last"].append({"s": s})
        print(f"  change run {i + 1}: " + ", ".join(f"{k} {v[-1]['s']}" for k, v in res.items()), flush=True)
    for d in ("Changes", "Changes copy"):
        shutil.rmtree(c.docs / d, ignore_errors=True)
    out = {k: {"s": stats([x["s"] for x in v]), "runs": v} for k, v in res.items()}
    out["rename_model_calls"] = sum(x["chat_calls"] + x["embed_calls"] for x in res["rename"])
    out["copy_model_calls"] = sum(x["chat_calls"] + x["embed_calls"] for x in res["copy"])
    out["new_under_target"] = sum(x["s"] is not None and x["s"] < TARGETS["new_file_s"] for x in res["new"])
    save("changes", {"conditions": cond, "method": "time from the disk change until the API shows it (search in bm25 mode for "
                     "text, /documents for paths); the folder is watched with POST /watch", **out})


def step_badinput(c: Ctx):
    import ctypes

    import pymupdf

    bad = c.docs / "Bad"
    bad.mkdir(exist_ok=True)
    stage = c.home / "bad-staging"
    stage.mkdir(exist_ok=True)
    receipt(stage / "ok.pdf", "badinputok")
    with pymupdf.open(stage / "ok.pdf") as doc:
        doc.save(stage / "protected.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    receipt(stage / "large.pdf", "badinputlarge", pages=50)
    rnd = random.Random(7)
    files = {"corrupt.pdf": b"%PDF-1.7\n" + bytes(rnd.randrange(256) for _ in range(20000)), "empty.pdf": b"",
             "protected.pdf": (stage / "protected.pdf").read_bytes(), "large.pdf": (stage / "large.pdf").read_bytes(),
             "budget.xlsx": bytes(rnd.randrange(256) for _ in range(5000)), "locked.pdf": (stage / "ok.pdf").read_bytes()}
    cond = conditions()
    t0 = time.time()
    (bad / "locked.pdf").write_bytes(files["locked.pdf"])
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    handle = k32.CreateFileW(str(bad / "locked.pdf"), 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ, no sharing, OPEN_EXISTING
    assert handle not in (None, ctypes.c_void_p(-1).value), "could not lock the file"
    for name, data in files.items():
        if name != "locked.pdf":
            (bad / name).write_bytes(data)
    rel = lambda n: f"Bad/{n}"  # noqa: E731

    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0))

    seen = {}  # file -> seconds until it first appeared in the index, with any status

    def settled():  # a scan finished after the files were written, or the watcher reported an error
        for k in c.documents():
            if k.startswith("Bad/"):
                seen.setdefault(k[4:], round(time.time() - t0, 2))
        if time.time() - t0 < 6:
            return False
        s, w = c.get("/index/status"), c.get("/watch")
        return bool(w.get("error")) or (not s.get("running") and not s.get("queued") and (w.get("last") or {}).get("at", "") >= since)
    waited = c.wait(settled, timeout=900, every=0.5)
    alive = c.http.get("/index/status").status_code == 200
    docs = c.documents()
    with c.db() as conn:
        rows = {c.rel(r["path"]): dict(r) for r in conn.execute("SELECT path, status, error, doc_type FROM files WHERE path LIKE ?", (str(bad) + "%",))}
    watch = c.get("/watch")
    result = {n: {"in_index": rel(n) in docs, "status": (rows.get(rel(n)) or {}).get("status"), "error": (rows.get(rel(n)) or {}).get("error")} for n in files}
    k32.CloseHandle(ctypes.c_void_p(handle))
    later = c.wait(lambda: (c.documents().get("Bad/locked.pdf") or {}).get("status") == "indexed", timeout=30, every=1)
    os.utime(bad / "locked.pdf")  # a change event, as when the other program saves and closes it
    after_touch = c.wait(lambda: (c.documents().get("Bad/locked.pdf") or {}).get("status") == "indexed", timeout=300, every=1)
    c.wait(lambda: not c.get("/index/status").get("running"), timeout=300)
    with c.db() as conn:  # every file once the lock is gone and the folder was scanned again
        rows = {c.rel(r["path"]): dict(r) for r in conn.execute("SELECT path, status, error FROM files WHERE path LIKE ?", (str(bad) + "%",))}
    after = {n: {"status": (rows.get(rel(n)) or {}).get("status"), "error": (rows.get(rel(n)) or {}).get("error")} for n in files}
    errors_shown = c.get("/overview")["errors"]
    expect = {"corrupt.pdf": "error", "empty.pdf": "error", "protected.pdf": "error", "locked.pdf": "error", "large.pdf": "indexed", "budget.xlsx": None}
    for n, want in expect.items():
        r = result[n]
        r["expected"] = want or "ignored"
        r["pass"] = (r["status"] == want and (want != "error" or bool(r["error"]))) if want else not r["in_index"]
    shutil.rmtree(bad, ignore_errors=True)
    shutil.rmtree(stage, ignore_errors=True)
    removed = c.wait(lambda: not any(k.startswith("Bad/") for k in c.documents()), timeout=300)
    save("badinput", {"conditions": cond, "backend_alive": alive, "settled_s": waited, "watch_error": watch.get("error"),
                      "files": result, "first_seen_s": seen, "after_unlock": after, "overview_errors_after_unlock": errors_shown,
                      "locked_indexed_after_unlock_without_event": later,
                      "locked_indexed_after_touch_s": after_touch, "bad_folder_removed_s": removed,
                      "pass": alive and all(r["pass"] for r in result.values())})


def selftest(_):
    """The checkers on known input, so a FAIL in the results is about Sniff and not about this script."""
    sys.path.insert(0, str(ROOT / "backend"))
    from sniff.ics import make_ics

    assert numbers("8,400 in total, from 2 documents. Rs. 231,800.40") == [8400, 2, 231800.4]
    assert has_date("expires on 14 Dec 2026", "2026-12-14") and has_date("due 2026-12-12.", "2026-12-12") and not has_date("2026-12-13", "2026-12-14")
    pv = {"title": "Id card expires (Bilal Ahmed)", "date": "2026-12-14", "description": "From Screenshot 2021-09-14 at 14.29.27.png, page 1. Added by Sniff."}
    good = make_ics("sniff-1@sniff.local", pv["title"], date(2026, 12, 14), pv["description"])
    assert ics_check(good, "text/calendar; charset=utf-8", pv)["pass"]
    assert not ics_check(good.replace("TRIGGER:-P1D\r\n", ""), "text/calendar", pv)["pass"]
    assert not ics_check(good, "text/calendar", {**pv, "date": "2026-12-15"})["pass"]
    c = argparse.Namespace(truth={GAS_SEP: {"type": "utility_bill", "fields": {}}, GAS_SEP_COPY: {"type": "utility_bill", "fields": {}, "copy_of": GAS_SEP},
                                  GAS_OCT: {"type": "utility_bill", "fields": {}}, GAS_OCT_PHOTO: {"type": "utility_bill", "fields": {}, "near_of": GAS_OCT}})
    c.same_record = lambda rel: Ctx.same_record(c, rel)
    c.kept = lambda: Ctx.kept(c)
    exp = {"numbers": [8400], "counted_in": [GAS_SEP, GAS_OCT, GAS_OCT_PHOTO], "not_numbers": [11400]}
    ok = {"answer": "8,400 in total, from 2 documents.", "sources": [(GAS_SEP_COPY, 1), (GAS_OCT, 1)], "conflicts": []}
    assert judge(c, ok, exp)["pass"], judge(c, ok, exp)
    twice = {**ok, "answer": "13,800 in total, from 3 documents.", "sources": [(GAS_SEP, 1), (GAS_OCT, 1), (GAS_OCT_PHOTO, 1)]}
    assert not judge(c, twice, exp)["pass"]
    inside = {**ok, "answer": "September PKR 3000.00, October PKR 8400.00: a total of PKR 11,000.00", "sources": [(GAS_OCT, 1)]}
    assert not judge(c, inside, {**exp, "largest": True})["pass"]
    print("selftest ok")


def run(a):
    c = Ctx(a)
    c.stream = "/ask/stream" in c.get("/openapi.json")["paths"]
    steps = a.steps.split(",") if a.steps else STEPS
    for s in steps:
        print(f"== {s}", flush=True)
        globals()[f"step_{s}"](c)
    c.mem.stopping.set()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # answers hold characters the Windows console code page lacks
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("docs")
    d.add_argument("--docs", required=True)
    d.add_argument("--synth", default=str(ROOT / "data" / "synth" / "out"))
    d.add_argument("--real", default=str(ROOT / "data" / "real" / "out"))
    cp = sub.add_parser("copy")
    cp.add_argument("--installed", default=r"%LOCALAPPDATA%\Sniff\backend")
    cp.add_argument("--to", required=True)
    cp.add_argument("--port", type=int, default=8790)
    st = sub.add_parser("start")
    st.add_argument("--exe", required=True)
    st.add_argument("--home", required=True)
    st.add_argument("--port", type=int, default=8790)
    st.add_argument("--runs", type=int, default=5)
    sp = sub.add_parser("stop")
    sp.add_argument("--home", required=True)
    px = sub.add_parser("proxy")
    px.add_argument("--home", required=True)
    r = sub.add_parser("run")
    r.add_argument("--url", required=True)
    r.add_argument("--docs", required=True)
    r.add_argument("--home", help="the back end's SNIFF_HOME: needed for model times, text checks and bad-input errors")
    r.add_argument("--frontend", help="a launcher build (frontend/dist) for the keystroke timing")
    r.add_argument("--steps", help=",".join(STEPS))
    r.add_argument("--runs", type=int, default=5)
    r.add_argument("--timeout", type=float, default=7200, help="seconds to wait for indexing")
    r.add_argument("--mcp-cmd", help="how to start the MCP server (default: this python -m sniff.mcp_server)")
    sub.add_parser("selftest")
    a = ap.parse_args()
    {"docs": make_docs, "copy": copy_backend, "start": start, "stop": stop, "proxy": proxy, "run": run, "selftest": selftest}[a.cmd](a)


if __name__ == "__main__":
    main()
