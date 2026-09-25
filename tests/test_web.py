"""Web app API tests: a real server on a free port, pipeline stubbed (no network, no Excel)."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from lunoviq.web import server


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    out = tmp_path_factory.mktemp("output")
    run_dir = out / "KO_20260925-0938"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(json.dumps({
        "ticker": "KO", "company": "COCA COLA CO", "generated": "2026-09-25T09:38:00", "price": 88.1,
        "methods": [{"key": "dcf_perpetuity", "value": 54.5}], "health": {"Overall": "OK"}, "errors": []}))
    (run_dir / "KO_Model.xlsx").write_bytes(b"PK-fake-xlsx")

    server.output_root = lambda: out
    server._tickers = [("KO", "COCA COLA CO"), ("KOF", "Coca-Cola FEMSA"), ("KDP", "Keurig Dr Pepper"),
                       ("MNST", "Monster Beverage Corp")]

    def fake_run(ticker, sets=(), progress=None, **kw):
        for s in ("fundamentals", "market", "excel", "peers", "recalc", "audit"):
            progress(s)
        if ticker == "XOM":
            from lunoviq.providers.sec_xbrl import DataError
            raise DataError("no 10-K")
        return {"model": str(run_dir / "KO_Model.xlsx"), "summary": str(run_dir / "summary.json"), "sets": sets}

    import lunoviq.pipeline
    orig = lunoviq.pipeline.run
    lunoviq.pipeline.run = fake_run
    threading.Thread(target=server.worker, daemon=True).start()
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    lunoviq.pipeline.run = orig


def call(base, path, body=None):
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        return e.code, (json.loads(raw) if e.headers.get_content_type() == "application/json" else raw)


def wait_job(base, job):
    for _ in range(100):
        code, j = call(base, "/api/runs/" + job)
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_index_is_served_with_cache_busted_assets(base):
    code, body = call(base, "/")
    assert code == 200 and b'/app.js?v=' in body and b'/app.css?v=' in body


def test_search_exact_then_prefix_then_name(base):
    _, res = call(base, "/api/tickers?q=ko")
    assert [r["ticker"] for r in res][:2] == ["KO", "KOF"]
    _, res = call(base, "/api/tickers?q=monster")
    assert res[0]["ticker"] == "MNST"


@pytest.mark.parametrize("body,msg", [
    ({"ticker": "../etc"}, "Geçerli bir hisse kodu"),
    ({"ticker": ""}, "Geçerli bir hisse kodu"),
    ({"ticker": "KO", "overrides": {"terminal_growth": 0.2}}, "Uzun vadeli büyüme"),
    ({"ticker": "KO", "overrides": {"exit_multiple": "abc"}}, "Çıkış çarpanı bir sayı"),
])
def test_invalid_requests_rejected_in_turkish(base, body, msg):
    code, res = call(base, "/api/runs", body)
    assert code == 400 and msg in res["error"]


def test_run_with_override_reports_steps_and_passes_named_range(base):
    code, res = call(base, "/api/runs", {"ticker": "ko", "overrides": {"revenue_growth": 0.06, "x": 1}})
    assert code == 202
    j = wait_job(base, res["job"])
    assert j["status"] == "done" and j["step"] == "audit" and j["run"] == "KO_20260925-0938"
    assert j["summary"]["ticker"] == "KO"


def test_data_error_is_friendly_and_not_retryable(base):
    _, res = call(base, "/api/runs", {"ticker": "XOM"})
    j = wait_job(base, res["job"])
    assert j["status"] == "error" and "10-K" in j["error"] and j["retryable"] is False


def test_history_summary_download(base):
    _, rows = call(base, "/api/history")
    assert rows[0]["ticker"] == "KO" and rows[0]["healthy"]
    code, s = call(base, "/api/summary?run=KO_20260925-0938")
    assert code == 200 and s["run"] == "KO_20260925-0938"
    code, data = call(base, "/api/download?run=KO_20260925-0938")
    assert code == 200 and data == b"PK-fake-xlsx"


@pytest.mark.parametrize("path", ["/api/summary?run=../../etc", "/api/download?run=..%2F..%2Fconfig",
                                  "/../../config/lunoviq.toml", "/%2e%2e/%2e%2e/config/lunoviq.toml"])
def test_path_traversal_blocked(base, path):
    code, _ = call(base, path)
    assert code == 404
