"""Fetch a ModelScope model repo: small files in one shot, big shards over N ranged connections.

Why two paths:

  * A few-KB config/tokenizer file wants one small request: ranged-connection machinery would
    cost more in overhead than the file is worth. So the weights take the ranged path and the
    config/tokenizer soup does not.
  * The files API publishes a `Sha256` per file that is the content hash (verified against
    the on-disk Qwen3-VL-4B download), so every file can be checked without trusting a
    length comparison.

Every file is skipped if a byte-identical copy is already present, so re-running is cheap.

No external tools: everything goes through urllib. Big shards are fetched over several ranged
connections (a single connection is throttled here) and resume across runs from a `<name>.part`
plus `<name>.part.json` pair, so an interrupted 4 GB shard continues instead of restarting.
(If you would rather delegate that to a dedicated downloader, this box also has Flower --
`~/Desktop/myProject/Flower` -- but nothing here needs it.)

    python scripts/fetch_ms_model.py Qwen/Qwen3-VL-8B-Instruct -d ..\\models\\qwen3vl-8b
"""
import argparse
import hashlib
import json
import pathlib
import sys
import threading
import time
import urllib.request

API = "https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision={rev}&Root="
FILE = "https://modelscope.cn/models/{repo}/resolve/{rev}/{path}"
MAX_STALLS = 12          # consecutive zero-progress reconnects before a range gives up (~5 min)


def sha256(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def list_files(repo, rev):
    with urllib.request.urlopen(API.format(repo=repo, rev=rev), timeout=60) as resp:
        data = json.load(resp)
    if data.get("Code") != 200:
        sys.exit(f"API said {data.get('Code')}: {data.get('Message')}")
    return [f for f in data["Data"]["Files"] if f["Type"] == "blob"]


def get_small(url, dest, want_sha, expected_size):
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
        while block := resp.read(1 << 20):
            out.write(block)
    got = sha256(tmp)
    if want_sha and got != want_sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"sha256 mismatch: {got} != {want_sha}")
    if expected_size and tmp.stat().st_size != expected_size:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch: {tmp.stat().st_size} != {expected_size}")
    tmp.replace(dest)


