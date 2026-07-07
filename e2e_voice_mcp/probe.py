"""EventProbe —— 事件总线观测器。

订阅 ServiceContainer 的 EventBus，累积服务器下行事件，供测试断言：
- json_events: 所有 INCOMING_JSON 原始 dict（stt / llm / tts / mcp ...）
- states:      DEVICE_STATE_CHANGED 的 new_state 序列
- errors:      NETWORK_ERROR 负载

三层断言里的「事件层 / 数据层」入口。状态层用 states，
自然语言层用 texts('tts')（LLM 非确定，弱断言）。
"""

import asyncio

from src.core.event_bus import Events


class EventProbe:
    def __init__(self, event_bus):
        self.bus = event_bus
        self.json_events = []
        self.states = []
        self.errors = []
        self._waiters = []  # list[(predicate, future)]

    def attach(self):
        self.bus.on(Events.INCOMING_JSON, self._on_json)
        self.bus.on(Events.DEVICE_STATE_CHANGED, self._on_state)
        self.bus.on(Events.NETWORK_ERROR, self._on_error)

    def detach(self):
        self.bus.off(Events.INCOMING_JSON, self._on_json)
        self.bus.off(Events.DEVICE_STATE_CHANGED, self._on_state)
        self.bus.off(Events.NETWORK_ERROR, self._on_error)

    # ---- 事件回调 ----
    async def _on_json(self, data):
        if isinstance(data, dict):
            self.json_events.append(data)
            self._resolve(data)

    async def _on_state(self, data):
        new_state = data.get("new_state") if isinstance(data, dict) else data
        self.states.append(new_state)

    async def _on_error(self, data):
        self.errors.append(data)

    # ---- 等待原语 ----
    def _resolve(self, data):
        for pred, fut in list(self._waiters):
            if not fut.done() and pred(data):
                fut.set_result(data)
                self._waiters.remove((pred, fut))

    async def wait_json(self, timeout=15, **match):
        """等待一条满足 match（字段全等）的 INCOMING_JSON，返回该 dict。

        先扫已收到的历史事件，再挂 future 等未来事件。
        """
        def pred(d):
            return all(d.get(k) == v for k, v in match.items())

        for d in self.json_events:
            if pred(d):
                return d

        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._waiters.append((pred, fut))
        return await asyncio.wait_for(fut, timeout)

    async def wait_mcp_method(self, method, timeout=15):
        """等待一条 type=mcp 且 payload.method==method 的消息。"""
        def has(evts):
            for e in evts:
                p = e.get("payload")
                if e.get("type") == "mcp" and isinstance(p, dict) and p.get("method") == method:
                    return e
            return None

        hit = has(self.json_events)
        if hit:
            return hit

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(0.2)
            hit = has(self.json_events)
            if hit:
                return hit
        raise asyncio.TimeoutError(f"未在 {timeout}s 内收到 mcp method={method}")

    # ---- 查询辅助 ----
    def texts(self, msg_type):
        return [
            e.get("text")
            for e in self.json_events
            if e.get("type") == msg_type and e.get("text")
        ]

    def types(self):
        return [e.get("type") for e in self.json_events]

    def tool_calls(self):
        """设备端收到的 mcp JSON-RPC 消息（tools/call 等）。"""
        return [e for e in self.json_events if e.get("type") == "mcp"]
