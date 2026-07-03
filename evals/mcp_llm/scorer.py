"""TSA / AFA / NF scorers (Phase 1). CR@k / IRR stubbed.

Inputs:
- expected: case["expected"] dict
- actual_tool_calls: list[{name, arguments(dict), result(dict|str)}]
- final_text: str (assistant last message content)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaseScore:
    tsa: float = 0.0           # 0..1
    afa: float = 0.0           # 0..1
    nf: float = 0.0            # 0..1 (1 = no violations)
    cr_at_k: float | None = None
    irr: float | None = None
    passed: bool = False
    notes: list[str] = field(default_factory=list)


# ---------- TSA ----------

def score_tsa(expected_calls: list[dict], actual_calls: list[dict]) -> tuple[float, list[str]]:
    """Order-sensitive name sequence match. Allow routing_retry to insert 1 extra."""
    notes: list[str] = []
    exp_names = [c["name"] for c in expected_calls]
    act_names = [c["name"] for c in actual_calls]

    if not exp_names and not act_names:
        return 1.0, notes
    if not exp_names and act_names:
        notes.append(f"tsa: expected no tool, got {act_names}")
        return 0.0, notes

    # Exact ordered prefix match?
    if act_names[: len(exp_names)] == exp_names:
        extra = len(act_names) - len(exp_names)
        if extra == 0:
            return 1.0, notes
        # allow_routing_retry: if any expected call has it, tolerate +1
        allow_extra = any(c.get("allow_routing_retry") for c in expected_calls)
        if allow_extra and extra == 1:
            notes.append("tsa: extra routing_retry tolerated")
            return 1.0, notes
        # Otherwise -10% per extra
        score = max(0.0, 1.0 - 0.10 * extra)
        notes.append(f"tsa: extra tools penalty x{extra}")
        return score, notes

    # Match in any order? partial credit
    matched = sum(1 for n in exp_names if n in act_names)
    score = matched / max(len(exp_names), 1) * 0.5  # disorder cap 50%
    notes.append(f"tsa: order mismatch exp={exp_names} act={act_names}")
    return score, notes


# ---------- AFA ----------

def _subset_match(expected: Any, actual: Any) -> tuple[bool, str | None]:
    """Recursive subset: every key in expected exists in actual with matching value."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False, "type:dict_expected"
        for k, v in expected.items():
            if k not in actual:
                return False, f"missing:/{k}"
            ok, why = _subset_match(v, actual[k])
            if not ok:
                return False, f"/{k}{why or ''}"
        return True, None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False, "type:list_expected"
        if len(expected) > len(actual):
            return False, "list:too_short"
        for i, ev in enumerate(expected):
            ok, why = _subset_match(ev, actual[i])
            if not ok:
                return False, f"/{i}{why or ''}"
        return True, None
    # leaf compare with numeric coercion
    if isinstance(expected, (int, float)) and isinstance(actual, str):
        try:
            actual_v = float(actual)
            return float(expected) == actual_v, None if float(expected) == actual_v else f"leaf:{expected}!={actual}"
        except ValueError:
            return False, f"leaf:{expected}!={actual!r}"
    if expected == actual:
        return True, None
    return False, f"leaf:{expected!r}!={actual!r}"


def score_afa(expected_calls: list[dict], actual_calls: list[dict]) -> tuple[float, list[str]]:
    notes: list[str] = []
    if not expected_calls:
        return 1.0, notes
    ok_count = 0
    for i, exp in enumerate(expected_calls):
        # find first actual matching by name
        match = None
        for ac in actual_calls:
            if ac["name"] == exp["name"]:
                match = ac
                break
        if match is None:
            notes.append(f"afa[{i}]: no actual call named {exp['name']}")
            continue
        mode = exp.get("args_match", "subset")
        exp_args = exp.get("args", {})
        act_args = match.get("arguments", {})
        if mode == "exact":
            if exp_args == act_args:
                ok_count += 1
            else:
                notes.append(f"afa[{i}]: exact mismatch {exp_args}!={act_args}")
        elif mode == "regex":
            # value-level regex per leaf
            ok = True
            for k, pat in exp_args.items():
                if k not in act_args or not re.search(str(pat), str(act_args[k]), re.IGNORECASE):
                    ok = False
                    notes.append(f"afa[{i}]: regex /{pat}/ no match in {k}={act_args.get(k)}")
                    break
            if ok:
                ok_count += 1
        else:  # subset
            ok, why = _subset_match(exp_args, act_args)
            if ok:
                ok_count += 1
            else:
                notes.append(f"afa[{i}]: subset fail {why}")
    return ok_count / len(expected_calls), notes


# ---------- NF ----------

_NUM_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])")
_DIGIT_RE = re.compile(r"\d+(?:\.\d+)?")


def collect_numbers(obj: Any, out: set[str]) -> None:
    if isinstance(obj, bool):  # before int!
        return
    if isinstance(obj, (int, float)):
        s = str(obj)
        # strip trailing .0 for int-like floats
        if isinstance(obj, float) and obj.is_integer():
            s = str(int(obj))
        out.add(s)
        return
    if isinstance(obj, str):
        for m in _DIGIT_RE.findall(obj):
            out.add(m)
            if m.endswith(".0"):
                out.add(m[:-2])
        return
    if isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, out)
        return
    if isinstance(obj, list):
        for v in obj:
            collect_numbers(v, out)


