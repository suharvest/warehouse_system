"""模拟客户自有 WMS —— 组织 / 仓库 / 用户 / 库存四者真正关联。

接口形状照抄现场探到的真实契约（http://192.168.101.100:3000）：
  GET  /api/products      -> {code, data:[...], msg}      支持 ?warehouse=
  GET  /api/inventory     -> {code, data:{items,summary}, msg}
  GET  /api/transactions  -> {code, data:[...], msg}
  POST /api/inbound       <- {productCode, qty, operator, remark, warehouse?}
  POST /api/outbound      <- 同上
  统一响应：code=0 成功，code=1 失败

探测接口（真实客户系统暂无，用于验证外部作用域绑定与身份导入）：
  GET /api/orgs                     组织 → 我方"外部租户"
  GET /api/warehouses[?org=]        仓库，按组织过滤
  GET /api/users[?org=]             账号，按组织过滤

**关键：库存按仓库独立记账**。这样才能验证「智能体绑了哪个外部仓库」是否真的生效——
绑北京中心仓的智能体入库，上海保税仓的库存不该变。
"""
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ORGS = [
    {"id": "ORG-BJ", "name": "北京总部"},
    {"id": "ORG-SH", "name": "上海分公司"},
]
WAREHOUSES = [
    {"id": "WH-BJ-01", "name": "北京中心仓", "org": "ORG-BJ"},
    {"id": "WH-BJ-02", "name": "北京备件仓", "org": "ORG-BJ"},
    {"id": "WH-SH-01", "name": "上海保税仓", "org": "ORG-SH"},
]
USERS = [
    {"id": "u1001", "login": "zhangsan", "realName": "张三", "org": "ORG-BJ"},
    {"id": "u1002", "login": "lisi",     "realName": "李四", "org": "ORG-BJ"},
    {"id": "u1003", "login": "wangwu",   "realName": "王五", "org": "ORG-SH"},
    {"id": "u1004", "login": "zhaoliu",  "realName": "赵六", "org": "ORG-SH"},
]
# 物料主数据（跨仓共用）
CATALOG = [
    {"id": "p1", "code": "SP-001", "name": "矿泉水", "spec": "500ml",  "unit": "瓶"},
    {"id": "p2", "code": "SP-002", "name": "打印纸", "spec": "A4 80g", "unit": "箱"},
    {"id": "p3", "code": "SP-003", "name": "签字笔", "spec": "黑色",   "unit": "支"},
]
# 库存**按仓库独立**：{warehouse_id: {code: {stock, minStock}}}
STOCK = {
    "WH-BJ-01": {"SP-001": {"stock": 150, "minStock": 50},
                 "SP-002": {"stock": 30,  "minStock": 20},
                 "SP-003": {"stock": 8,   "minStock": 50}},
    "WH-BJ-02": {"SP-001": {"stock": 40,  "minStock": 10},
                 "SP-003": {"stock": 200, "minStock": 50}},
    "WH-SH-01": {"SP-001": {"stock": 500, "minStock": 100},
                 "SP-002": {"stock": 5,   "minStock": 20}},
}
DEFAULT_WH = "WH-BJ-01"
TRANSACTIONS = []


def _status(stock, minStock):
    if stock <= 0:
        return {"key": "out", "label": "缺货", "cls": "tag-out"}
    if stock < minStock:
        return {"key": "low", "label": "库存不足", "cls": "tag-low"}
    return {"key": "normal", "label": "正常", "cls": "tag-normal"}


def _products_of(wh):
    out = []
    for code, st in (STOCK.get(wh) or {}).items():
        meta = next(c for c in CATALOG if c["code"] == code)
        out.append({**meta, "stock": st["stock"], "minStock": st["minStock"],
                    "warehouse": wh})
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u, q = urlparse(self.path), parse_qs(urlparse(self.path).query)
        wh = (q.get("warehouse") or [DEFAULT_WH])[0]
        org = (q.get("org") or [None])[0]

        if u.path == "/api/products":
            if wh not in STOCK:
                return self._send({"code": 1, "data": None, "msg": f"未知仓库 {wh}"})
            return self._send({"code": 0, "data": _products_of(wh), "msg": "ok"})

        if u.path == "/api/inventory":
            items = [{**p, "status": _status(p["stock"], p["minStock"])}
                     for p in _products_of(wh)]
            return self._send({"code": 0, "data": {
                "items": items,
                "summary": {"total": len(items),
                            "totalStock": sum(i["stock"] for i in items),
                            "lowCount": sum(1 for i in items if i["stock"] < i["minStock"])},
            }, "msg": "ok"})

        if u.path == "/api/transactions":
            data = [t for t in reversed(TRANSACTIONS)
                    if not q.get("warehouse") or t["warehouse"] == wh]
            return self._send({"code": 0, "data": data, "msg": "ok"})

        if u.path == "/api/orgs":
            return self._send({"code": 0, "data": ORGS, "msg": "ok"})
        if u.path == "/api/warehouses":
            return self._send({"code": 0, "msg": "ok",
                               "data": [w for w in WAREHOUSES if not org or w["org"] == org]})
        if u.path == "/api/users":
            return self._send({"code": 0, "msg": "ok",
                               "data": [x for x in USERS if not org or x["org"] == org]})
        return self._send({"code": 1, "data": None, "msg": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send({"code": 1, "data": None, "msg": "请求体不是合法 JSON"})
        if u.path not in ("/api/inbound", "/api/outbound"):
            return self._send({"code": 1, "data": None, "msg": "not found"}, 404)

        wh = (body.get("warehouse") or DEFAULT_WH).strip()
        if wh not in STOCK:
            return self._send({"code": 1, "data": None, "msg": f"未知仓库 {wh}"})
        code = (body.get("productCode") or "").strip()
        if not code:
            return self._send({"code": 1, "data": None, "msg": "产品编码不能为空"})
        qty = int(body.get("qty") or 0)
        if qty <= 0:
            return self._send({"code": 1, "data": None, "msg": "数量必须大于 0"})
        st = STOCK[wh].get(code)
        if st is None:
            return self._send({"code": 1, "data": None,
                               "msg": f"仓库 {wh} 没有产品 {code}"})

        is_in = u.path.endswith("inbound")
        if not is_in and st["stock"] < qty:
            return self._send({"code": 1, "data": None,
                               "msg": f"库存不足,当前库存:{st['stock']}"})
        st["stock"] += qty if is_in else -qty

        meta = next(c for c in CATALOG if c["code"] == code)
        tx = {"id": f"t{len(TRANSACTIONS)+1}",
              "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
              "type": "in" if is_in else "out", "warehouse": wh,
              "productId": meta["id"], "code": code, "name": meta["name"], "qty": qty,
              "operator": body.get("operator") or "", "remark": body.get("remark") or ""}
        TRANSACTIONS.append(tx)
        return self._send({"code": 0, "msg": "ok", "data": {
            "transaction": tx,
            "product": {**meta, "stock": st["stock"], "warehouse": wh,
                        "status": _status(st["stock"], st["minStock"])}}})


if __name__ == "__main__":
    import os
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("MOCK_WMS_PORT", "3100"))), H).serve_forever()
