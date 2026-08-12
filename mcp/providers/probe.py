"""对端数据体检（Provider 接入前的语义校验）

与 ``test_runner`` 的分工：

- ``test_runner`` 查的是**结构**——Provider 有没有按 BaseProvider 的约定返回
  带指定 key 的 dict。它不看内容，所以一个"每次查询都返回 not_found"的
  Provider 照样能全绿：失败响应里同样有 ``success`` 这个 key。
- 本模块查的是**语义**——拿一个真实存在的样本物料走一遍只读链路，确认对端
  系统真的能查出数据来，且数据字段是可用的（名称非空、库存是数字）。

踩过的坑：某备品系统在不带过滤参数时返回一条只有 ``partType`` 的占位记录
（``{"code":0,"data":{"partType":"测试数据"}}``）。Provider 把它当成唯一候选，
于是所有查询都返回"未找到"，而 Level 1 依旧全绿。P1 的空壳检测就是为了在
接入前把这类响应挡下来。

本模块不认识任何具体 ERP 的接口，只通过 BaseProvider 的公开方法探测，
因此对所有第三方 Provider 通用。
"""

import logging
import time

from .test_runner import load_provider_from_file

logger = logging.getLogger("WarehouseMCP")

# 目录探测时最多检查多少条记录的字段完整性
_SAMPLE_LIMIT = 50


def _check(cid, title, status, detail="", raw=None, latency_ms=None):
    return {
        "id": cid,
        "title": title,
        "status": status,  # pass | fail | warn | skip
        "detail": detail,
        "raw": raw,
        "latency_ms": latency_ms,
    }


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _timed(fn):
    start = time.perf_counter()
    try:
        result = fn()
        return result, None, round((time.perf_counter() - start) * 1000, 2)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", round((time.perf_counter() - start) * 1000, 2)


def _probe_catalog(provider):
    """P1 目录探测：不带查询词拉一次列表，检查有没有空壳记录。"""
    resp, err, ms = _timed(
        lambda: provider.search(None, "material", None, None, None, True, False, 0)
    )
    if err:
        return _check("P1", "目录探测", "fail", f"调用 search 抛异常：{err}", latency_ms=ms)
    if not isinstance(resp, dict):
        return _check("P1", "目录探测", "fail",
                      f"search 返回了 {type(resp).__name__}，不是 dict", latency_ms=ms)
    if not resp.get("success"):
        return _check("P1", "目录探测", "warn",
                      "对端不支持不带查询词的列表拉取。模糊匹配将只能依赖单点查询，"
                      f"同名多规格消歧会受限。返回：{resp.get('message') or resp.get('error')}",
                      raw=resp, latency_ms=ms)

    items = resp.get("items") or []
    if not items:
        return _check("P1", "目录探测", "warn",
                      "对端返回空目录。若该系统确有物料数据，说明列表查询没打通，"
                      "语音模糊匹配会失效。", raw=resp, latency_ms=ms)

    hollow = [
        it for it in items[:_SAMPLE_LIMIT]
        if _blank(it.get("name")) and _blank(it.get("sku"))
    ]
    no_stock = [
        it for it in items[:_SAMPLE_LIMIT]
        if not _is_number(it.get("current_stock"))
    ]

    if hollow:
        return _check(
            "P1", "目录探测", "fail",
            f"返回的 {len(items)} 条记录里有 {len(hollow)} 条名称和编码都是空的。"
            "这类空壳记录会被当成候选参与匹配，导致真实物料查不到、"
            "语音播报出空名字。请确认对端列表接口返回的是真实物料记录。"
            f"\n空壳示例：{hollow[0]}",
            raw={"count": len(items), "hollow": hollow[:3]}, latency_ms=ms,
        )
    if no_stock:
        return _check(
            "P1", "目录探测", "warn",
            f"{len(no_stock)} 条记录的 current_stock 不是数字，这些物料无法播报库存。"
            f"\n示例：{no_stock[0]}",
            raw={"count": len(items), "no_stock": no_stock[:3]}, latency_ms=ms,
        )
    return _check("P1", "目录探测", "pass",
                  f"拉到 {len(items)} 条物料，名称/编码/库存字段完整。",
                  raw={"count": len(items), "first": items[0]}, latency_ms=ms)