# 常见无害数字（年份/日期 token 等可能出现在 speak 中，但 tool result 本身也会带，
# 所以一般都进白名单）。这里只允许"序数语境"常数。
_BENIGN_NUMS = {"1", "2", "3"}  # 谨慎：避免 LLM 编造小数字逃过检测


def score_nf(
    tool_results: list[Any],
    final_text: str,
    expected_final: dict,
    user_turns: list[str] | None = None,
) -> tuple[float, list[str]]:
    notes: list[str] = []
    if not final_text:
        # 没有 final 也不算 NF 违规（TSA 会扣）
        return 1.0, ["nf: empty final_text"]

    # Strip <think>...</think> blocks before NF checks (Qwen3 on edgellm
    # may output thinking even with enable_thinking=false)
    clean_final = re.sub(r'<think>.*?</think>', '', final_text, flags=re.DOTALL).strip()
    if not clean_final:
        return 1.0, ["nf: empty final_text (think only)"]
    final_text = clean_final

    allowed: set[str] = set()
    for tr in tool_results:
        collect_numbers(tr, allowed)

    # expected numeric_values are also allowed
    for v in expected_final.get("numeric_values", []) or []:
        allowed.add(str(v))

    # 用户消息中已出现的数字也是允许的（模型转述用户问题不算幻觉）
    for ut in (user_turns or []):
        for m in _NUM_RE.findall(ut):
            allowed.add(m)

    allowed |= _BENIGN_NUMS

    # rule 1: hallucinated numbers
    text_nums = _NUM_RE.findall(final_text)
    halluc = [n for n in text_nums if n not in allowed]
    # also allow normalized variants (".0" stripped)
    halluc = [n for n in halluc if (n.rstrip(".0") not in allowed)]
    if halluc:
        notes.append(f"nf: hallucinated numbers {halluc[:5]} not in allowed={sorted(allowed)[:20]}")

    # rule 2: required numeric_values must appear
    missing = []
    for v in expected_final.get("numeric_values", []) or []:
        if str(v) not in final_text:
            missing.append(str(v))
    if missing:
        notes.append(f"nf: missing required numbers {missing}")

    # rule 3: must_not_match regex blacklist
    blacklist_hit = []
    for pat in expected_final.get("must_not_match", []) or []:
        if re.search(pat, final_text):
            blacklist_hit.append(pat)
    if blacklist_hit:
        notes.append(f"nf: regex blacklist hit {blacklist_hit}")

    # rule 4: must_contain / must_not_contain
    for tok in expected_final.get("must_contain", []) or []:
        if tok not in final_text:
            notes.append(f"nf: must_contain missing {tok!r}")
    for tok in expected_final.get("must_not_contain", []) or []:
        if tok in final_text:
            notes.append(f"nf: must_not_contain hit {tok!r}")

    violations = sum(1 for n in notes if n.startswith("nf:") and not n.startswith("nf: empty"))
    if violations == 0:
        return 1.0, notes
    # Each violation drops 0.5
    return max(0.0, 1.0 - 0.5 * violations), notes


# ---------- CR@k / IRR (Phase 1 stubs) ----------

def score_cr_at_k(*args, **kwargs):
    return None, ["cr_at_k: phase2"]


def score_irr(case_class: str, actual_calls: list[dict], final_text: str) -> tuple[float | None, list[str]]:
    if case_class != "E_irrelevance":
        return None, []
    if actual_calls:
        return 0.0, [f"irr: tool called when none expected ({[c['name'] for c in actual_calls]})"]
    reject_pat = re.compile(r"无法|不能|抱歉|不在我职责|只负责|无关")
    if reject_pat.search(final_text):
        return 1.0, []
    return 0.0, ["irr: no reject phrase in final"]


# ---------- top-level ----------

def score_case(
    case: dict,
    actual_calls: list[dict],
    final_text: str,
    tool_results_for_nf: list[Any],
) -> CaseScore:
    exp = case.get("expected", {})
    exp_calls = exp.get("tool_calls", []) or []
    exp_final = exp.get("final", {}) or {}

    sc = CaseScore()
    sc.tsa, n1 = score_tsa(exp_calls, actual_calls)
    sc.afa, n2 = score_afa(exp_calls, actual_calls)
    sc.nf, n3 = score_nf(tool_results_for_nf, final_text, exp_final, user_turns=case.get("user_turns") or [])
    sc.irr, n4 = score_irr(case.get("class", ""), actual_calls, final_text)
    sc.notes = n1 + n2 + n3 + n4
    # pass criteria: tsa==1 & afa>=0.8 & nf==1, irr passes if applicable
    pass_irr = sc.irr is None or sc.irr >= 0.999
    sc.passed = (sc.tsa >= 0.999 and sc.afa >= 0.8 and sc.nf >= 0.999 and pass_irr)
    return sc
