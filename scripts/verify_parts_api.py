#!/usr/bin/env python3
"""备品管理系统接口契约验证脚本（对接 warehouse MCP 前置检查）。

只依赖 Python 3.7+ 标准库，不需要 pip 装任何东西，可以直接拷到对方机器上跑。

用法：
    # 只读检查（安全，不改任何数据）
    python3 verify_parts_api.py http://10.109.20.102:8888 \\
        --name 撬具 --no P001 --type LH-815

    # 附加写操作检查（会真的入库 1 件再出库 1 件，请用测试料号）
    python3 verify_parts_api.py http://10.109.20.102:8888 \\
        --name 撬具 --no P001 --type LH-815 --write --operator 联调测试

退出码：0 = 全部通过；1 = 有 FAIL；2 = 参数或连接问题。
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

# ── 输出 ──

_RESULTS = []


def _emit(status, title, detail=""):
    _RESULTS.append((status, title))
    mark = {"PASS": "[ PASS ]", "FAIL": "[ FAIL ]", "WARN": "[ WARN ]", "INFO": "[ INFO ]"}[status]
    print(f"{mark} {title}")
    for line in str(detail).splitlines():
        if line.strip():
            print(f"         {line}")


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False)


# ── HTTP ──

def call(base_url, method, params, timeout=15):
    """调用备品接口，返回 (raw_response_dict, error_str)。"""
    url = f"{base_url.rstrip('/')}/api/{method}"
    body = json.dumps({"method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, f"响应不是合法 JSON，原文前 300 字：{text[:300]}"


def records_of(resp):
    """把响应的 data 归一化成记录列表。"""
    data = resp.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and data:
        return [data]
    return []


_STOCK_FIELDS = ("partNo", "partName", "partType", "stockQty")


def missing_fields(rec):
    return [f for f in _STOCK_FIELDS if f not in rec]


# ── 检查项 ──

def check_connectivity(base):
    resp, err = call(base, "parts_query_stock", {})
    if err:
        _emit("FAIL", "C0 连通性：接口不可达", err)
        return None
    _emit("PASS", "C0 连通性：接口可达并返回 JSON")
    return resp


def check_code_type(resp):
    code = resp.get("code")
    if code == 0:
        _emit("PASS", "C1 code 字段是整数 0", f"code={code!r}")
    elif code == "0":
        _emit("WARN", "C1 code 字段是字符串 \"0\"",
              "provider 里 `data.get('code') != 0` 会把成功判成失败，接入前必须改成兼容两种类型")
    else:
        _emit("WARN", "C1 code 非 0", f"code={code!r} msg={resp.get('msg')!r}")


def check_empty_query(resp):
    """空参数查询能否拿到全量列表——决定模糊匹配方案。"""
    recs = records_of(resp)
    data = resp.get("data")

    if isinstance(data, list) and len(data) > 1:
        bad = [r for r in recs if missing_fields(r)]
        if bad:
            _emit("WARN", f"C2 空参数返回列表（{len(recs)} 条），但部分记录字段不全",
                  f"缺字段的记录示例：{_dump(bad[0])}")
        else:
            _emit("PASS", f"C2 空参数返回全量列表（{len(recs)} 条），字段完整",
                  "→ 可直接用全量模糊匹配方案")
        return "full_list", recs

    if len(recs) == 1 and missing_fields(recs[0]):
        _emit("FAIL", "C2 空参数只返回一条不完整的占位记录",
              f"实际返回：{_dump(data)}\n"
              f"缺少字段：{', '.join(missing_fields(recs[0]))}\n"
              "→ 无法用它做候选匹配。provider 的 _fetch_products 必须能识别并丢弃这种响应，"
              "否则会把它当成唯一候选，导致所有查询返回「未找到备品」")
        return "placeholder", recs

    _emit("WARN", "C2 空参数未返回可用的全量列表", f"实际返回：{_dump(data)}")
    return "unusable", recs


def check_lookup(base, label, key, value):
    """按单个字段查询，返回 (记录列表 或 None)。"""
    if not value:
        _emit("INFO", f"C3 跳过按 {key} 查询", f"未提供 --{label} 参数")
        return None

    resp, err = call(base, "parts_query_stock", {key: value})
    if err:
        _emit("FAIL", f"C3 按 {key}={value!r} 查询失败", err)
        return None
    if resp.get("code") not in (0, "0"):
        _emit("FAIL", f"C3 按 {key}={value!r} 查询返回失败",
              f"code={resp.get('code')!r} msg={resp.get('msg')!r}")
        return None

    recs = records_of(resp)
    if not recs:
        _emit("FAIL", f"C3 按 {key}={value!r} 查不到任何记录",
              f"原始响应：{_dump(resp)}")
        return None

    miss = missing_fields(recs[0])
    if miss:
        _emit("FAIL", f"C3 按 {key}={value!r} 能查到，但记录缺字段",
              f"缺少：{', '.join(miss)}\n首条：{_dump(recs[0])}\n"
              "→ 缺 stockQty 就播报不了库存数字，缺 partNo/partName 语音消歧无法回读")
        return recs

    multi = "（返回 %d 条）" % len(recs) if len(recs) > 1 else ""
    _emit("PASS", f"C3 按 {key}={value!r} 查询正常{multi}",
          f"首条：{_dump(recs[0])}")
    return recs


def check_partial_name(base, name):
    """名称模糊能力：只给前半截能不能查到。决定模糊匹配放在哪一侧。"""
    if not name or len(name) < 2:
        _emit("INFO", "C4 跳过名称模糊检查", "--name 未提供或长度不足 2")
        return

    prefix = name[: max(1, len(name) // 2)]
    resp, err = call(base, "parts_query_stock", {"partName": prefix})
    if err:
        _emit("WARN", f"C4 名称片段 {prefix!r} 查询出错", err)
        return

    recs = records_of(resp)
    ok = resp.get("code") in (0, "0") and recs and not missing_fields(recs[0])
    if ok:
        _emit("PASS", f"C4 名称支持模糊匹配（片段 {prefix!r} 查到 {len(recs)} 条）",
              "→ 匹配可以下放给备品系统，我方只做候选排序")
    else:
        _emit("WARN", f"C4 名称疑似只支持精确匹配（片段 {prefix!r} 查不到）",
              f"原始响应：{_dump(resp)}\n"
              "→ 语音场景下 ASR 文本很难精确命中，我方需要缓存一份备品目录做本地模糊匹配")


def check_type_uniqueness(base, part_type):
    """partType 是否唯一——决定出入库能不能用它定位。"""
    if not part_type:
        _emit("INFO", "C5 跳过 partType 唯一性检查", "未提供 --type 参数")
        return

    resp, err = call(base, "parts_query_stock", {"partType": part_type})
    if err:
        _emit("FAIL", f"C5 按 partType={part_type!r} 查询失败", err)
        return

    recs = records_of(resp)
    if not recs:
        _emit("FAIL", f"C5 按 partType={part_type!r} 查不到记录", f"原始响应：{_dump(resp)}")
        return

    nos = {r.get("partNo") for r in recs if r.get("partNo")}
    if len(recs) > 1 or len(nos) > 1:
        _emit("FAIL", f"C5 partType={part_type!r} 对应多个备品（{len(recs)} 条，料号 {sorted(nos)}）",
              "→ 出入库接口只按 partType 定位会扣错料，必须改用 partNo 定位。"
              "请与备品系统确认出入库能否接受 partNo 作为主定位键")
    else:
        _emit("PASS", f"C5 partType={part_type!r} 唯一对应一个备品",
              f"partNo={recs[0].get('partNo')!r}")


def check_write(base, name, no, part_type, operator):
    """写操作往返：入库 1 件 → 复查 → 出库 1 件 → 复查。"""
    if not (part_type and no):
        _emit("FAIL", "C6 写操作检查需要同时提供 --type 和 --no", "已跳过")
        return

    def stock_of():
        resp, err = call(base, "parts_query_stock", {"partNo": no})
        if err or resp.get("code") not in (0, "0"):
            return None
        recs = records_of(resp)
        if not recs:
            return None
        try:
            return int(recs[0].get("stockQty"))
        except (TypeError, ValueError):
            return None

    before = stock_of()
    if before is None:
        _emit("FAIL", "C6 写操作前置复查失败：拿不到 stockQty 数字", f"partNo={no!r}")
        return
    _emit("INFO", "C6 写操作起始库存", f"partNo={no!r} stockQty={before}")

    # ── 入库 1 件（带 remark，验证多传字段会不会被拒） ──
    resp, err = call(base, "parts_stock_in", {
        "partType": part_type, "number": 1, "stockInUser": operator,
        "partNo": no, "partName": name or "", "remark": "warehouse MCP 联调验证",
    })
    if err:
        _emit("FAIL", "C6.1 入库调用失败", err)
        return
    if resp.get("code") not in (0, "0"):
        _emit("FAIL", "C6.1 入库被拒绝",
              f"code={resp.get('code')!r} msg={resp.get('msg')!r}\n"
              "若原因是 remark 字段不被接受，provider 里 stock_in 传 remark 的逻辑要去掉")
        return

    in_data = resp.get("data") or {}
    if "OutQty" in in_data:
        _emit("PASS", "C6.1 入库成功，响应含 OutQty 字段", f"data={_dump(in_data)}")
    else:
        _emit("WARN", "C6.1 入库成功，但响应里没有 OutQty 字段",
              f"data={_dump(in_data)}\n"
              "→ provider 用 data.get('OutQty', quantity) 取实际数量，取不到会静默用请求值兜底，"
              "对方部分入库时播报的数字会是错的")

    after_in = stock_of()
    if after_in == before + 1:
        _emit("PASS", "C6.2 入库后库存 +1 已生效", f"{before} → {after_in}")
    else:
        _emit("FAIL", "C6.2 入库后库存未按预期变化",
              f"入库前 {before}，入库后 {after_in}，期望 {before + 1}")

    # ── 出库 1 件，把数据还原 ──
    resp, err = call(base, "parts_stock_out", {
        "partType": part_type, "number": 1, "recipient": operator,
        "partNo": no, "partName": name or "", "remark": "warehouse MCP 联调验证回滚",
    })
    if err:
        _emit("FAIL", "C6.3 出库调用失败（数据未还原，请手工核对）", err)
        return
    if resp.get("code") not in (0, "0"):
        _emit("FAIL", "C6.3 出库被拒绝（数据未还原，请手工核对）",
              f"code={resp.get('code')!r} msg={resp.get('msg')!r}")
        return

    after_out = stock_of()
    if after_out == before:
        _emit("PASS", "C6.4 出库后库存已还原到初始值", f"{after_in} → {after_out}")
    else:
        _emit("FAIL", "C6.4 出库后库存未回到初始值（请手工核对）",
              f"初始 {before}，当前 {after_out}")

    # ── 库存不足时的错误文案 ──
    resp, err = call(base, "parts_stock_out", {
        "partType": part_type, "number": 99999999, "recipient": operator,
        "partNo": no, "partName": name or "",
    })
    if err:
        _emit("WARN", "C6.5 超量出库探测出错（不影响接入）", err)
    elif resp.get("code") in (0, "0"):
        _emit("FAIL", "C6.5 超量出库（99999999 件）居然成功了",
              "备品系统没有做库存校验，我方必须在 provider 里自行拦截，"
              "并请立刻人工核对该料号库存")
    else:
        msg = resp.get("msg") or ""
        if "库存不足" in msg:
            _emit("PASS", "C6.5 超量出库被拒，msg 含「库存不足」",
                  f"msg={msg!r} → provider 的 insufficient_stock 判定可用")
        else:
            _emit("WARN", "C6.5 超量出库被拒，但 msg 不含「库存不足」",
                  f"msg={msg!r}\n"
                  "→ provider 靠这四个字判定 insufficient_stock，需按实际文案调整")


# ── 主流程 ──

def main():
    ap = argparse.ArgumentParser(
        description="备品管理系统接口契约验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("base_url", help="备品系统地址，例如 http://10.109.20.102:8888")
    ap.add_argument("--name", help="一个真实存在的备品名称")
    ap.add_argument("--no", dest="part_no", help="对应的备品编号 partNo")
    ap.add_argument("--type", dest="part_type", help="对应的规格 partType")
    ap.add_argument("--write", action="store_true",
                    help="附加写操作检查：入库 1 件再出库 1 件还原（请用测试料号）")
    ap.add_argument("--operator", default="联调测试", help="写操作的经手人，默认「联调测试」")
    args = ap.parse_args()

    print(f"目标：{args.base_url}")
    print(f"模式：{'只读 + 写操作' if args.write else '只读'}")
    print("-" * 64)

    resp = check_connectivity(args.base_url)
    if resp is None:
        print("-" * 64)
        print("接口不可达，后续检查全部跳过。请确认地址、端口和网络可达性。")
        return 2

    check_code_type(resp)
    check_empty_query(resp)
    print()

    check_lookup(args.base_url, "name", "partName", args.name)
    check_lookup(args.base_url, "no", "partNo", args.part_no)
    check_partial_name(args.base_url, args.name)
    check_type_uniqueness(args.base_url, args.part_type)

    if args.write:
        print()
        check_write(args.base_url, args.name, args.part_no, args.part_type, args.operator)

    # ── 汇总 ──
    print()
    print("-" * 64)
    n_fail = sum(1 for s, _ in _RESULTS if s == "FAIL")
    n_warn = sum(1 for s, _ in _RESULTS if s == "WARN")
    n_pass = sum(1 for s, _ in _RESULTS if s == "PASS")
    print(f"通过 {n_pass}　警告 {n_warn}　失败 {n_fail}")

    if n_fail:
        print("\n未通过的项：")
        for s, t in _RESULTS:
            if s == "FAIL":
                print(f"  - {t}")
        print("\n这些项不解决，接入 warehouse MCP 后语音查询/出入库会失败。")
    elif n_warn:
        print("\n没有硬性失败，但有警告项需要在 provider 代码里做兼容处理。")
    else:
        print("\n接口契约检查全部通过。")

    if not args.write:
        print("\n注意：本次未做写操作检查。出入库接口的字段名、库存是否真的变化、"
              "库存不足的错误文案都还没有验证，请在测试料号上加 --write 再跑一次。")

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
