from pathlib import Path
checks = {
  "app.js": 26745,
  "test_app.js": 5577,
  "test_server.py": 29900,
  "server.py": 60925,
}
for name, size in checks.items():
  data = Path(name).read_bytes()
  assert len(data) == size, (name, len(data), size)
app = Path("app.js").read_text()
assert "fetchWithTimeout" in app and "scheduleError" in app
assert app.rstrip().endswith("refreshMatches();")
ta = Path("test_app.js").read_text()
assert "FETCH_TIMEOUT_MS" in ta and "AbortController" in ta
srv = Path("server.py").read_text()
for m in ["run_with_timeout", "is_allowed_logo_host", "API_LOAD_TIMEOUT_SECONDS", "send_response(504)"]:
  assert m in srv, m
ts = Path("test_server.py").read_text()
for m in ["is_allowed_logo_host", "run_with_timeout", "stale"]:
  assert m in ts, m
print("markers OK")
