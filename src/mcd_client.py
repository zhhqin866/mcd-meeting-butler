"""最小化的麦当劳 MCP 客户端。

不依赖任何第三方 MCP SDK，只用 requests 完成 Streamable HTTP 握手：
    initialize -> notifications/initialized -> tools/call

兼容 application/json 与 text/event-stream 两种响应格式。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2024-11-05"
MIN_INTERVAL = 0.12  # 秒，控制请求频率，避免触发 600 次/分钟限流


class McpError(RuntimeError):
    pass


class McdMcpClient:
    def __init__(self, token: str | None = None, url: str | None = None, timeout: int = 30):
        self.url = (url or os.environ.get("MCD_MCP_URL") or DEFAULT_URL).rstrip("/")
        self.token = token or os.environ.get("MCD_MCP_TOKEN")
        if not self.token:
            raise McpError(
                "缺少 MCP Token。请先设置环境变量 MCD_MCP_TOKEN，"
                "申请地址 https://open.mcd.cn/mcp"
            )
        self.timeout = timeout
        self._id = 0
        self._last_call = 0.0
        self._session_id: str | None = None
        self._ready = False

        self.http = requests.Session()
        self.http.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
        )

    # ---------------------------------------------------------------- 传输层

    def _throttle(self) -> None:
        gap = time.time() - self._last_call
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        self._last_call = time.time()

    def _parse_body(self, resp: requests.Response) -> dict[str, Any]:
        ctype = resp.headers.get("Content-Type", "")
        text = resp.text

        if "text/event-stream" in ctype or text.lstrip().startswith("event:") or "data:" in text[:200]:
            payloads: list[dict] = []
            for line in text.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    payloads.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
            if not payloads:
                raise McpError(f"SSE 响应中未找到可解析的 data 行：{text[:300]}")
            # 取最后一个带 result / error 的消息
            for item in reversed(payloads):
                if "result" in item or "error" in item:
                    return item
            return payloads[-1]

        try:
            return resp.json()
        except ValueError as exc:
            raise McpError(f"响应不是合法 JSON：{text[:300]}") from exc

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._throttle()
        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        for attempt in range(4):
            resp = self.http.post(self.url, json=payload, headers=headers, timeout=self.timeout)

            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid

            if resp.status_code == 429:
                wait = 2 ** attempt
                time.sleep(wait)
                continue
            if resp.status_code == 401:
                raise McpError("Token 无效或已过期（401），请重新申请并更新 MCD_MCP_TOKEN")
            if resp.status_code >= 400:
                raise McpError(f"HTTP {resp.status_code}：{resp.text[:300]}")

            if not resp.text.strip():
                return {}
            return self._parse_body(resp)

        raise McpError("请求被限流（429），重试 4 次仍失败，请降低调用频率")

    # ---------------------------------------------------------------- 协议层

    def initialize(self) -> dict[str, Any]:
        if self._ready:
            return {}
        self._id += 1
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mcd-meeting-butler", "version": "1.0.0"},
                },
            }
        )
        if "error" in result:
            raise McpError(f"initialize 失败：{result['error']}")

        # 握手完成通知，无 id、无响应体
        try:
            self.http.post(
                self.url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers={"Mcp-Session-Id": self._session_id} if self._session_id else {},
                timeout=self.timeout,
            )
        except requests.RequestException:
            pass

        self._ready = True
        return result.get("result", {})

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        self._id += 1
        result = self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/list", "params": {}})
        if "error" in result:
            raise McpError(f"tools/list 失败：{result['error']}")
        return result.get("result", {}).get("tools", [])

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """调用工具并返回解析后的业务数据。"""
        self.initialize()
        self._id += 1
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            }
        )
        if "error" in result:
            raise McpError(f"{tool} 调用失败：{result['error']}")

        payload = result.get("result", {})
        if payload.get("isError"):
            raise McpError(f"{tool} 返回错误：{self._content_text(payload)}")

        return self._unwrap(tool, self._decode(payload))

    @staticmethod
    def _unwrap(tool: str, data: Any) -> Any:
        """麦当劳 MCP 统一返回 {success, code, message, datetime, traceId, data}。

        success=false 时抛出 McpError（带上 code 与 message），
        成功则剥掉信封直接返回 data，业务层不用每次判 success。
        """
        if isinstance(data, dict) and "success" in data:
            if not data.get("success"):
                raise McpError(f"{tool} 失败：{data.get('message')}（code={data.get('code')}）")
            return data.get("data")
        return data

    @staticmethod
    def _content_text(payload: dict[str, Any]) -> str:
        parts = [c.get("text", "") for c in payload.get("content", []) if c.get("type") == "text"]
        return "\n".join(p for p in parts if p)

    def _decode(self, payload: dict[str, Any]) -> Any:
        """MCP 业务数据通常以 JSON 字符串塞在 content[].text 里，这里统一解析。"""
        if "structuredContent" in payload:
            return payload["structuredContent"]
        text = self._content_text(payload)
        if not text:
            return payload
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
