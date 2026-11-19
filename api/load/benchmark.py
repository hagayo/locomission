"""Read-only HTTP load probe (stdlib). Never uploads photos or incurs AI costs.

Examples:
  python -m load.benchmark --url http://127.0.0.1:8000/api/health
  python -m load.benchmark --url http://127.0.0.1:8000/api/lobby \
      --auth-from-env --workers 12 --requests 240

Do not run against a live/public deployment without explicit --allow-remote.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from math import ceil


def percentile(sorted_values: list[float], proportion: float) -> float:
    return sorted_values[max(0, ceil(len(sorted_values) * proportion) - 1)]


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("--url", default="http://127.0.0.1:8000/api/health")
    cli.add_argument("--workers", type=int, default=12)
    cli.add_argument("--requests", type=int, default=240)
    cli.add_argument("--auth-from-env", action="store_true")
    cli.add_argument("--allow-remote", action="store_true")
    args = cli.parse_args()
    target = urllib.parse.urlparse(args.url)
    if target.scheme not in ("http", "https"):
        cli.error("Expected HTTP(S) URL")
    if target.hostname not in ("127.0.0.1", "localhost") and not args.allow_remote:
        cli.error("Remote load tests require explicit --allow-remote")
    if target.path not in ("/api/health", "/api/lobby", "/api/presence"):
        cli.error("Only read-only API endpoints are permitted")
    if not 1 <= args.workers <= 64 or not 1 <= args.requests <= 10000:
        cli.error("Workers 1-64 and requests 1-10000")
    headers = {"Accept": "application/json"}
    if args.auth_from_env:
        jwt = os.getenv("LOCOMISSION_LOAD_CLERK_JWT")
        session = os.getenv("LOCOMISSION_LOAD_GAME_SESSION")
        if not jwt or not session:
            cli.error("Set LOCOMISSION_LOAD_CLERK_JWT and LOCOMISSION_LOAD_GAME_SESSION")
        headers.update({"Authorization": "Bearer " + jwt,
                        "X-Game-Session": session})
    elif target.path != "/api/health":
        cli.error("Protected endpoints require --auth-from-env")

    def probe(_index: int) -> tuple[float, int]:
        req = urllib.request.Request(args.url, headers=headers, method="GET")
        begin = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read(1024)
                code = response.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except (urllib.error.URLError, TimeoutError, OSError):
            code = 0
        return ((time.perf_counter() - begin) * 1000, code)

    begin = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(probe, range(args.requests)))
    duration = max(time.perf_counter() - begin, 1e-9)
    latencies = sorted(p for p, _ in results)
    codes = {}
    for _, code in results:
        codes[str(code)] = codes.get(str(code), 0) + 1
    print(json.dumps({
        "url_path": target.path, "requests": args.requests,
        "workers": args.workers, "duration_s": round(duration, 3),
        "rps": round(len(results) / duration, 1),
        "p50_ms": round(statistics.median(latencies), 2),
        "p95_ms": round(percentile(latencies, .95), 2),
        "p99_ms": round(percentile(latencies, .99), 2),
        "statuses": codes,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
