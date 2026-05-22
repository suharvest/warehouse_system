"""Phase 1 MVP runner.

Usage:
  uv run --extra eval python -m evals.mcp_llm.run \
    --base-url https://api.deepseek.com/v1 \
    --api-key sk-xxx \
    --model deepseek-chat \
    --cases evals/mcp_llm/cases/seed_20.jsonl \
    --prompt evals/mcp_llm/prompts/p0_baseline.md
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import sys
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any

from openai import OpenAI

# Allow running both as `python -m evals.mcp_llm.run` AND
# `uv run python evals/mcp_llm/run.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.mcp_llm.backend_proc import (
    BackendHandle,
    reset_db,
    start_backend,
    stop_backend,
)
from evals.mcp_llm.llm_client import call_llm_stream
from evals.mcp_llm.mcp_client import (
    build_stdio_params,
    mcp_session,
    mcp_tools_to_openai,
    tool_result_to_json,
)
from evals.mcp_llm.scorer import score_case


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_cases(path: Path) -> list[dict]:
    cases = []
    if path.suffix in (".jsonl", ".ndjson"):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(json.loads(line))
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        cases = data if isinstance(data, list) else data.get("cases", [])
    return cases


def filter_cases(cases: list[dict], args) -> list[dict]:
    out = cases
    if args.filter_class:
        wanted = set(args.filter_class.split(","))
        out = [c for c in out if c.get("class") in wanted]
    if args.filter_id:
        wanted = set(args.filter_id.split(","))
        out = [c for c in out if c.get("id") in wanted]
    if args.limit:
        out = out[: args.limit]
    return out


async def run_one_case(
    case: dict,
    *,
    llm_client: OpenAI,
    model: str,
    temperature: float,
    timeout: float,
    tools_openai: list[dict],
    mcp_sess,
    system_prompt: str,
    extra_body: dict | None = None,
    seed: int | None = None,
) -> dict:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    tool_results_seen: list[Any] = []
    actual_calls: list[dict] = []
    ttfts: list[float] = []
    e2es: list[float] = []
    last_content = ""
    errors: list[str] = []

    for user_turn in case["user_turns"]:
        messages.append({"role": "user", "content": user_turn})
        # tool-calling inner loop with hard cap
        for _step in range(6):
            res = call_llm_stream(
                llm_client,
                model,
                messages,
                tools_openai,
                temperature=temperature,
                tool_choice="auto",
                timeout=timeout,
                extra_body=extra_body,
                seed=seed,
            )
            if res.ttft is not None:
                ttfts.append(res.ttft)
            e2es.append(res.e2e)
            if res.error:
                errors.append(res.error)
                break

            if res.tool_calls:
                # Append assistant message with tool_calls
                tc_msg = {
                    "role": "assistant",
                    "content": res.content or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                            },
                        }
                        for tc in res.tool_calls
                    ],
                }
                messages.append(tc_msg)
                # Execute each
                from evals.mcp_llm.mcp_client import TOOL_NAME_ALIAS_REVERSE, sanitize_tool_args
                for tc in res.tool_calls:
                    real_name = TOOL_NAME_ALIAS_REVERSE.get(tc["name"], tc["name"])
                    # filter unknown kwargs to prevent server-side pydantic ValidationError
                    schema_map = getattr(run_one_case, "_schema_map", {}) or {}
                    clean_args, dropped = sanitize_tool_args(real_name, tc["arguments"] or {}, schema_map)
                    if dropped:
                        errors.append(f"sanitize: dropped extra kwargs {dropped} from {real_name}")
                    actual_calls.append(
                        {
                            "name": real_name,  # scorer compares against expected real names
                            "arguments": tc["arguments"],  # keep original for scorer
                        }
                    )
                    try:
                        mcp_result = await asyncio.wait_for(
                            mcp_sess.call_tool(real_name, clean_args),
                            timeout=30,
                        )
                        result_obj = tool_result_to_json(mcp_result)
                    except Exception as e:
                        result_obj = {"success": False, "error": f"mcp_call_failed: {e}"}
                        errors.append(f"mcp_call_failed: {tc['name']}: {e}")
                    tool_results_seen.append(result_obj)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result_obj, ensure_ascii=False),
                        }
                    )
                # loop again so LLM can summarize
                continue
            # no tool calls → assistant final
            messages.append({"role": "assistant", "content": res.content})
            last_content = res.content or ""
            break
        else:
            errors.append("tool_loop_exhausted")

    return {
        "case_id": case["id"],
        "actual_calls": actual_calls,
        "tool_results": tool_results_seen,
        "final": last_content,
        "ttfts": ttfts,
        "e2es": e2es,
        "errors": errors,
    }


def is_write_case(case: dict) -> bool:
    return case.get("class") == "D_write_ops"


async def run_all(args, run_dir: Path) -> dict:
    cases_path = Path(args.cases)
    cases = filter_cases(load_cases(cases_path), args)
    print(f"[run] {len(cases)} cases after filter")

    prompts = []
    if args.prompt_dir:
        for p in sorted(Path(args.prompt_dir).glob("*.md")):
            prompts.append((p.stem, p.read_text(encoding="utf-8")))
    else:
        p = Path(args.prompt)
        prompts.append((p.stem, p.read_text(encoding="utf-8")))

    seed_path = Path(args.seed)
    backend: BackendHandle = start_backend(seed_path, profile_name="base", port=None)
    print(f"[backend] up on :{backend.port}, db={backend.db_path}")
    print(f"[backend] api_key={backend.api_key_plaintext}")

    results_all: list[dict] = []

    try:
        mcp_params = build_stdio_params(
            Path(args.mcp_script),
            backend.base_url,
            backend.api_key_plaintext,
        )

        async with mcp_session(mcp_params) as sess:
            tools_list = await sess.list_tools()
            tools_openai = mcp_tools_to_openai(tools_list)
            print(f"[mcp] {len(tools_openai)} tools: {[t['function']['name'] for t in tools_openai]}")
            # Stash schema map on run_one_case for arg sanitization
            from evals.mcp_llm.mcp_client import build_tool_schema_map
            run_one_case._schema_map = build_tool_schema_map(tools_openai)
            print(f"[mcp] schema params: {{k: len(v) for k, v in run_one_case._schema_map.items()}}".replace("{k: len(v) for k, v in run_one_case._schema_map.items()}", str({k: len(v) for k, v in run_one_case._schema_map.items()})))

            if args.dry_run:
                return {"dry_run": True, "tools": [t['function']['name'] for t in tools_openai], "cases": len(cases)}

            llm_client = OpenAI(base_url=args.base_url, api_key=args.api_key)

            extra_body = None
            if args.extra_body_json:
                extra_body = json.loads(args.extra_body_json)
                print(f"[extra_body] {extra_body}")

            k = max(1, int(args.k))
            print(f"[passk] k={k} seed_base={args.seed_base}")

            for prompt_name, prompt_text in prompts:
                read_cases = [c for c in cases if not is_write_case(c)]
                write_cases = [c for c in cases if is_write_case(c)]

                sem = asyncio.Semaphore(args.concurrency)

                async def run_one_rep(c, rep):
                    seed = args.seed_base + rep * 1000
                    try:
                        r = await run_one_case(
                            c,
                            llm_client=llm_client,
                            model=args.model,
                            temperature=args.temperature,
                            timeout=args.timeout,
                            tools_openai=tools_openai,
                            mcp_sess=sess,
                            system_prompt=prompt_text,
                            extra_body=extra_body,
                            seed=seed,
                        )
                    except Exception as e:
                        r = {
                            "case_id": c["id"],
                            "actual_calls": [],
                            "tool_results": [],
                            "final": "",
                            "ttfts": [],
                            "e2es": [],
                            "errors": [f"infra: {e}", traceback.format_exc()],
                        }
                    r["prompt"] = prompt_name
                    r["case"] = c
                    r["rep"] = rep
                    r["seed"] = seed
                    return r

                async def run_read(c, rep):
                    async with sem:
                        return await run_one_rep(c, rep)

                print(f"[prompt={prompt_name}] read={len(read_cases)} × k={k} concurrent")
                read_results: list[dict] = []
                for rep in range(k):
                    rep_results = await asyncio.gather(*(run_read(c, rep) for c in read_cases))
                    read_results.extend(rep_results)
                    print(f"  rep {rep+1}/{k} read done")

                print(f"[prompt={prompt_name}] write={len(write_cases)} × k={k} serial (db reset each)")
                write_results: list[dict] = []
                for rep in range(k):
                    for c in write_cases:
                        reset_db(backend)
                        r = await run_one_rep(c, rep)
                        write_results.append(r)
                    print(f"  rep {rep+1}/{k} write done")

                results_all.extend(read_results + write_results)
    finally:
        try:
            stop_backend(backend, keep_db=args.keep_db_on_fail)
        except Exception as e:
            print(f"[teardown] {e}")

    # Score
    scored = []
    for r in results_all:
        sc = score_case(r["case"], r["actual_calls"], r["final"], r["tool_results"])
        scored.append(
            {
                "id": r["case_id"],
                "prompt": r["prompt"],
                "class": r["case"].get("class"),
                "rep": r.get("rep", 0),
                "seed": r.get("seed"),
                "tsa": sc.tsa,
                "afa": sc.afa,
                "nf": sc.nf,
                "irr": sc.irr,
                "passed": sc.passed,
                "notes": sc.notes,
                "errors": r["errors"],
                "ttft_p50": _p50(r["ttfts"]),
                "e2e_total": sum(r["e2es"]),
                "actual_calls": [{"name": c["name"], "arguments": c["arguments"]} for c in r["actual_calls"]],
                "final": r["final"],
                "tool_results": r["tool_results"],
            }
        )

    # pass^k aggregation: group by (id, prompt); case passes iff every rep passes
    by_case: dict = {}
    for s in scored:
        key = (s["id"], s["prompt"])
        by_case.setdefault(key, []).append(s)
    aggregated = []
    for (cid, pname), reps in by_case.items():
        cls = reps[0]["class"]
        all_pass = all(r["passed"] for r in reps)
        any_pass = any(r["passed"] for r in reps)
        aggregated.append({
            "id": cid,
            "prompt": pname,
            "class": cls,
            "reps": len(reps),
            "tsa_mean": sum(r["tsa"] for r in reps) / len(reps),
            "afa_mean": sum(r["afa"] for r in reps) / len(reps),
            "nf_mean": sum(r["nf"] for r in reps) / len(reps),
            "irr_any": any(r.get("irr") for r in reps if r.get("irr") is not None),
            "pass_k": all_pass,      # pass^k
            "pass_1": any_pass,      # pass^1
            "ttft_p50": _p50([r["ttft_p50"] for r in reps if r["ttft_p50"]]),
            "e2e_mean": sum(r["e2e_total"] for r in reps) / len(reps),
        })

    return {
        "run_dir": str(run_dir),
        "results": scored,        # raw per-rep
        "aggregated": aggregated, # per-case pass^k
        "cases_total": len(cases),
        "k": int(args.k) if args.k else 1,
    }


def _p50(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[len(s) // 2]


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = max(0, int(len(s) * 0.95) - 1)
    return s[idx]


def write_report(result: dict, run_dir: Path, args) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)

    # raw json
    (run_dir / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    # config
    (run_dir / "run_config.json").write_text(
        json.dumps(
            {
                "base_url": args.base_url,
                "model": args.model,
                "cases": args.cases,
                "prompt": args.prompt,
                "prompt_dir": args.prompt_dir,
                "concurrency": args.concurrency,
                "temperature": args.temperature,
                "timestamp": _dt.datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # markdown summary
    md = [f"# Eval Run — {args.model}", ""]
    md.append(f"- timestamp: {_dt.datetime.now().isoformat()}")
    md.append(f"- base_url: `{args.base_url}`")
    md.append(f"- cases: `{args.cases}` ({result['cases_total']} after filter)")
    md.append(f"- prompts: " + ", ".join(sorted({r['prompt'] for r in result['results']})))
    md.append("")

    # per (class, prompt) aggregate
    agg: dict[tuple, list] = defaultdict(list)
    for r in result["results"]:
        agg[(r["class"], r["prompt"])].append(r)

    k = result.get("k", 1)
    md.append(f"- k (pass^k reps) = {k}")
    md.append("")
    md.append("## 按 Class × Prompt 汇总（pass^k 视角，每 case 全 k 次都过才算 pass）")
    md.append("")
    md.append(f"| Class | Prompt | N cases | TSA mean | AFA mean | NF mean | IRR any | pass^{k} | pass^1 | TTFT p50 | E2E mean |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    agg_k: dict[tuple, list] = defaultdict(list)
    for r in result.get("aggregated", []):
        agg_k[(r["class"], r["prompt"])].append(r)
    for (cls, pr), rows in sorted(agg_k.items()):
        n = len(rows)
        tsa = sum(r["tsa_mean"] for r in rows) / n
        afa = sum(r["afa_mean"] for r in rows) / n
        nf = sum(r["nf_mean"] for r in rows) / n
        irr = sum(1 for r in rows if r.get("irr_any")) / n
        pk = sum(1 for r in rows if r["pass_k"]) / n
        p1 = sum(1 for r in rows if r["pass_1"]) / n
        ttft = sum(r["ttft_p50"] for r in rows) / n
        e2e = sum(r["e2e_mean"] for r in rows) / n
        md.append(
            f"| {cls} | {pr} | {n} | {tsa:.2%} | {afa:.2%} | {nf:.2%} | "
            f"{irr:.2%} | {pk:.2%} | {p1:.2%} | {ttft:.2f}s | {e2e:.2f}s |"
        )

    md.append("")
    md.append("## 全局汇总")
    all_aggs = result.get("aggregated", [])
    if all_aggs:
        n = len(all_aggs)
        md.append(f"- N cases = {n} × k = {k} reps each")
        md.append(f"- TSA mean = {sum(r['tsa_mean'] for r in all_aggs)/n:.2%}")
        md.append(f"- AFA mean = {sum(r['afa_mean'] for r in all_aggs)/n:.2%}")
        md.append(f"- NF  mean = {sum(r['nf_mean']  for r in all_aggs)/n:.2%}")
        md.append(f"- pass^{k} = {sum(1 for r in all_aggs if r['pass_k'])/n:.2%}")
        md.append(f"- pass^1  = {sum(1 for r in all_aggs if r['pass_1'])/n:.2%}")

    all_rows = result["results"]

    md.append("")
    md.append("## 每用例明细")
    md.append("")
    md.append("| ID | Class | Prompt | TSA | AFA | NF | Pass | Notes |")
    md.append("|---|---|---|---|---|---|---|---|")
    for r in sorted(all_rows, key=lambda x: (x["prompt"], x["id"])):
        notes_short = "; ".join(r["notes"])[:120].replace("|", "\\|")
        md.append(
            f"| {r['id']} | {r['class']} | {r['prompt']} | {r['tsa']:.0%} | {r['afa']:.0%} | "
            f"{r['nf']:.0%} | {'PASS' if r['passed'] else 'FAIL'} | {notes_short} |"
        )

    # failures detail
    md.append("")
    md.append("## 失败用例 trace")
    for r in all_rows:
        if r["passed"]:
            continue
        md.append("")
        md.append(f"### {r['id']} ({r['class']}, prompt={r['prompt']})")
        md.append(f"- TSA={r['tsa']:.0%} AFA={r['afa']:.0%} NF={r['nf']:.0%}")
        md.append(f"- notes: {r['notes']}")
        md.append(f"- errors: {r['errors']}")
        md.append(f"- actual_calls: `{r['actual_calls']}`")
        final_excerpt = (r["final"] or "")[:300]
        md.append(f"- final: `{final_excerpt}`")

    (run_dir / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print(f"[report] wrote {run_dir/'summary.md'}")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--cases", required=True)
    p.add_argument("--seed", default="evals/mcp_llm/cases/seed.yaml")
    p.add_argument("--prompt", default=None)
    p.add_argument("--prompt-dir", default=None)
    p.add_argument("--k", type=int, default=1, help="pass^k: each case repeated k times; case passes iff all k reps pass")
    p.add_argument("--seed-base", type=int, default=20260521, help="seed = seed_base + rep_idx * 1000")
    p.add_argument("--extra-body-json", default=None, help="JSON dict forwarded as OpenAI extra_body, e.g. '{\"enable_thinking\":false}'")
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--write-concurrency", type=int, default=1)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--tool-choice", default="auto")
    p.add_argument("--backend-port-base", type=int, default=12450)
    p.add_argument("--mcp-script", default="mcp/warehouse_mcp.py")
    p.add_argument("--output", default=None)
    p.add_argument("--stream", default="true")
    p.add_argument("--keep-db-on-fail", action="store_true")
    p.add_argument("--filter-class", default=None)
    p.add_argument("--filter-id", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if not a.prompt and not a.prompt_dir:
        p.error("--prompt or --prompt-dir required")
    return a


def main():
    args = parse_args()
    run_id = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else PROJECT_ROOT / "evals" / "mcp_llm" / "reports" / run_id
    out.mkdir(parents=True, exist_ok=True)
    print(f"[run_id] {run_id} → {out}")

    result = asyncio.run(run_all(args, out))
    if args.dry_run:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    write_report(result, out, args)


if __name__ == "__main__":
    main()