def _probe_resolve(provider, sample):
    """P2 名称解析：样本能不能解析出候选。"""
    resp, err, ms = _timed(lambda: provider.resolve_name(sample, "material"))
    if err:
        return _check("P2", "名称解析", "fail", f"调用 resolve_name 抛异常：{err}", latency_ms=ms)
    if not isinstance(resp, dict):
        return _check("P2", "名称解析", "fail",
                      f"resolve_name 返回了 {type(resp).__name__}，不是 dict", latency_ms=ms)

    cands = resp.get("candidates") or []
    if not cands:
        return _check("P2", "名称解析", "fail",
                      f"样本 “{sample}” 解析不出任何候选。语音说出这个物料名时，"
                      "系统无法定位，出入库会直接失败。", raw=resp, latency_ms=ms)

    top = cands[0] or {}
    if _blank(top.get("name")):
        return _check("P2", "名称解析", "fail",
                      f"解析出 {len(cands)} 个候选，但首选候选没有名称，无法向用户播报。"
                      f"\n首选：{top}", raw=resp, latency_ms=ms)

    conf = "有把握" if resp.get("confident") else "不确定（会向用户追问）"
    return _check("P2", "名称解析", "pass",
                  f"解析出 {len(cands)} 个候选，首选“{top.get('name')}”，{conf}。",
                  raw={"confident": resp.get("confident"), "top": top}, latency_ms=ms)


def _probe_query_stock(provider, sample):
    """P3 库存查询：样本能不能查出库存数字。"""
    resp, err, ms = _timed(lambda: provider.query_stock(sample, False))
    if err:
        return _check("P3", "库存查询", "fail", f"调用 query_stock 抛异常：{err}", latency_ms=ms)
    if not isinstance(resp, dict):
        return _check("P3", "库存查询", "fail",
                      f"query_stock 返回了 {type(resp).__name__}，不是 dict", latency_ms=ms)
    if not resp.get("success"):
        return _check("P3", "库存查询", "fail",
                      f"样本 “{sample}” 查不到库存：{resp.get('message') or resp.get('error')}。"
                      "请确认样本在对端系统里真实存在；若存在，说明查询链路没打通。",
                      raw=resp, latency_ms=ms)

    p = resp.get("product") or {}
    problems = []
    if _blank(p.get("name")):
        problems.append("product.name 为空，播报时会念出空名字")
    if not _is_number(p.get("current_stock")):
        problems.append(f"product.current_stock 不是数字（{p.get('current_stock')!r}），无法播报库存")
    if problems:
        return _check("P3", "库存查询", "fail",
                      "查询返回成功，但字段不可用：" + "；".join(problems) + f"\nproduct={p}",
                      raw=resp, latency_ms=ms)

    return _check("P3", "库存查询", "pass",
                  f"“{p.get('name')}”当前库存 {p.get('current_stock')} "
                  f"{p.get('unit') or ''}，编码 {p.get('sku') or '（无）'}。",
                  raw=p, latency_ms=ms)


