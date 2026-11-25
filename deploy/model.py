"""Download and verify the pinned CPU model, resuming completed range requests."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen
import hashlib, time, os

URL = "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf"
SIZE = 1117320736
SHA = "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"
DIR = Path("llm")
DIR.mkdir(exist_ok=True)
STEP = 16 * 1024**2


def fetch(start):
    end = min(start + STEP, SIZE) - 1
    path = DIR / f"part-{start}"
    if path.exists() and path.stat().st_size == end - start + 1:
        return path
    for attempt in range(4):
        try:
            req = Request(
                URL + f"?chunk={start}", headers={"Range": f"bytes={start}-{end}"}
            )
            with urlopen(req, timeout=90) as r:
                if r.status != 206 or not r.headers["Content-Range"].startswith(
                    f"bytes {start}-{end}/"
                ):
                    raise ValueError("range mismatch")
                data = r.read(end - start + 2)
            if len(data) != end - start + 1:
                raise ValueError("short range")
            path.write_bytes(data)
            return path
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2)


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(2**20):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    target = DIR / "qwen.gguf"
    if target.exists() and hash_file(target) == SHA:
        raise SystemExit(0)
    with ThreadPoolExecutor(max_workers=10) as pool:
        parts = list(pool.map(fetch, range(0, SIZE, STEP)))
    h = hashlib.sha256()
    with (DIR / "qwen.verified").open("wb") as dst:
        for p in parts:
            data = p.read_bytes()
            h.update(data)
            dst.write(data)
    if h.hexdigest() != SHA:
        raise ValueError("model hash mismatch")
    os.replace(DIR / "qwen.verified", target)
    for p in parts:
        p.unlink()
    print(SHA)