def get_big(url, dest, want_sha, expected_size, streams):
    """Multi-connection ranged download, resumable across runs (stdlib only).

    A single connection to ModelScope is throttled here (measured 120 KB/s .. 2 MiB/s), so the file
    is cut into `streams` ranges fetched by threads. Each range keeps its own offset in
    `<name>.part.json`, beside the pre-allocated `<name>.part`: a crash, a Ctrl-C or a dead network
    resumes from what is already on disk instead of re-fetching a 4 GB shard from zero.
    """
    part = dest.with_name(dest.name + ".part")
    side = dest.with_name(dest.name + ".part.json")
    n = max(1, min(streams, max(1, expected_size // (2 << 20))))    # never more ranges than MiB
    step = expected_size // n
    fresh = [[i * step, (expected_size - 1 if i == n - 1 else (i + 1) * step - 1), 0] for i in range(n)]
    ranges = fresh
    if part.is_file() and part.stat().st_size == expected_size and side.is_file():
        try:
            old = json.loads(side.read_text(encoding="utf-8"))
            if old.get("size") == expected_size and len(old.get("ranges", [])) == n:
                ranges = old["ranges"]          # same plan -> keep what is already on disk
        except (OSError, ValueError):
            pass
    if not part.is_file() or part.stat().st_size != expected_size:
        with open(part, "wb") as fh:
            fh.truncate(expected_size)          # sparse: NTFS does not write the zeros out
        ranges = fresh
    state = {"size": expected_size, "ranges": ranges}
    side.write_text(json.dumps(state), encoding="utf-8")
    start_done = sum(r[2] for r in ranges)
    print("   resume %.1f/%.1f MiB already on disk" % (start_done / 2**20, expected_size / 2**20), flush=True)

    lock = threading.Lock()
    t0, errors, printed = time.perf_counter(), [], [0.0]

    def report(force=False):
        done = sum(r[2] for r in ranges)
        now = time.perf_counter()
        if not force and now - printed[0] < 2:
            return
        printed[0] = now
        rate = (done - start_done) / 2**20 / max(now - t0, 1e-9)     # this run, not the resumed bytes
        eta = (expected_size - done) / 2**20 / rate / 60 if rate else 0
        print("   %8.1f/%8.1f MiB  %5.2f MiB/s  eta %4.1f min" % (done / 2**20, expected_size / 2**20, rate, eta), flush=True)

    def fetch(i):
        start, end, done = ranges[i]
        left, stall = end - start + 1, 0
        while done < left:
            before = done
            try:
                req = urllib.request.Request(url, headers={"Range": "bytes=%d-%d" % (start + done, end)})
                with urllib.request.urlopen(req, timeout=120) as resp, open(part, "r+b") as fh:
                    fh.seek(start + done)
                    while done < left:
                        block = resp.read(1 << 20)
                        if not block:
                            break                   # server closed early -> reconnect below, same offset
                        fh.write(block)
                        done += len(block)
                        ranges[i][2] = done
                        with lock:
                            if done % (8 << 20) < (1 << 20):
                                side.write_text(json.dumps(state), encoding="utf-8")
                            report()
            except Exception as exc:  # noqa: BLE001
                errors.append("range %d at %d: %s" % (i, done, exc))
            if done == before:
                # A round that moved nothing is the failure this loop retries: ModelScope drops
                # parallel connections under load (measured on a 942 MiB shard: 16 ranges, several
                # of them wedged with zero progress while others ran). Back off, then reconnect.
                stall += 1
                if stall > MAX_STALLS:
                    errors.append("range %d gave up at %d/%d" % (i, done, left))
                    return
                time.sleep(min(2 ** stall, 30))
            else:
                stall = 0

    threads = [threading.Thread(target=fetch, args=(i,), daemon=True) for i in range(len(ranges))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with lock:
        report(force=True)
    if sum(r[2] for r in ranges) < expected_size:
        raise RuntimeError("incomplete: " + "; ".join(errors[-3:] or ["ranges did not finish"]))
    size = part.stat().st_size
    if expected_size and size != expected_size:
        raise RuntimeError("size mismatch: %d != %d" % (size, expected_size))
    if want_sha and sha256(part) != want_sha:
        got = sha256(part)
        part.unlink(missing_ok=True)
        side.unlink(missing_ok=True)
        raise RuntimeError("sha256 mismatch: %s != %s" % (got, want_sha))
    side.unlink(missing_ok=True)
    part.replace(dest)


def verified(dest: pathlib.Path, size: int, want: str) -> bool:
    """True when the destination is already the file we asked for, byte for byte."""
    return dest.exists() and dest.stat().st_size == size and (not want or sha256(dest) == want)


def main():
    # The console code page is GBK on this box: the CJK path prints as mojibake and any replacement
    # character from a half-decoded child line would raise on print instead.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("-d", "--dir", required=True)
    ap.add_argument("--rev", default="master")
    ap.add_argument("--streams", type=int, default=8,
                    help="parallel ranged connections (8 is what ModelScope tolerated here; 16 wedged ranges)")
    ap.add_argument("--small-mb", type=float, default=10.0, help="below this, one plain request instead of ranged connections")
    ap.add_argument("--retries", type=int, default=3)
    a = ap.parse_args()

    dest_dir = pathlib.Path(a.dir).resolve()
    files = sorted(list_files(a.repo, a.rev), key=lambda f: f["Size"])
    total = sum(f["Size"] for f in files)
    print(f"{a.repo}@{a.rev}: {len(files)} files, {total / 2**30:.2f} GiB -> {dest_dir}", flush=True)

    small_cut = a.small_mb * 2**20
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        name, size, want = f["Path"], f["Size"], f.get("Sha256")
        dest = dest_dir / pathlib.Path(name).name
        if verified(dest, size, want):
            print(f"skip  {name} (already verified)", flush=True)
            continue
        url = FILE.format(repo=a.repo, rev=a.rev, path=name)
        kind = "urllib" if size < small_cut else f"urllib x{a.streams} ranges"
        print(f"get   {name}  {size / 2**20:9.1f} MiB  via {kind}", flush=True)
        t0 = time.perf_counter()
        for attempt in range(1, a.retries + 1):
            try:
                if verified(dest, size, want):
                    # A retry after a crash in the parse below, or a child that finished while
                    # its parent was dying: the file is here already. Re-fetching a 4.9 GB shard
                    # because the *reporting* broke is the expensive kind of wrong.
                    print(f"   attempt {attempt}: 已经在盘上且校验通过，跳过", flush=True)
                    break
                if size < small_cut:
                    get_small(url, dest, want, size)
                else:
                    get_big(url, dest, want, size, a.streams)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"   attempt {attempt} failed: {exc}", flush=True)
                if attempt == a.retries:
                    raise
                time.sleep(5)
        dt = time.perf_counter() - t0
        print(f"ok    {name}  {dt:.1f}s  {size / 2**20 / max(dt, 1e-9):.2f} MiB/s", flush=True)

    bad = []
    for f in files:
        dest = dest_dir / pathlib.Path(f["Path"]).name
        if not dest.exists() or (f.get("Sha256") and sha256(dest) != f["Sha256"]):
            bad.append(f["Path"])
    print(f"\nverified {len(files) - len(bad)}/{len(files)} files" + (f", BAD: {bad}" if bad else ""), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
