"""
Small HTTP client shared by all providers: disk cache with TTL, gzip support,
retries with back-off and per-host politeness delay. Standard library only.

The cache makes runs reproducible (the raw payload behind every number is
kept on disk) and keeps us within free-tier / fair-access limits.
"""
import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import config

_last_call = {}
MIN_INTERVAL = {"data.sec.gov": 0.15, "www.sec.gov": 0.15}   # SEC: max 10 req/s


class FetchError(RuntimeError):
    pass


def _cache_path(url):
    root = Path(config.get("http.cache_dir", ".cache"))
    if not root.is_absolute():
        root = config.ROOT / root
    h = hashlib.sha256(url.encode()).hexdigest()[:24]
    return root / urlparse(url).netloc / h


def fetch_bytes(url, ttl=3600, headers=None, retries=3, offline=False):
    """Return (body, fetched_at_epoch). Serves from cache when fresh or offline."""
    path = _cache_path(url)
    meta = path.with_suffix(".json")
    if path.exists() and meta.exists():
        info = json.loads(meta.read_text())
        if offline or time.time() - info["fetched_at"] < ttl:
            return path.read_bytes(), info["fetched_at"]
    if offline:
        raise FetchError("offline and not cached: %s" % url)

    host = urlparse(url).netloc
    wait = MIN_INTERVAL.get(host, 0) - (time.time() - _last_call.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    hdrs = {"User-Agent": "Mozilla/5.0 (Lunoviq)", "Accept-Encoding": "gzip, deflate"}
    hdrs.update(headers or {})
    err, body = None, None
    for attempt in range(retries):
        try:
            _last_call[host] = time.time()
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=30) as r:
                body = r.read()
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            break
        except (urllib.error.URLError, TimeoutError) as e:
            err = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                break
            time.sleep(1.5 * (attempt + 1))
    if body is None:
        raise FetchError("%s: %s" % (url, err))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    now = time.time()
    meta.write_text(json.dumps({"url": url, "fetched_at": now}))
    return body, now


def fetch_json(url, **kw):
    body, ts = fetch_bytes(url, **kw)
    return json.loads(body), ts