def _probe_search(provider, sample):
    """P4 搜索：样本能不能搜到，且结果字段可用。"""
    resp, err, ms = _timed(
        lambda: provider.search(sample, "material", None, None, None, True, False, 0)
    )
    if err:
        return _check("P4", "关键词搜索", "fail", f"调用 search 抛异常：{err}", latency_ms=ms)
    if not isinstance(resp, dict) or not resp.get("success"):
        return _check("P4", "关键词搜索", "fail",
                      f"搜索 “{sample}” 失败："
                      f"{(resp or {}).get('message') or (resp or {}).get('error')}",
                      raw=resp, latency_ms=ms)

    items = resp.get("items") or []
    if not items:
        return _check("P4", "关键词搜索", "fail",
                      f"搜索 “{sample}” 没有任何结果。用户问“有哪些X”时会得到空答复。",
                      raw=resp, latency_ms=ms)

    top = items[0] or {}
    if _blank(top.get("name")) and _blank(top.get("sku")):
        return _check("P4", "关键词搜索", "fail",
                      f"搜到 {len(items)} 条，但首条名称和编码都为空：{top}",
                      raw=resp, latency_ms=ms)

    return _check("P4", "关键词搜索", "pass",
                  f"搜到 {resp.get('total', len(items))} 条，首条“{top.get('name')}”。",
                  raw={"total": resp.get("total"), "first": top}, latency_ms=ms)


def _probe_consistency(stock_check, search_check, sample):
    """P5 一致性：同一样本，query_stock 与 search 报出来的库存要对得上。"""
    if stock_check["status"] != "pass" or search_check["status"] != "pass":
        return _check("P5", "数据一致性", "skip", "前置检查未通过，跳过。")

    a = (stock_check.get("raw") or {}).get("current_stock")
    first = (search_check.get("raw") or {}).get("first") or {}
    b = first.get("current_stock")

    name_a = (stock_check.get("raw") or {}).get("name")
    name_b = first.get("name")
    if name_a and name_b and name_a != name_b:
        return _check("P5", "数据一致性", "skip",
                      f"两个接口返回的是不同物料（{name_a} / {name_b}），无法比对库存。")

    if not _is_number(b):
        return _check("P5", "数据一致性", "warn",
                      f"搜索结果里的 current_stock 不是数字（{b!r}），无法与库存查询比对。")
    if a != b:
        return _check("P5", "数据一致性", "warn",
                      f"同一物料，库存查询报 {a}，搜索结果报 {b}。两个接口取数口径不一致，"
                      "用户换个问法会听到不同的数字。")
    return _check("P5", "数据一致性", "pass", f"两个接口都报 {a}，口径一致。")


def run_probe(filepath: str, config: dict, sample: str = "") -> dict:
    """对端数据体检。

    Args:
        filepath: Provider .py 文件绝对路径。
        config:   Provider 初始化配置（必须已含正确的 api_base_url）。
        sample:   对端系统里真实存在的物料名称或编码；留空则只做目录探测。

    Returns:
        {
            "sample": str,
            "checks": [{id, title, status, detail, raw, latency_ms}, ...],
            "all_passed": bool,   # 无 fail 即为 True（warn 不阻断）
            "has_warning": bool,
        }
    """
    sample = (sample or "").strip()

    try:
        provider = load_provider_from_file(filepath, config)
    except Exception as e:
        err = _check("P0", "加载 Provider", "fail", f"{type(e).__name__}: {e}")
        return {"sample": sample, "checks": [err], "all_passed": False, "has_warning": False}

    checks = [_probe_catalog(provider)]

    if not sample:
        skip_note = "未填写样本物料，跳过。填一个对端系统里真实存在的物料名称或编码可以查得更深。"
        checks += [
            _check("P2", "名称解析", "skip", skip_note),
            _check("P3", "库存查询", "skip", skip_note),
            _check("P4", "关键词搜索", "skip", skip_note),
            _check("P5", "数据一致性", "skip", skip_note),
        ]
    else:
        c2 = _probe_resolve(provider, sample)
        c3 = _probe_query_stock(provider, sample)
        c4 = _probe_search(provider, sample)
        checks += [c2, c3, c4, _probe_consistency(c3, c4, sample)]

    all_passed = all(c["status"] != "fail" for c in checks)
    has_warning = any(c["status"] == "warn" for c in checks)
    logger.info(
        f"Provider 体检完成：sample={sample!r} all_passed={all_passed} "
        f"fail={sum(1 for c in checks if c['status'] == 'fail')}"
    )
    return {
        "sample": sample,
        "checks": checks,
        "all_passed": all_passed,
        "has_warning": has_warning,
    }
