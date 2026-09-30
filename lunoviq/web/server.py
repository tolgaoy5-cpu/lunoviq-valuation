"""
Local web app: `python -m lunoviq serve` -> http://127.0.0.1:8765

Standard library only. Binds to localhost. Model builds run one at a time on
a worker thread (Excel recalculates one workbook at a time); the browser polls
job status.

API
    GET  /api/tickers?q=ko            ticker / company search (SEC list)
    POST /api/runs  {ticker, overrides} -> {job}
    GET  /api/runs/<job>              status, current step, summary when done
    GET  /api/history                 previous runs (newest first)
    GET  /api/summary?run=<dir>       summary of a previous run
    GET  /api/download?run=<dir>      the Excel model
    POST /api/open {run}              open the model in Excel (macOS)
    POST /api/quit                    stop the server (from the UI's Quit link)
"""
import json
import queue
import re
import subprocess
import sys
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import config

STATIC = Path(__file__).resolve().parent / "static"
RUN_DIR_RE = re.compile(r"^[A-Z0-9.\-]{1,10}_\d{8}-\d{4,6}$")
TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")
# UI field -> (named range, lower bound, upper bound)
OVERRIDES = {
    "year1_growth": ("drv_Year1Growth", -0.30, 0.60),
    "revenue_growth": ("gm_RevenueGrowth", -0.30, 0.60),
    "terminal_growth": ("val_TerminalGrowth", -0.02, 0.05),
    "exit_multiple": ("val_ExitMultiple", 1.0, 80.0),
}
LABELS = {"year1_growth": "Year-1 growth", "revenue_growth": "Revenue growth", "terminal_growth": "Terminal growth",
          "exit_multiple": "Exit multiple"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}

JOBS = {}
QUEUE = queue.Queue()


def output_root():
    p = Path(config.get("paths.output_dir", "output"))
    return p if p.is_absolute() else config.ROOT / p


def friendly_error(exc):
    """-> (message for the user, technical detail, retry makes sense?)"""
    from ..providers.sec_xbrl import DataError
    msg = str(exc)
    if isinstance(exc, DataError):
        return "SEC does not publish enough annual (10-K) data for this company to build the model.", msg, False
    if isinstance(exc, LookupError) and "Ticker not found" in msg:
        return ("This ticker is not in the SEC list. Only US companies that file with the SEC are "
                "supported.", msg, False)
    from ..excel.recalc import RecalcTimeout
    if isinstance(exc, RecalcTimeout):
        return ("Excel did not respond. A dialog may be open in Excel (for example document recovery "
                "or a file warning): close it, then try again.", msg, True)
    if "Yahoo" in msg or "finance.yahoo" in msg:
        return "The share price could not be fetched (Yahoo). Try again in a few minutes.", msg, True
    if "sec.gov" in msg:
        return "SEC could not be reached. Check your internet connection and try again.", msg, True
    return "An unexpected error occurred while building the model.", msg, True


def worker():
    from .. import pipeline
    while True:
        job_id = QUEUE.get()
        job = JOBS[job_id]
        job["status"] = "running"
        try:
            sets = ["%s=%s" % (OVERRIDES[k][0], v) for k, v in job["overrides"].items()]
            res = pipeline.run(job["ticker"], sets=sets,
                               progress=lambda s: job.__setitem__("step", s))
            job["run"] = Path(res["model"]).parent.name
            job["summary"] = json.loads(Path(res["summary"]).read_text())
            job["status"] = "done"
        except Exception as e:                       # noqa: BLE001 - reported to the UI
            job["error"], job["detail"], job["retryable"] = friendly_error(e)
            job["status"] = "error"                          # last: pollers must see the message with it
            traceback.print_exc()
        finally:
            QUEUE.task_done()


_tickers = None


def ticker_list():
    global _tickers
    if _tickers is None:
        from .. import http
        from ..providers import sec_xbrl
        data, _ = http.fetch_json(sec_xbrl.TICKERS, headers={"User-Agent": config.sec_user_agent()},
                                  ttl=7 * 86400)
        _tickers = [(r["ticker"].upper(), r["title"]) for r in data.values()]
    return _tickers


def search(q, limit=8):
    q = q.strip().upper()
    if not q:
        return []
    exact, starts, name = [], [], []
    for t, n in ticker_list():
        if t == q:
            exact.append((t, n))
        elif t.startswith(q):
            starts.append((t, n))
        elif q in n.upper():
            name.append((t, n))
    starts.sort(key=lambda x: len(x[0]))
    return [{"ticker": t, "name": n} for t, n in (exact + starts + name)[:limit]]


