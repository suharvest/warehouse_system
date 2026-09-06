#!/usr/bin/env python3
"""离线场景模拟：持续发请求的同时人为断网 30s 再恢复，统计丢请求情况。

用法:
  uv run --with httpx evaluation/offline_test.py \
      --base-url http://localhost:18025 --api-key <key> \
      --out evaluation/runs/2026-09-05-load/raw-mac-local

配合外部脚本，在本进程运行期间对目标端口做网络中断（iptables DROP 或
docker network disconnect），中断窗口由调用方控制，本脚本只负责在中断
前后持续发请求并记录每一秒的成功/失败计数，用于判断请求是被"排队补发"
还是直接丢弃/报错（结论写进 summary，不在本脚本里做判断）。
"""
import argparse
import asyncio
import json
import time
from datetime import datetime, timezone

import httpx


async def main_async(args):
    url = f"{args.base_url}/api/materials/list"
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    per_second = []
    end_at = time.monotonic() + args.total_duration

    async with httpx.AsyncClient(trust_env=False) as client:
        while time.monotonic() < end_at:
            sec_start = time.monotonic()
            ok = 0
            fail = 0
            fail_reasons = {}
            # fire requests for ~1s window at fixed rate
            while time.monotonic() - sec_start < 1.0:
                try:
                    r = await client.get(url, headers=headers, timeout=2.0)
                    if r.status_code == 200:
                        ok += 1
                    else:
                        fail += 1
                        fail_reasons[str(r.status_code)] = fail_reasons.get(str(r.status_code), 0) + 1
                except Exception as e:
                    fail += 1
                    k = type(e).__name__
                    fail_reasons[k] = fail_reasons.get(k, 0) + 1
                await asyncio.sleep(0.05)  # ~20 req/s per second bucket
            per_second.append({
                "t": round(time.monotonic() - (end_at - args.total_duration), 1),
                "ok": ok,
                "fail": fail,
                "fail_reasons": fail_reasons,
            })
            print(json.dumps(per_second[-1], ensure_ascii=False), flush=True)

    summary = {
        "total_duration_s": args.total_duration,
        "buckets": per_second,
        "total_ok": sum(b["ok"] for b in per_second),
        "total_fail": sum(b["fail"] for b in per_second),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/offline_summary.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("written:", f"{args.out}/offline_summary.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--total-duration", type=int, default=60)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
