import json
import html as html_module
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
FANDOM_API = "https://lol.fandom.com/api.php"
OFFICIAL_SCHEDULE_URL = "https://lolesports.com/en-US/schedule"
FEED_API = "https://feed.lolesports.com/livestats/v1"
TEAM_MATCH_HISTORY_PAGES = {"T1": "T1/Match_History"}
CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "matches": [],
    "stage": {"page": "", "label": "", "year": "", "key": "", "refreshedAt": ""},
}
SCHEDULE_CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "matches": [],
}
OFFICIAL_CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "by_key": {},
    "by_id": {},
    "diagnostics": {},
}
DETAIL_CACHE = {}
LOGO_CACHE = {}
STANDINGS_CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "rows": [],
    "competition": {"league": "LCK", "label": "LCK", "stage": ""},
}
DEFAULT_STAGE_SUFFIX = "Rounds_3-4"  # Only used when Leaguepedia is unavailable.
HONG_KONG = timezone(timedelta(hours=8))
TEAM_CODE_ALIASES = {
    "BILIBILI": "BLG",
    "BILIBILIGAMING": "BLG",
    "BNKFEARX": "BFX",
    "BNKFEAR": "BFX",
    "FEARX": "BFX",
    "DPLUSKIA": "DK",
    "DPLUS": "DK",
    "DNFREECS": "DNS",
    "DNF": "DNS",
    "DRX": "DRX",
    "GENG": "GEN",
    "GENGESPORTS": "GEN",
    "GEN.G": "GEN",
    "GEN.GESPORTS": "GEN",
    "HANWHALIFEESPORTS": "HLE",
    "HANWHA": "HLE",
    "HANWHALIFE": "HLE",
    "KTROLSTER": "KT",
    "NONGSHIMREDFORCE": "NS",
    "NONGSHIM": "NS",
    "OKSAVINGSBANKBRION": "BRO",
    "BRION": "BRO",
    "T1ESPORTS": "T1",
}
STAGE_CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "pages": [],
    "season_year": None,
}
OFFICIAL_CACHE_LOCK = Lock()
STAGE_CACHE_LOCK = Lock()
CACHE_LOCK = Lock()
SCHEDULE_CACHE_LOCK = Lock()
STANDINGS_CACHE_LOCK = Lock()
API_LOAD_TIMEOUT_SECONDS = 20


def run_with_timeout(fn, timeout_seconds=API_LOAD_TIMEOUT_SECONDS):
    """Run callable in a worker thread; raise TimeoutError if it exceeds the limit.

    Do not use ``with ThreadPoolExecutor`` here: on timeout the context manager
    calls shutdown(wait=True) and blocks until the hung worker finishes, which
    defeats the timeout and leaves API clients with 0 bytes for minutes.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as error:
            future.cancel()
            raise TimeoutError(
                f"Operation timed out after {timeout_seconds}s"
            ) from error
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def is_allowed_logo_host(hostname):
    if not hostname:
        return False
    host = str(hostname).lower().rstrip(".")
    if host == "static.wikia.nocookie.net" or host.endswith(".wikia.nocookie.net"):
        return True
    if host == "lol.fandom.com" or host.endswith(".fandom.com"):
        return True
    return False
