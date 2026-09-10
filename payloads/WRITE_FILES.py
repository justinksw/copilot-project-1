#!/usr/bin/env python3
import base64, gzip, json, pathlib
root = pathlib.Path(__file__).resolve().parents[1]
payloads = root / "payloads"
manifest = json.loads((payloads / "manifest.json").read_text())
for name, n in manifest.items():
    b64 = "".join((payloads / name / f"{i:02d}.b64").read_text().strip() for i in range(n))
    data = gzip.decompress(base64.b64decode(b64))
    (root / name).write_bytes(data)
    print("wrote", name, len(data))
print("done")
