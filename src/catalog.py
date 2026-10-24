"""从麦当劳 MCP 取数：地址 → 门店 → 助餐服务 → 菜单 → 营养。

真实接口的几个约束（踩过坑，记在这里）：
- `delivery-query-stores` 必传 beType，团餐是 6，麦乐送是 2
- `query-meal-assistance` 在 beType=2/5/6 时必传 beCode
- `query-meals` 返回两部分：categories 只给 code + tags，meals 才是 code → 名称/价格的映射
- 价格字段 currentPrice 是字符串，金额单位是元
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import nutrition
from .mcd_client import McdMcpClient
from .planner import Item, classify

BE_TYPE_GROUP = 6     # 企业团餐
BE_TYPE_DELIVERY = 2  # 麦乐送
ORDER_TYPE_DELIVERY = 2

# 输出脱敏等级：province（默认，只留省级）/ city（留到市/区）/ none（不脱敏，仅本机调试）
MASK_LEVEL = (os.environ.get("MCD_MASK_LEVEL") or "province").strip().lower()

_PROVINCE_SUFFIXES = ("省", "自治区", "特别行政区")
_MUNICIPALITIES = ("北京市", "上海市", "天津市", "重庆市")
_STORE_BRAND = "麦当劳"
_STORE_SUFFIXES = ("得来速餐厅", "得来速", "甜品站", "餐厅", "门店")

# 分类名 → 内部类别
CATEGORY_BY_NAME = {
    "主食": "main",
    "小食甜品": "side",
    "小食": "side",
    "甜品": "side",
    "饮品": "drink",
    "饮料": "drink",
    "麦咖啡": "coffee",
    "咖啡": "coffee",
    "套餐": "combo",
}
# 这些分类太泛，不作为主分类
GENERIC_CATEGORIES = {"人气热卖", "热卖", "推荐", "新品", "限时"}

WINDOW_RE = re.compile(r"([^，,()（）]+)\((\d{1,2}:\d{2})至(\d{1,2}:\d{2})\)")

# 门店时段名 → 内部时段
PERIOD_BY_LABEL = {
    "早餐": "morning",
    "午餐": "lunch",
    "下午茶": "afternoon",
    "夜市": "evening",
    "宵夜": "evening",
}

# 不是「一份餐」的东西：酱料、蘸酱、加购、餐具、包装、服务费
NOT_A_MEAL_RE = re.compile(
    r"(酱$|蘸|配料|加购|换购|餐具|包装|服务费|配送费|随机|盲盒|赠品|优惠券|券$)"
)


@dataclass
class Store:
    storeCode: str
    beCode: str
    name: str
    beType: int = BE_TYPE_GROUP
    orderType: int = ORDER_TYPE_DELIVERY
    reservation: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)

    def windows_on(self, date: str) -> list[tuple[str, str, str]]:
        return self.reservation.get(date, [])


@dataclass
class Assistance:
    code: str
    name: str
    items: list[str]
    enabled: bool
    reason: str = ""

    def __str__(self) -> str:
        return self.name


# ------------------------------------------------------------------ 地址与门店

def mask_address(text: str, level: str | None = None) -> str:
    """隐去详细地址，默认只留到**省级**（`江苏省***`）。

    为什么不留到市/区：会议室团餐的场景里，「哪个省」已经足够解释时段与菜单差异；
    再往下精确到市、区，配合门店名就足以反推真实办公地点，而这个信息对读者毫无价值。
    需要调试时用 `MCD_MASK_LEVEL=city` 或 `none` 临时放宽。
    """
    s = str(text or "").strip()
    if not s:
        return "—"
    lv = (level or MASK_LEVEL).lower()
    if lv == "none":
        return s
    if lv != "city":
        for suffix in _PROVINCE_SUFFIXES:
            i = s.find(suffix)
            if i > 0:
                return s[: i + len(suffix)] + "***"
        for m in _MUNICIPALITIES:
            if s.startswith(m):
                return m + "***"
    # 没有省级信息（或显式要求 city）时，退到**第一个**市/区/县，即市级
    # 注意用第一个而不是最后一个：`甲市乙区…` 要切成 `甲市***`，
    # 用最后一个会得到 `甲市乙区***`——区县一级已经足够定位了。
    idx = [i for i in (s.find("市"), s.find("区"), s.find("县")) if i > 0]
    if idx:
        return s[: min(idx) + 1] + "***"
    return (s[:4] + "***") if len(s) > 4 else "***"


def mask_store_name(name: str) -> str:
    """隐去门店名里的地点段，保留品牌与业态。

    「麦当劳××市××数码城得来速餐厅」——只脱敏地址是不够的，门店名本身就是定位信息：
    商场名 + 城市足以反推办公地点。所以一律换成「麦当劳××××得来速餐厅」。
    """
    s = str(name or "").strip()
    if not s:
        return "—"
    # 已经是占位名（mock 样本）或已脱敏过的，原样返回，别二次打码
    if MASK_LEVEL == "none" or "××" in s or any(k in s for k in ("示例", "静态", "样本")):
        return s
    if not s.startswith(_STORE_BRAND):
        return s
    for suffix in _STORE_SUFFIXES:
        if s.endswith(suffix):
            return f"{_STORE_BRAND}××××{suffix}"
    return f"{_STORE_BRAND}××××"


def mask_store_code(code: str) -> str:
    """门店编码能反查门店，输出里只保留前两位。"""
    s = str(code or "").strip()
    if not s or MASK_LEVEL == "none":
        return s
    return s[:2] + "*" * max(0, len(s) - 2)



def resolve_address(client: McdMcpClient, keyword: str | None = None) -> dict[str, Any]:
    """取配送地址。keyword 非空时做模糊匹配，匹配不上退回第一条并打标记。"""
    data = client.call("delivery-query-addresses", {})
    addresses = (data or {}).get("addresses") or []
    if not addresses:
        raise RuntimeError(
            "账号下没有配送地址。请先在麦当劳 APP 或小程序里添加一个收货地址，再回来运行。"
        )
    if keyword:
        for a in addresses:
            full = a.get("fullAddress") or ""
            if keyword in full or full in keyword:
                return a
        # 匹配不上不要静默用第一条：用户会以为查的是他给的地址，
        # 实际拿到的是别的城市门店，后面全错。
        fallback = dict(addresses[0])
        fallback["_fallback"] = True
        return fallback
    return addresses[0]


def find_store(client: McdMcpClient, address_id: str, be_type: int = BE_TYPE_GROUP) -> Store:
    data = client.call("delivery-query-stores", {"addressId": address_id, "beType": be_type})
    stores = data if isinstance(data, list) else (data or {}).get("stores") or []
    if not stores:
        raise RuntimeError(f"该地址附近没有可用的{'团餐' if be_type == BE_TYPE_GROUP else '外送'}门店")

    # 优先营业中的门店
    stores = sorted(stores, key=lambda s: 0 if s.get("businessStatus", True) else 1)
    s = stores[0]

    reservation: dict[str, list[tuple[str, str, str]]] = {}
    for opt in s.get("reservationTimeOptions") or []:
        date = opt.get("date")
        text = opt.get("reservationOptionText") or ""
        if date:
            reservation[date] = WINDOW_RE.findall(text)

    return Store(
        storeCode=str(s.get("storeCode")),
        beCode=str(s.get("beCode") or ""),
        name=s.get("storeName") or "未知门店",
        beType=be_type,
        orderType=ORDER_TYPE_DELIVERY,
        reservation=reservation,
    )


# ------------------------------------------------------------------ 助餐服务

def query_assistance(client: McdMcpClient, store: Store, reservation: str | None = None) -> list[Assistance]:
    args: dict[str, Any] = {
        "storeCode": store.storeCode,
        "beType": store.beType,
        "orderType": store.orderType,
        "beCode": store.beCode,
    }
    if reservation:
        args["reservationDate"] = reservation
    data = client.call("query-meal-assistance", args)
    out: list[Assistance] = []
    for it in (data or {}).get("mealAssistanceItems") or []:
        out.append(
            Assistance(
                code=it.get("gmServiceCode") or "",
                name=it.get("gmServiceName") or "",
                items=list(it.get("serviceItems") or []),
                enabled=bool(it.get("enable")),
                reason=it.get("unusableReason") or "",
            )
        )
    return out


def pick_assistance(items: list[Assistance]) -> Assistance | None:
    """优先选服务等级最高的可用项：专人摆台 > 专人分餐 > 保鲜速达。"""
    order = ["摆台", "分餐", "速达"]
    usable = [a for a in items if a.enabled and a.code]
    if not usable:
        return None
    for keyword in order:
        for a in usable:
            if keyword in a.name:
                return a
    return usable[0]


def lead_time_hint(items: list[Assistance]) -> list[str]:
    """把「需提前 4h 预约」这类不可用原因翻译成可执行的建议。"""
    hints = []
    for a in items:
        if not a.enabled and a.reason:
            hints.append(f"{a.name}：{a.reason}")
    return hints


# ------------------------------------------------------------------ 菜单

def _pick_category(cats: list[str]) -> str:
    for c in cats:
        if c not in GENERIC_CATEGORIES:
            return c
    return cats[0] if cats else ""


def fetch_menu(client: McdMcpClient, store: Store) -> tuple[list[Item], list[str]]:
    data = client.call(
        "query-meals",
        {
            "storeCode": store.storeCode,
            "orderType": store.orderType,
            "beType": store.beType,
            "beCode": store.beCode,
        },
    )
    data = data or {}
    categories = data.get("categories") or []
    meals: dict[str, Any] = data.get("meals") or {}

    membership: dict[str, dict[str, Any]] = {}
    for cat in categories:
        cname = cat.get("name") or ""
        for m in cat.get("meals") or []:
            code = str(m.get("code") or "")
            if not code:
                continue
            slot = membership.setdefault(code, {"cats": [], "tags": []})
            slot["cats"].append(cname)
            slot["tags"].extend(str(t) for t in (m.get("tags") or []) if t)

    items: list[Item] = []
    for code, info in membership.items():
        meta = meals.get(code) or {}
        name = meta.get("name")
        price = meta.get("currentPrice") or meta.get("originalPrice")
        if not name or price is None:
            continue
        if NOT_A_MEAL_RE.search(str(name)):
            continue
        try:
            price_val = float(str(price).replace(",", ""))
        except ValueError:
            continue

        cname = _pick_category(info["cats"])
        cat = CATEGORY_BY_NAME.get(cname) or classify(str(name))
        items.append(
            Item(
                code=code,
                name=str(name),
                price=price_val,
                category=cat,
                tags=info["tags"],
                raw={"category": cname, "image": meta.get("image")},
            )
        )
    return items, [c.get("name") or "" for c in categories]


def fetch_nutrition(client: McdMcpClient) -> dict[str, nutrition.Nutrition]:
    return nutrition.parse(client.call("list-nutrition-foods", {}))


def attach_nutrition(items: list[Item], table: dict[str, nutrition.Nutrition]) -> int:
    hit = 0
    for it in items:
        n = nutrition.lookup(table, it.name)
        if n is None:
            continue
        it.kcal = n.kcal
        it.protein = n.protein
        it.sodium = n.sodium
        if n.kcal:
            hit += 1
    return hit


# ------------------------------------------------------------------ 计价

def price_combo(
    client: McdMcpClient,
    store: Store,
    combo: list[dict[str, Any]],
    people: int,
    gm_service: str | None,
    reservation: str | None = None,
) -> dict[str, Any] | None:
    """官方计价。金额单位是分，这里统一换算成元。

    注意：带 gmServiceCode 时必须同时传 reservationDate，
    否则返回 600062「助餐服务不可用」。
    """
    args: dict[str, Any] = {
        "storeCode": store.storeCode,
        "orderType": store.orderType,
        "beType": store.beType,
        "beCode": store.beCode,
        "items": [{"productCode": c["code"], "quantity": people} for c in combo],
    }
    if gm_service:
        args["gmServiceCode"] = gm_service
    if reservation:
        args["reservationDate"] = reservation
    data = client.call("calculate-price", args)
    if not isinstance(data, dict):
        return None

    def yuan(key: str) -> float:
        return round(float(data.get(key) or 0) / 100.0, 2)

    return {
        "original": yuan("originalPrice"),
        "payable": yuan("price"),
        "discount": yuan("discount"),
        "delivery": yuan("deliveryPrice"),
        "packing": yuan("packingPrice"),
        "promotion": (data.get("enjoyed") or {}).get("promotionName") or "",
        "promotion_rate": (data.get("enjoyed") or {}).get("realDiscount"),
        "assistance": [x.get("name") for x in (data.get("mealAssistanceList") or [])],
        "lines": [
            {
                "code": p.get("productCode"),
                "name": p.get("productName"),
                "quantity": p.get("quantity"),
                "subtotal": round(float(p.get("subtotal") or 0) / 100.0, 2),
            }
            for p in (data.get("productList") or [])
        ],
    }


# ------------------------------------------------------------------ 预约时段

def suggest_reservation(store: Store, meeting: datetime) -> tuple[str, str, str]:
    """根据会议时间推荐预约时段。

    返回 (reservationDate, 说明, 内部时段)。内部时段取自门店真实供餐时段，
    比按小时推断可靠 —— 同一小时在不同门店可能属于「下午茶」或「夜市」。
    """
    date = meeting.strftime("%Y-%m-%d")
    hhmm = meeting.strftime("%H:%M")
    windows = store.windows_on(date)
    for label, start, end in windows:
        if start <= hhmm <= end:
            return (
                f"{date} {hhmm}",
                f"会议时间落在门店「{label}」时段（{start}–{end}）",
                PERIOD_BY_LABEL.get(label, ""),
            )
    if windows:
        label, start, end = windows[0]
        return (
            f"{date} {start}",
            f"会议时间不在供餐时段内，已对齐到最近时段「{label}」（{start}–{end}）",
            PERIOD_BY_LABEL.get(label, ""),
        )
    return f"{date} {hhmm}", "该门店未返回预约时段信息", ""


def hours_until(meeting: datetime) -> float:
    return (meeting - datetime.now()).total_seconds() / 3600.0


def premium_service_available(meeting: datetime) -> bool:
    """专人分餐/摆台需提前 4 小时预约。"""
    return hours_until(meeting) >= 4.0
