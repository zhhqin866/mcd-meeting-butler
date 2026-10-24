"""把一次真实运行的终端输出渲染成 SVG，供 README 展示。

为什么用 SVG 而不是截图：截图是位图，缩放会糊，也容易被怀疑是摆拍。
SVG 里存的就是原始文本，可以直接搜到「专人摆台」「366.12」这些真实数字。

用法：
    python tools/make_demo_svg.py 输出.svg < 终端输出.txt
"""

from __future__ import annotations

import sys
import unicodedata
from html import escape
from pathlib import Path

BG = "#0d1117"
BAR = "#161b22"
FG = "#c9d1d9"
DIM = "#8b949e"
ACCENT = "#3fb950"   # ✓
WARN = "#d29922"     # !
STEP = "#58a6ff"     # →

FONT = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', 'Microsoft YaHei', monospace")

LINE_H = 23
PAD_X = 22
PAD_TOP = 58
FONT_SIZE = 13.5
CHAR_W = 8.35  # 13.5px 等宽字体的实际字宽，用于估算画布宽度


def color_of(line: str) -> str:
    s = line.strip()
    if s.startswith("✓"):
        return ACCENT
    if s.startswith("!"):
        return WARN
    if s.startswith("→"):
        return STEP
    if s.startswith("■"):
        return FG
    if s.startswith(("·", "! ")):
        return FG
    return FG


def display_width(line: str) -> int:
    """等宽字体里中日韩字符占两格，直接用 len() 会算窄，文字会溢出边框。"""
    w = 0
    for ch in line:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def render(lines: list[str]) -> str:
    cols = max((display_width(ln) for ln in lines), default=60)
    width = max(680, int(cols * CHAR_W) + PAD_X * 2)
    height = PAD_TOP + len(lines) * LINE_H + 24

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{FONT}" font-size="{FONT_SIZE}">',
        '<defs><clipPath id="r"><rect width="100%" height="100%" rx="10"/></clipPath></defs>',
        f'<g clip-path="url(#r)">',
        f'<rect width="{width}" height="{height}" fill="{BG}"/>',
        f'<rect width="{width}" height="38" fill="{BAR}"/>',
    ]
    for i, dot in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        parts.append(f'<circle cx="{22 + i * 20}" cy="19" r="6" fill="{dot}"/>')
    parts.append(
        f'<text x="{width / 2:.0f}" y="24" fill="{DIM}" text-anchor="middle" '
        f'font-size="12">python -m src.main plan --people 8 --budget 50</text>'
    )

    y = PAD_TOP
    for ln in lines:
        if ln.strip():
            parts.append(
                f'<text x="{PAD_X}" y="{y}" fill="{color_of(ln)}" xml:space="preserve">'
                f'{escape(ln)}</text>'
            )
        y += LINE_H
    parts.append("</g></svg>")
    return "\n".join(parts)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/demo-run.svg")
    raw = sys.stdin.read().replace("\r\n", "\n").rstrip("\n")
    lines = raw.split("\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(lines), encoding="utf-8")
    print(f"✓ {out}（{len(lines)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
