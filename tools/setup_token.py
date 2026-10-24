"""一次性配置脚本：把 MCP Token 写入本地环境，不经过任何对话或网络。

用法：
    python tools/setup_token.py

运行后按提示粘贴 Token（输入不回显），脚本会：
    1. 写入 WorkBuddy 连接器配置 ~/.workbuddy-ai/mcp.json（合并，不覆盖其他服务）
    2. 写入项目根目录 .env（已在 .gitignore 中）
    3. 回显打码后的 Token 供你核对

Token 只写本地文件，脚本不上传、不打印明文。
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_JSON = Path.home() / ".workbuddy-ai" / "mcp.json"
ENV_FILE = ROOT / ".env"
SERVER_NAME = "mcd-mcp"
MCP_URL = "https://mcp.mcd.cn"


def mask(token: str) -> str:
    if len(token) <= 12:
        return "*" * len(token)
    return f"{token[:4]}{'*' * 8}{token[-4:]}"


def read_token() -> str:
    print("请粘贴 MCP Token（粘贴后直接回车，输入不会显示在屏幕上）")
    print("Token 从 https://open.mcd.cn/mcp 点【控制台】→【激活】获取\n")
    try:
        token = getpass.getpass("Token: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消")
        sys.exit(1)

    if not token:
        print("✗ Token 为空，请重新运行")
        sys.exit(1)
    if any(ch.isspace() for ch in token):
        print("✗ Token 中间含有空白字符，可能是复制时带了换行，请重新复制")
        sys.exit(1)
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
        print("（已自动去掉开头的 'Bearer ' 前缀）")
    if len(token) < 16:
        print(f"⚠ Token 只有 {len(token)} 个字符，比预期短，确认没复制漏？继续写入。")
    return token


def write_mcp_json(token: str) -> None:
    MCP_JSON.parent.mkdir(parents=True, exist_ok=True)
    config: dict = {}
    if MCP_JSON.exists():
        try:
            config = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = MCP_JSON.with_suffix(".json.bak")
            MCP_JSON.rename(backup)
            print(f"⚠ 原 mcp.json 不是合法 JSON，已备份为 {backup.name}")
            config = {}

    servers = config.setdefault("mcpServers", {})
    existed = SERVER_NAME in servers
    servers[SERVER_NAME] = {
        "type": "streamablehttp",
        "url": MCP_URL,
        "headers": {"Authorization": f"Bearer {token}"},
    }
    MCP_JSON.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{'✓ 已更新' if existed else '✓ 已写入'} {MCP_JSON}")


def write_env(token: str) -> None:
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = [
            ln for ln in ENV_FILE.read_text(encoding="utf-8").splitlines()
            if not ln.startswith("MCD_MCP_TOKEN=")
        ]
    lines.append(f"MCD_MCP_TOKEN={token}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass
    print(f"✓ 已写入 {ENV_FILE}")


def main() -> int:
    print("=" * 56)
    print(" 麦会点 · MCP Token 本地配置")
    print("=" * 56)

    token = read_token()
    print()
    write_mcp_json(token)
    write_env(token)

    print()
    print(f"打码核对：{mask(token)}   长度 {len(token)}")
    print()
    print("接下来：")
    print("  1. 回到 WorkBuddy，左侧【连接器】→【自定义连接器】")
    print(f"  2. 找到 {SERVER_NAME}，点【启用】/【信任】")
    print("  3. 回来告诉我「配好了」，我跑 probe 验证")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
