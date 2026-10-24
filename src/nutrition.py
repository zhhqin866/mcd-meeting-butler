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


def lookup(table: dict[str, Nutrition], dish_name: str) -> Nutrition | None:
    """按名称匹配营养数据，支持「套餐名包含单品名」这类包含匹配。"""
    if not table or not dish_name:
        return None
    if dish_name in table:
        return table[dish_name]
    for key, val in table.items():
        if key and (key in dish_name or dish_name in key):
            return val
    return None
