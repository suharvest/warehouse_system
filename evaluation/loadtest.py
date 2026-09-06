#!/usr/bin/env python3
"""A2 边界压测脚本 — 库存查询 / 库存更新 / 任务创建(=stock-in) 分级加压。

用法:
  uv run --with httpx --with anyio evaluation/loadtest.py \
      --base-url http://100.92.125.65:18025 \
      --scenario query --concurrency 1,5,10,20,50 --duration 60 \
      --out evaluation/runs/2026-09-05-load/raw

scenario: query | stock_in | stock_out
"""
import argparse
import asyncio
import json
import os
import statistics
import time
from datetime import datetime, timezone

import httpx


async def worker(client, url, method, json_body, headers, stop_at, results, errors, status_codes):
    while time.monotonic() < stop_at:
        t0 = time.monotonic()
        try:
            if method == "GET":
                r = await client.get(url, headers=headers, timeout=10.0)
            else:
                r = await client.post(url, json=json_body, headers=headers, timeout=10.0)
            dt = (time.monotonic() - t0) * 1000
            results.append(dt)
            status_codes[r.status_code] = status_codes.get(r.status_code, 0) + 1
            if r.status_code >= 400:
                errors.append(r.status_code)
        except Exception as e:
            dt = (time.monotonic() - t0) * 1000
            results.append(dt)
            errors.append(str(type(e).__name__))
            status_codes["EXC"] = status_codes.get("EXC", 0) + 1


def pct(data, p):
    if not data:
        return None
    data = sorted(data)
    k = int(len(data) * p)
    k = min(k, len(data) - 1)
    return data[k]


async def run_level(base_url, scenario, concurrency, duration, headers):
    if scenario == "query":
        url = f"{base_url}/api/materials/list"
        method = "GET"
        body = None
    elif scenario == "stock_in":
        url = f"{base_url}/api/materials/stock-in"
        method = "POST"
        body = {
            "product_name": "seed-material-2",
            "quantity": 1,
            "reason_category": "purchase",
            "warehouse_id": 1,
        }
    elif scenario == "stock_out":
        url = f"{base_url}/api/materials/stock-out"
        method = "POST"
        body = {
            "product_name": "seed-material-1",
            "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": 1,
        }
    else:
        raise ValueError(scenario)

    results, errors, status_codes = [], [], {}
    async with httpx.AsyncClient(trust_env=False) as client:
        stop_at = time.monotonic() + duration
        tasks = [
            asyncio.create_task(
                worker(client, url, method, body, headers, stop_at, results, errors, status_codes)
            )
            for _ in range(concurrency)
        ]
        await asyncio.gather(*tasks)

    n = len(results)
    err_n = len(errors)
    summary = {
        "scenario": scenario,
        "concurrency": concurrency,
        "duration_s": duration,
        "requests_total": n,
        "throughput_rps": round(n / duration, 2) if duration else None,
        "error_count": err_n,
        "error_rate_pct": round(100 * err_n / n, 2) if n else None,
        "p50_ms": round(pct(results, 0.50), 1) if results else None,
        "p95_ms": round(pct(results, 0.95), 1) if results else None,
        "p99_ms": round(pct(results, 0.99), 1) if results else None,
        "status_codes": status_codes,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    return summary


async def main_async(args):
    headers = {}
    if args.api_key:
        headers["X-API-Key"] = args.api_key

    os.makedirs(args.out, exist_ok=True)
    levels = [int(x) for x in args.concurrency.split(",")]
    all_summaries = []
    for c in levels:
        print(f"=== scenario={args.scenario} concurrency={c} duration={args.duration}s ===", flush=True)
        summary = await run_level(args.base_url, args.scenario, c, args.duration, headers)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        all_summaries.append(summary)
        # small gap between levels to let server recover / connections close
        await asyncio.sleep(3)

    out_path = os.path.join(args.out, f"{args.scenario}_summary.json")
    with open(out_path, "w") as f:
        json.dump(all_summaries, f, ensure_ascii=False, indent=2)
    print(f"written: {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--scenario", required=True, choices=["query", "stock_in", "stock_out"])
    ap.add_argument("--concurrency", default="1,5,10,20,50")
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