def history():
    rows = []
    for f in output_root().glob("*/summary.json"):
        if not RUN_DIR_RE.match(f.parent.name):
            continue
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        rows.append({"run": f.parent.name, "ticker": d.get("ticker"), "company": d.get("company"),
                     "generated": d.get("generated"), "price": d.get("price"),
                     "methods": {m["key"]: m["value"] for m in d.get("methods", [])},
                     "healthy": d.get("health", {}).get("Overall") == "OK" and not d.get("errors")})
    return sorted(rows, key=lambda r: r["generated"] or "", reverse=True)


def run_path(run):
    if not run or not RUN_DIR_RE.match(run):
        return None
    p = output_root() / run
    return p if p.is_dir() else None


class Handler(BaseHTTPRequestHandler):
    server_version = "Lunoviq"

    def log_message(self, fmt, *args):             # quiet console
        if "/api/runs/" not in str(args[0] if args else ""):
            sys.stderr.write("  %s\n" % (fmt % args))

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/tickers":
            try:
                return self._json(search(q.get("q", "")))
            except Exception:                        # noqa: BLE001
                return self._json({"error": "The company list could not be loaded."}, 503)
        if u.path.startswith("/api/runs/"):
            job = JOBS.get(u.path.rsplit("/", 1)[-1])
            if not job:
                return self._json({"error": "Job not found."}, 404)
            pos = list(QUEUE.queue).index(job["id"]) + 1 if job["id"] in list(QUEUE.queue) else 0
            return self._json({k: v for k, v in job.items() if k != "overrides"} | {"queue_position": pos})
        if u.path == "/api/history":
            return self._json(history())
        if u.path == "/api/summary":
            p = run_path(q.get("run"))
            if not p or not (p / "summary.json").exists():
                return self._json({"error": "Run not found."}, 404)
            return self._json(json.loads((p / "summary.json").read_text()) | {"run": p.name})
        if u.path == "/api/download":
            p = run_path(q.get("run"))
            files = list(p.glob("*_Model.xlsx")) if p else []
            if not files:
                return self._json({"error": "File not found."}, 404)
            data = files[0].read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % files[0].name)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        # static files
        name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = (STATIC / name).resolve()
        if STATIC not in f.parents and f != STATIC / "index.html" or not f.is_file():
            self.send_error(404)
            return None
        data = f.read_bytes()
        if f.name == "index.html":                     # cache-bust assets after every change
            for asset in ("app.css", "app.js"):
                ver = int((STATIC / asset).stat().st_mtime)
                data = data.replace(b'"/%s"' % asset.encode(), b'"/%s?v=%d"' % (asset.encode(), ver))
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)
        return None

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        if u.path == "/api/runs":
            ticker = str(body.get("ticker", "")).strip().upper()
            if not TICKER_RE.match(ticker):
                return self._json({"error": "Enter a valid ticker (e.g. KO, MSFT)."}, 400)
            overrides = {}
            for k, v in (body.get("overrides") or {}).items():
                if k not in OVERRIDES or v in (None, ""):
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return self._json({"error": "%s must be a number." % LABELS[k]}, 400)
                lo, hi = OVERRIDES[k][1:]
                if not lo <= v <= hi:
                    fmt = (lambda x: "%gx" % x) if k == "exit_multiple" else (lambda x: "%g%%" % (x * 100))
                    return self._json({"error": "%s must be between %s and %s." % (LABELS[k], fmt(lo), fmt(hi))}, 400)
                overrides[k] = v
            if "terminal_growth" in overrides and overrides["terminal_growth"] >= 0.05:
                return self._json({"error": "Terminal growth must be below WACC."}, 400)
            job_id = uuid.uuid4().hex[:12]
            JOBS[job_id] = {"id": job_id, "ticker": ticker, "overrides": overrides, "status": "queued",
                            "step": None, "error": None}
            QUEUE.put(job_id)
            return self._json({"job": job_id}, 202)
        if u.path == "/api/quit":
            busy = any(j["status"] in ("queued", "running") for j in JOBS.values())
            if busy and not body.get("force"):
                return self._json({"error": "A model is still being built. Quit anyway?", "busy": True}, 409)
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        if u.path == "/api/open":
            p = run_path(body.get("run"))
            files = list(p.glob("*_Model.xlsx")) if p else []
            if not files:
                return self._json({"error": "File not found."}, 404)
            if sys.platform == "darwin":
                subprocess.run(["open", str(files[0])], check=False)
                return self._json({"ok": True})
            return self._json({"error": "Opening in Excel is supported on macOS only; download the file instead."}, 501)
        return self._json({"error": "Unknown request."}, 404)


def serve(port=8765, open_browser=True):
    threading.Thread(target=worker, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d" % port
    print("Lunoviq is running at %s   (Ctrl+C to stop)" % url)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
