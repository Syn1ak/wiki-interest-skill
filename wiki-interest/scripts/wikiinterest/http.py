"""HTTP client for Wikimedia APIs: polite User-Agent, throttling, retries and a disk cache.

Every request goes through here so the rest of the code never has to think about
rate limits (HTTP 429) or repeated queries.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = SKILL_ROOT / ".cache"

# https://meta.wikimedia.org/wiki/User-Agent_policy asks for a contact in the User-Agent.
CONTACT = os.environ.get("WIKI_INTEREST_CONTACT", "https://github.com/ (wiki-interest skill)")
USER_AGENT = f"wiki-interest-skill/0.1 ({CONTACT}) python-urllib"

DAY = 24 * 3600


class ApiError(RuntimeError):
    pass


class Client:
    def __init__(self, cache_dir=None, min_interval=0.3, max_attempts=5, max_wait=90):
        self.cache_dir = Path(cache_dir or os.environ.get("WIKI_INTEREST_CACHE") or DEFAULT_CACHE)
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self.max_wait = max_wait
        self._last_request = 0.0
        self.stats = {"network": 0, "cache": 0}

    def get_json(self, url, params=None, ttl=7 * DAY):
        """GET a JSON document. Returns None on 404.

        ttl=None caches forever (use for data that cannot change, e.g. past pageviews).
        """
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        path = self._cache_path(url)
        cached = self._read_cache(path, ttl)
        if cached is not None:
            self.stats["cache"] += 1
            return cached["data"]

        status, data = self._fetch(url)
        self.stats["network"] += 1
        self._write_cache(path, url, status, data)
        return data

    def _fetch(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(1, self.max_attempts + 1):
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.status, json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return 404, None
                if e.code != 429 and e.code < 500:
                    raise ApiError(f"HTTP {e.code} for {url}") from e
                retry_after = e.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
            except urllib.error.URLError as e:
                delay = 2 ** attempt
                if attempt == self.max_attempts:
                    raise ApiError(f"Network error for {url}: {e.reason}") from e
            if attempt < self.max_attempts:
                time.sleep(min(delay, self.max_wait))
        raise ApiError(f"Gave up after {self.max_attempts} attempts (rate limited or server error): {url}")

    def _cache_path(self, url):
        h = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.json"

    @staticmethod
    def _read_cache(path, ttl):
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if ttl is not None and time.time() - entry["fetched_at"] > ttl:
            return None
        return entry

    @staticmethod
    def _write_cache(path, url, status, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"url": url, "fetched_at": time.time(), "status": status, "data": data}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False))
        tmp.replace(path)
