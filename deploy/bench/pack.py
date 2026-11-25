"""Generate the catalog on the loading host, then feed it to register.py inside the API."""

from benchmark import queries, SIZES, ROOT
import json

counts = json.loads((ROOT / "loaded.json").read_text())
timings = json.loads((ROOT / "timings.json").read_text())
packs = {}
for name in SIZES:
    statements = dict(queries(name))
    if set(statements) != set(timings[name]) or any(
        "error" in r for r in timings[name].values()
    ):
        raise ValueError(f"Unverified pack: {name}")
    packs[name] = dict(counts=counts[name], queries=statements, timings=timings[name])
(ROOT / "packs.json").write_text(json.dumps(packs))
