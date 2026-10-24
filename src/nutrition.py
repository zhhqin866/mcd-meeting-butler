"""营养数据解析。

list-nutrition-foods 返回的不是 JSON，而是一张紧凑表：

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
      猪柳麦满分,null,1288,308,16,16,24,781,213
      ...

首行给出总行数与列名，后续每行以两个空格缩进、逗号分隔。
这里只取实际用得到的列：能量(kcal)、蛋白质、钠。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADER_RE = re.compile(r"^\[(\d+)\]\{([^}]*)\}\s*:?\s*$")


@dataclass
class Nutrition:
    name: str
    kcal: float | None = None
    protein: float | None = None
    sodium: float | None = None
    calcium: float | None = None


def _num(value: str) -> float | None:
    value = value.strip()
    if not value or value.lower() in ("null", "none", "-"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse(raw: object) -> dict[str, Nutrition]:
    """把紧凑表解析成 {餐品名: Nutrition}。"""
    if isinstance(raw, dict):
        return {}
    if not isinstance(raw, str):
        return {}

    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return {}

    header = HEADER_RE.match(lines[0].strip())
    if not header:
        return {}

    fields = [f.strip() for f in header.group(2).split(",")]
    idx = {name: i for i, name in enumerate(fields)}
    ncols = len(fields)

    i_name = idx.get("productName", 0)
    i_kcal = idx.get("energyKcal")
    i_protein = idx.get("protein")
    i_sodium = idx.get("sodium")
    i_calcium = idx.get("calcium")

    out: dict[str, Nutrition] = {}
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(",")]
        if len(cells) > ncols:
            # 名称里可能带逗号，多出来的列并入名称
            extra = len(cells) - ncols
            cells = [",".join(cells[: extra + 1])] + cells[extra + 1 :]
        if len(cells) < ncols:
            continue
        name = cells[i_name]
        if not name:
            continue
        out[name] = Nutrition(
            name=name,
            kcal=_num(cells[i_kcal]) if i_kcal is not None else None,
            protein=_num(cells[i_protein]) if i_protein is not None else None,
            sodium=_num(cells[i_sodium]) if i_sodium is not None else None,
            calcium=_num(cells[i_calcium]) if i_calcium is not None else None,
        )
    return out


# 菜单名与营养表名的写法差异：商标符号、品牌前缀、杯型后缀。
# 例：菜单「麦咖啡™美式」 ↔ 营养表「热美式中杯」，直接包含匹配匹配不上。
_SIZE_TOKENS = ("超大杯", "标准杯", "中杯", "大杯", "小杯")
_NOISE_TOKENS = (
    "™", "®", "©", "麦当劳", "麦咖啡",
    "（", "）", "(", ")", "-", "－", "_", " ", "\u3000",
)


def normalize(name: str) -> str:
    """去掉商标、品牌前缀与杯型，只留可比的餐品核心名。"""
    s = str(name or "")
    for token in _SIZE_TOKENS + _NOISE_TOKENS:
        s = s.replace(token, "")
    return s.strip().lower()


def _lcs_len(a: str, b: str) -> int:
    """最长公共子串长度。用于「美式」这类短核心名的兜底匹配。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def lookup(table: dict[str, Nutrition], dish_name: str) -> Nutrition | None:
    """按名称匹配营养数据，逐级放宽：

    1. 原名精确命中
    2. 归一化后精确命中（去 ™ / 杯型 / 空格）
    3. 归一化后双向包含，取最长的一方（避免「麦香鸡」误命中「麦香鸡腿堡」）
    4. 最长公共子串占比 ≥ 0.75 时兜底
    """
    if not table or not dish_name:
        return None
    if dish_name in table:
        return table[dish_name]

    target = normalize(dish_name)
    if not target:
        return None

    norm_index: dict[str, Nutrition] = {}
    for key, val in table.items():
        nk = normalize(key)
        if nk:
            norm_index.setdefault(nk, val)

    if target in norm_index:
        return norm_index[target]

    # 包含匹配：优先取核心名更长的，减少误配
    contained: tuple[int, Nutrition] | None = None
    for nk, val in norm_index.items():
        if len(nk) < 2:
            continue
        if nk in target or target in nk:
            score = min(len(nk), len(target))
            if contained is None or score > contained[0]:
                contained = (score, val)
    if contained is not None:
        return contained[1]

    best: tuple[float, Nutrition] | None = None
    for nk, val in norm_index.items():
        common = _lcs_len(target, nk)
        if not common:
            continue
        ratio = common / min(len(target), len(nk))
        if ratio >= 0.75 and (best is None or ratio > best[0]):
            best = (ratio, val)
    return best[1] if best else None
