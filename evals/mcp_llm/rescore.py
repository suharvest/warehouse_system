"""Re-score existing eval reports without re-running the API.

Reads each report dir's metrics.json (which contains raw `results` + `case` data),
re-runs scorer.score_case on each rep, regenerates aggregated and summary.md.

Usage:
  uv run --extra eval python -m evals.mcp_llm.rescore evals/mcp_llm/reports/phase2_*
"""
from __future__ import annotations
import json
import sys
from collections import defaultdict
from pathlib import Path

from evals.mcp_llm.scorer import score_case


def _p50(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[len(s) // 2]


def rescore_report(report_dir: Path) -> dict:
    metrics_path = report_dir / "metrics.json"
    if not metrics_path.exists():
        return {"error": f"no metrics.json in {report_dir}"}
    data = json.loads(metrics_path.read_text())

    # Each result needs case info. Older reports may not have it embedded.
    # We require: id, prompt, class, rep, seed, actual_calls, final, tool_results.
    # Re-load case definitions from disk if needed.
    rc = data.get("run_config") or {}
    cases_path = Path(rc.get("cases") or "evals/mcp_llm/cases/seed_200.jsonl")
    cases_by_id: dict[str, dict] = {}
    if cases_path.exists():
        for line in cases_path.read_text().splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            cases_by_id[c["id"]] = c

    rescored = []
    for r in data["results"]:
        case = cases_by_id.get(r["id"])
        if not case:
            rescored.append(r)
            continue
        sc = score_case(case, r["actual_calls"], r["final"], r["tool_results"])
        rescored.append({
            **r,
            "tsa": sc.tsa, "afa": sc.afa, "nf": sc.nf, "irr": sc.irr,
            "passed": sc.passed, "notes": sc.notes,
        })

    # Re-aggregate
    by_case = defaultdict(list)
    for s in rescored:
        key = (s["id"], s["prompt"])
        by_case[key].append(s)
    aggregated = []
    for (cid, pname), reps in by_case.items():
        cls = reps[0]["class"]
        all_pass = all(r["passed"] for r in reps)
        any_pass = any(r["passed"] for r in reps)
        aggregated.append({
            "id": cid, "prompt": pname, "class": cls, "reps": len(reps),
            "tsa_mean": sum(r["tsa"] for r in reps) / len(reps),
            "afa_mean": sum(r["afa"] for r in reps) / len(reps),
            "nf_mean": sum(r["nf"] for r in reps) / len(reps),
            "irr_any": any(r.get("irr") for r in reps if r.get("irr") is not None),
            "pass_k": all_pass, "pass_1": any_pass,
            "ttft_p50": _p50([r["ttft_p50"] for r in reps if r.get("ttft_p50")]),
            "e2e_mean": sum(r["e2e_total"] for r in reps) / len(reps),
        })

    data["results"] = rescored
    data["aggregated"] = aggregated
    metrics_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))

    # Regenerate summary.md (top-level only — class × prompt + global)
    n_all = len(aggregated)
    k = data.get("k", 1)
    md = [
        f"# Eval Run (rescored) — {report_dir.name}",
        "",
        f"- k = {k}",
        f"- cases = {n_all}",
        "",
        "## 按 Class × Prompt（pass^k 视角）",
        "",
        f"| Class | Prompt | N | TSA | AFA | NF | IRR any | pass^{k} | pass^1 | TTFT p50 | E2E mean |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    by_cls = defaultdict(list)
    for r in aggregated:
        by_cls[(r["class"], r["prompt"])].append(r)
    for (cls, pr), rows in sorted(by_cls.items()):
        n = len(rows)
        tsa = sum(r["tsa_mean"] for r in rows) / n
        afa = sum(r["afa_mean"] for r in rows) / n
        nf = sum(r["nf_mean"] for r in rows) / n
        irr = sum(1 for r in rows if r.get("irr_any")) / n
        pk = sum(1 for r in rows if r["pass_k"]) / n
        p1 = sum(1 for r in rows if r["pass_1"]) / n
        ttft = sum(r["ttft_p50"] for r in rows) / n
        e2e = sum(r["e2e_mean"] for r in rows) / n
        md.append(f"| {cls} | {pr} | {n} | {tsa:.2%} | {afa:.2%} | {nf:.2%} | {irr:.2%} | {pk:.2%} | {p1:.2%} | {ttft:.2f}s | {e2e:.2f}s |")

    md += ["", "## 全局"]
    md.append(f"- TSA mean = {sum(r['tsa_mean'] for r in aggregated)/n_all:.2%}")
    md.append(f"- AFA mean = {sum(r['afa_mean'] for r in aggregated)/n_all:.2%}")
    md.append(f"- NF  mean = {sum(r['nf_mean']  for r in aggregated)/n_all:.2%}")
    md.append(f"- pass^{k} = {sum(1 for r in aggregated if r['pass_k'])/n_all:.2%}")
    md.append(f"- pass^1  = {sum(1 for r in aggregated if r['pass_1'])/n_all:.2%}")

    (report_dir / "summary_rescored.md").write_text("\n".join(md))

    return {
        "report": report_dir.name,
        "pass_k": sum(1 for r in aggregated if r["pass_k"]) / n_all,
        "pass_1": sum(1 for r in aggregated if r["pass_1"]) / n_all,
        "nf_mean": sum(r["nf_mean"] for r in aggregated) / n_all,
        "n": n_all,
    }


def main():
    if len(sys.argv) < 2:
        print("usage: rescore.py <report_dir> [<report_dir>...]")
        sys.exit(1)
    for p in sys.argv[1:]:
        rd = Path(p)
        if not rd.is_dir():
            print(f"skip {p}")
            continue
        r = rescore_report(rd)
        print(f"{rd.name:50s} pass^k={r.get('pass_k',0):.2%} pass^1={r.get('pass_1',0):.2%} nf={r.get('nf_mean',0):.2%}")


if __name__ == "__main__":
    main()
