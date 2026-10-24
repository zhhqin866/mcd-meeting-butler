"""选品与预算求解。

设计取舍：预算、忌口、热量是硬约束，交给确定性算法；大模型只负责
把自然语言转成参数、把结果讲成人话。

求解方式：对「主食 × 小食 × 饮品」做小规模穷举（菜单量级通常 < 200，
组合数在千级以内），用代价函数挑选，而不是逐项贪心——贪心会稳定地
选出最便宜的单品，然后凑出一份热量严重不足的餐。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# 时段 -> (热量区间下限, 上限)
TIME_WINDOWS = {
    "morning": (250, 500),
    "lunch": (600, 900),
    "afternoon": (400, 650),
    "evening": (600, 900),
}

WINDOW_LABEL = {
    "morning": "上午场",
    "lunch": "午餐场",
    "afternoon": "下午场",
    "evening": "晚间场",
}

CATEGORY_RULES = [
    ("coffee", ("咖啡", "拿铁", "美式", "卡布", "摩卡", "麦咖啡")),
    ("drink", ("可乐", "雪碧", "果汁", "汁", "茶", "矿泉水", "饮")),
    ("side", ("薯条", "鸡块", "鸡翅", "派", "沙拉", "玉米", "小食", "麦乐鸡", "苹果片")),
    ("combo", ("套餐", "组合", "超值", "三件")),
    ("main", ("汉堡", "堡", "卷", "饭", "粥", "油条", "松饼", "鸡腿", "巨无霸", "麦香鱼", "鱼柳")),
]

ALLERGEN_MAP = {
    "牛肉": ("牛肉", "牛", "beef"),
    "猪肉": ("猪肉", "猪", "培根", "火腿", "pork"),
    "鸡肉": ("鸡肉", "鸡", "chicken"),
    "鱼": ("鱼", "鳕", "fish"),
    "虾": ("虾", "shrimp"),
    "花生": ("花生", "peanut"),
    "坚果": ("坚果", "杏仁", "核桃", "nut"),
    "奶": ("奶", "芝士", "奶酪", "乳", "milk", "cheese"),
    "蛋": ("蛋", "egg"),
    "麸质": ("面包", "堡", "麦", "面", "wheat", "gluten"),
}

# 代价函数权重。注意：预算不在这里——预算是硬约束，先过滤再评分，
# 见 plan() 里的两段式求解。
W_KCAL = 1 / 50.0       # 每偏离目标 1 kcal
W_KCAL_CAP = 6.0        # 超热量上限的额外惩罚
W_NO_COFFEE = 3.0       # 需要咖啡却没给
W_PRICE = 0.01          # 同分时偏好便宜
W_IMPUTED = 0.8         # 每有一项热量靠估算，加一点惩罚，避免用缺数据蒙过去


@dataclass
class Item:
    code: str
    name: str
    price: float
    category: str = "other"
    kcal: float | None = None
    protein: float | None = None
    sodium: float | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.name}(¥{self.price:.1f})"


@dataclass
class MeetingParams:
    people: int
    budget: float          # 人均预算
    start: str = ""
    duration: int = 120
    avoid: list[str] = field(default_factory=list)
    need_coffee: bool = False
    max_kcal: float | None = None
    address: str = ""
    window_hint: str = ""  # 门店实际供餐时段（morning/lunch/afternoon/evening），优先于按小时推断


# ------------------------------------------------------------------ 归一化

def classify(name: str) -> str:
    for cat, keys in CATEGORY_RULES:
        if any(k in name for k in keys):
            return cat
    return "other"


def _first_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group()) if m else None


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _dig_list(raw: Any) -> list[Any]:
    """在任意嵌套结构中找出第一个「看起来像列表」的数组。"""
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    for key in ("meals", "products", "items", "list", "data", "result", "mealList", "productList"):
        val = raw.get(key)
        if isinstance(val, list) and val:
            return val
        if isinstance(val, dict):
            found = _dig_list(val)
            if found:
                return found
    for val in raw.values():
        if isinstance(val, (list, dict)):
            found = _dig_list(val)
            if found:
                return found
    return []


def normalize_meals(raw: Any) -> list[Item]:
    """把 query-meals 的返回结构归一化为 Item 列表。

    字段命名在不同 Tool 间不完全一致，这里做宽容匹配；
    拿不到名称或价格的条目直接丢弃，不猜。
    """
    items: list[Item] = []
    for r in _dig_list(raw):
        if not isinstance(r, dict):
            continue
        name = _pick(r, "name", "productName", "mealName", "title", "cnName")
        code = _pick(r, "code", "productCode", "mealCode", "id", "skuCode")
        price = _first_number(_pick(r, "price", "salePrice", "currentPrice", "amount", "unitPrice"))
        if not name or price is None:
            continue

        tags = [str(t) for t in (r.get("tags") or []) if t]
        # 配料信息可能挂在 ingredients / components / desc 上
        for key in ("ingredients", "components", "material", "desc", "description"):
            val = r.get(key)
            if isinstance(val, list):
                tags.extend(str(x) for x in val)
            elif isinstance(val, str) and val:
                tags.append(val)

        items.append(
            Item(
                code=str(code or name),
                name=str(name),
                price=price,
                category=classify(str(name)),
                tags=tags,
                raw=r,
            )
        )
    return items


def attach_nutrition(items: list[Item], nutrition_raw: Any) -> None:
    """用 list-nutrition-foods 的结果补齐热量与蛋白，按名称匹配。"""
    index: dict[str, dict[str, Any]] = {}
    for r in _dig_list(nutrition_raw):
        if isinstance(r, dict):
            name = _pick(r, "name", "productName", "mealName", "title")
            if name:
                index[str(name)] = r

    for it in items:
        row = index.get(it.name)
        if row is None:
            for k, v in index.items():
                if k in it.name or it.name in k:
                    row = v
                    break
        if row is None:
            continue
        it.kcal = _first_number(_pick(row, "energy", "kcal", "calorie", "energyKcal", "热量"))
        it.protein = _first_number(_pick(row, "protein", "蛋白质"))
        it.sodium = _first_number(_pick(row, "sodium", "钠"))


# ------------------------------------------------------------------ 约束

def violates(it: Item, avoid: Iterable[str]) -> bool:
    """忌口 / 过敏原硬约束。命中即剔除，不做「建议少吃」式的妥协。

    匹配范围包含餐品名、标签与配料字段。真实场景下配料来自
    query-meal-detail 的套餐组成，本函数只做保守的关键词匹配。
    """
    haystack = f"{it.name} {' '.join(it.tags)}".lower()
    for word in avoid:
        word = word.strip()
        if not word:
            continue
        keys = ALLERGEN_MAP.get(word, (word,))
        if any(k.lower() in haystack for k in keys):
            return True
    return False


def infer_window(start: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", start or "")
    if not m:
        return "lunch"
    hour = int(m.group(1))
    if hour < 11:
        return "morning"
    if hour < 14:
        return "lunch"
    if hour < 18:
        return "afternoon"
    return "evening"


# ------------------------------------------------------------------ 求解

def _median(values: list[float]) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _category_kcal_median(pool: list[Item]) -> dict[str, float]:
    """按类目统计已知热量的中位数，用于补缺失项。"""
    buckets: dict[str, list[float]] = {}
    for it in pool:
        if it.kcal:
            buckets.setdefault(it.category, []).append(float(it.kcal))
    out: dict[str, float] = {}
    for cat, vals in buckets.items():
        med = _median(vals)
        if med is not None:
            out[cat] = med
    # 饮品类缺失时不能按主食中位数补，否则热量虚高
    out.setdefault("drink", 120.0)
    out.setdefault("coffee", 15.0)
    return out


def _upgrade_hint(
    picked: list[Item], pool: list[Item], per_person: float, kcal: float, lo: float
) -> dict[str, Any] | None:
    """热量没达标时，算一笔「加多少钱能补齐」的账。

    比一句「可再加一份小食」有用得多：用户真正想知道的是代价。
    """
    if kcal >= lo:
        return None
    have = {x.code for x in picked}
    best: tuple[float, Item, float] | None = None
    for cand in pool:
        if cand.code in have or not cand.kcal:
            continue
        new_kcal = kcal + cand.kcal
        if new_kcal < lo:
            continue
        new_price = per_person + cand.price
        if best is None or new_price < best[0]:
            best = (new_price, cand, new_kcal)
    if best is None:
        return None
    new_price, cand, new_kcal = best
    return {
        "name": cand.name,
        "price": round(cand.price, 2),
        "new_per_person": round(new_price, 2),
        "new_kcal": round(new_kcal, 1),
        "delta": round(cand.price, 2),
    }


def plan(menu: list[Item], params: MeetingParams) -> dict[str, Any]:
    window = params.window_hint if params.window_hint in TIME_WINDOWS else infer_window(params.start)
    lo, hi = TIME_WINDOWS[window]
    max_kcal = params.max_kcal or hi
    target = (lo + max_kcal) / 2

    pool = [it for it in menu if not violates(it, params.avoid)]
    excluded = [it for it in menu if violates(it, params.avoid)]

    by_cat: dict[str, list[Item]] = {}
    for it in pool:
        by_cat.setdefault(it.category, []).append(it)

    mains = by_cat.get("main", []) + by_cat.get("combo", [])
    if not mains:
        raise ValueError("过滤忌口后没有可选主食，请放宽 --avoid 或换门店")

    # 必须保留 None，否则预算紧时永远凑三件套、直接超支
    sides: list[Item | None] = list(by_cat.get("side", [])) + [None]
    drinks: list[Item | None] = list(by_cat.get("drink", [])) + list(by_cat.get("coffee", [])) + [None]

    # 营养表覆盖不全（实测 47 项餐品只命中一部分）。若因为某一项缺热量就整体
    # 跳过热量约束，求解器会退化成「只挑便宜」，再反过来提示热量不足。
    # 改为：缺的项按同类目已知热量的中位数估算，并记一笔惩罚。
    fallback_kcal = _category_kcal_median(pool)

    def kcal_of(it: Item) -> tuple[float, bool]:
        if it.kcal is not None:
            return float(it.kcal), False
        return fallback_kcal.get(it.category, 0.0), True

    # 预算对行政来说是硬上限——超一块钱也报不了。所以先按预算过滤，只在可行域
    # 里评分；可行域为空时才退一步，给一份「最接近」的方案并明说没有合规解，
    # 而不是让代价函数偷偷替用户放松约束。
    best: tuple[float, list[Item], float, float] | None = None      # 可行域内最优
    closest: tuple[float, list[Item], float, float] | None = None   # 超预算时的兜底

    for m in mains:
        for s in sides:
            for d in drinks:
                combo = [x for x in (m, s, d) if x is not None]
                price = sum(x.price for x in combo)
                pairs = [kcal_of(x) for x in combo]
                kcal = sum(v for v, _ in pairs)
                imputed = sum(1 for _, est in pairs if est)
                no_coffee = params.need_coffee and not any(x.category == "coffee" for x in combo)

                if price > params.budget + 1e-6:
                    # 兜底候选：优先「超得最少」，同超支时再比热量与咖啡
                    over = price - params.budget
                    alt = over * 10 + abs(kcal - target) * W_KCAL + imputed * W_IMPUTED
                    if no_coffee:
                        alt += W_NO_COFFEE
                    if closest is None or alt < closest[0]:
                        closest = (alt, combo, price, kcal)
                    continue

                cost = abs(kcal - target) * W_KCAL
                if kcal > max_kcal:
                    cost += W_KCAL_CAP
                cost += imputed * W_IMPUTED
                if no_coffee:
                    cost += W_NO_COFFEE
                cost += price * W_PRICE

                if best is None or cost < best[0]:
                    best = (cost, combo, price, kcal)

    feasible = best is not None
    if not feasible:
        if closest is None:
            raise ValueError("没有任何组合能凑出方案，请放宽忌口或换门店")
        best = closest

    _, picked, per_person, kcal = best
    total = per_person * params.people
    # 无解时不再建议「加一份小食」——那只会更超预算
    upgrade = _upgrade_hint(picked, pool, per_person, kcal, lo) if feasible else None

    coffees = by_cat.get("coffee", [])
    coffee_available = bool(coffees)
    min_coffee = min((c.price for c in coffees), default=None)

    return {
        "window": window,
        "window_label": WINDOW_LABEL[window],
        "kcal_range": [lo, max_kcal],
        "kcal_target": target,
        "people": params.people,
        "budget_per_person": params.budget,
        "combo": [
            {
                "code": i.code,
                "name": i.name,
                "price": i.price,
                "category": i.category,
                "kcal": i.kcal,
                "estimated": i.kcal is None,
            }
            for i in picked
        ],
        "per_person_price": round(per_person, 2),
        "per_person_kcal": round(kcal, 1),
        "subtotal": round(total, 2),
        "budget_total": round(params.budget * params.people, 2),
        "within_budget": per_person <= params.budget + 1e-6,
        "feasible": feasible,
        "over_budget": round(max(0.0, per_person - params.budget), 2),
        "excluded_by_diet": [i.name for i in excluded],
        "coffee_available": coffee_available,
        "kcal_estimated": [i.name for i in picked if i.kcal is None],
        "upgrade": upgrade,
        "notes": _notes(
            picked, params, per_person, kcal, lo, max_kcal,
            coffee_available, min_coffee, upgrade, feasible,
        ),
    }


def _notes(
    picked, params, per_person, kcal, lo, max_kcal,
    coffee_available=True, min_coffee=None, upgrade=None, feasible=True,
) -> list[str]:
    notes: list[str] = []
    if not feasible:
        over = per_person - params.budget
        notes.append(
            f"没有满足全部条件的方案：人均预算 ¥{params.budget:.0f} 内凑不出合规组合，"
            f"下面这份是最接近的——人均 ¥{per_person:.1f}，超 ¥{over:.1f}"
        )
        notes.append("要压到预算内，可以去掉一项，或换一家起订价更低的门店")
        # 无解时不再叠加「加热量」「加咖啡」这类建议——它们只会让超支更多
        return notes
    if per_person > params.budget + 1e-6:
        notes.append(f"人均 ¥{per_person:.1f} 超出预算 ¥{params.budget:.1f}，建议减一份小食或换单品")
    if kcal > max_kcal + 1e-6:
        notes.append(f"人均 {kcal:.0f} kcal 超出该时段上限 {max_kcal:.0f}，可去掉小食")
    elif kcal < lo - 1e-6:
        if upgrade:
            notes.append(
                f"人均 {kcal:.0f} kcal 低于该时段下限 {lo:.0f}；"
                f"加 ¥{upgrade['delta']:.1f} 来一份{upgrade['name']}"
                f"（人均 ¥{upgrade['new_per_person']:.0f}）可到 {upgrade['new_kcal']:.0f} kcal"
            )
        else:
            notes.append(f"人均 {kcal:.0f} kcal 低于该时段下限 {lo:.0f}，可再加一份小食或饮品")
    if params.need_coffee and not any(i.category == "coffee" for i in picked):
        if coffee_available and min_coffee:
            notes.append(
                f"预算内放不下咖啡（门店最低 ¥{min_coffee:.1f}），"
                f"人均提到 ¥{per_person + min_coffee:.0f} 左右可加一杯"
            )
        elif coffee_available:
            notes.append("本方案未含咖啡，需要的话可手动替换饮品")
        else:
            notes.append("该门店当前没有咖啡类餐品")
    missing = [i.name for i in picked if i.kcal is None]
    if missing:
        # 缺数据项已按类目中位数补齐，约束仍然生效，只是精度差一些
        scope = "全部餐品" if len(missing) == len(picked) else "、".join(missing)
        notes.append(f"{scope}缺营养数据，热量按同类目均值估算")
    if not notes:
        notes.append("方案在预算与热量区间内，可直接下单")
    return notes
