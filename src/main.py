"""麦会点 · 会议室团餐管家 —— CLI 入口。

用法：
    python -m src.main probe
    python -m src.main plan --people 8 --budget 40 --address "上海市徐汇区虹梅路1801号" --start "2026-10-24 15:00" --avoid 牛肉,花生 --need-coffee
    python -m src.main order --plan 点餐方案.json --confirm
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import planner, report
from .mcd_client import McdMcpClient, McpError

ROOT = Path(__file__).resolve().parent.parent
MOCK_PATH = ROOT / "examples" / "mock_menu.json"


# ------------------------------------------------------------------ 数据获取

def _mock_data() -> tuple[list[planner.Item], dict[str, Any]]:
    data = json.loads(MOCK_PATH.read_text(encoding="utf-8"))
    items = planner.normalize_meals(data["meals"])
    planner.attach_nutrition(items, data["nutrition"])
    return items, data.get("store", {"name": "示例门店（静态数据）"})


def _live_data(client: McdMcpClient, args) -> tuple[list[planner.Item], dict[str, Any]]:
    print("→ query-nearby-stores 定位门店 ...")
    stores_raw = client.call("query-nearby-stores", {"address": args.address})
    stores = planner._dig_list(stores_raw)
    if not stores:
        raise SystemExit("未找到可用门店，请检查 --address")
    store = stores[0]
    store_name = store.get("name") or store.get("storeName") or "未知门店"
    print(f"  门店：{store_name}")

    print("→ query-meal-assistance 检查团餐资质 ...")
    try:
        assist = client.call("query-meal-assistance", store)
        supported = bool(assist) and not (isinstance(assist, dict) and assist.get("supported") is False)
        print(f"  团餐服务：{'支持' if supported else '不支持，降级为到店取餐'}")
    except McpError as exc:
        supported = False
        assist = {}
        print(f"  团餐资质查询失败（{exc}），降级为到店取餐")

    print("→ query-meals 拉取菜单 ...")
    items = planner.normalize_meals(client.call("query-meals", store))
    if not items:
        raise SystemExit("菜单为空，可能是该门店当前不售卖或返回结构变更，请先跑 probe 检查")
    print(f"  可选餐品 {len(items)} 项")

    print("→ list-nutrition-foods 拉取营养数据 ...")
    try:
        planner.attach_nutrition(items, client.call("list-nutrition-foods", {}))
        hit = sum(1 for i in items if i.kcal)
        print(f"  匹配到营养数据 {hit} 项")
    except McpError as exc:
        print(f"  营养数据获取失败（{exc}），跳过热量约束")

    info = {
        "name": store_name,
        "store": store,
        "meal_assistance": assist,
        "group_meal_supported": supported,
    }
    return items, info


def _price_check(client: McdMcpClient, store: dict[str, Any], combo: list[dict], people: int) -> dict[str, Any] | None:
    """用官方 calculate-price 校验本地估算。"""
    cart = [{"code": c["code"], "quantity": people} for c in combo]
    try:
        return client.call("calculate-price", {**store, "items": cart})
    except McpError as exc:
        print(f"  calculate-price 校验失败（{exc}），沿用本地估算")
        return None


# ------------------------------------------------------------------ 子命令

def cmd_probe(args) -> int:
    client = McdMcpClient()
    info = client.initialize()
    print("✓ 握手成功")
    if info:
        print(f"  server: {info.get('serverInfo', {})}")
    tools = client.list_tools()
    print(f"✓ 可用工具 {len(tools)} 个：")
    for t in tools:
        print(f"  - {t.get('name'):<28} {(t.get('description') or '')[:60]}")
    return 0


def cmd_plan(args) -> int:
    params = planner.MeetingParams(
        people=args.people,
        budget=args.budget,
        start=args.start or "",
        duration=args.duration,
        avoid=[w for w in (args.avoid or "").replace("，", ",").split(",") if w.strip()],
        need_coffee=args.need_coffee,
        max_kcal=args.max_kcal,
        address=args.address,
    )

    if args.mock:
        print("（mock 模式：使用 examples/mock_menu.json 静态数据）")
        items, store = _mock_data()
        client = None
    else:
        client = McdMcpClient()
        items, store = _live_data(client, args)

    result = planner.plan(items, params)
    result["need_coffee"] = params.need_coffee
    result["coupons"] = []

    if client is not None:
        print("→ calculate-price 官方计价校验 ...")
        priced = _price_check(client, store.get("store", {}), result["combo"], params.people)
        if isinstance(priced, dict):
            total = planner._first_number(
                priced.get("totalAmount") or priced.get("payable") or priced.get("totalPrice")
            )
            if total and abs(total - result["subtotal"]) > 1:
                print(f"  本地估算 ¥{result['subtotal']:.2f} ≠ 官方计价 ¥{total:.2f}，以官方为准")
                result["subtotal"] = round(total, 2)
            discount = planner._first_number(priced.get("discountAmount") or priced.get("discount"))
            if discount:
                result["discount"] = round(discount, 2)

    out_xlsx = Path(args.out or "点餐方案.xlsx")
    report.build(result, store, out_xlsx)

    plan_json = out_xlsx.with_suffix(".json")
    plan_json.write_text(
        json.dumps({"params": params.__dict__, "result": result, "store": store},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ---------------------------------------------------------- 终端摘要
    print()
    print(f"■ {result['window_label']} · {params.people} 人 · 人均预算 ¥{params.budget:.0f}")
    print(f"  门店：{store.get('name', '—')}")
    print("  每人一份：")
    for c in result["combo"]:
        kcal = f"  {c['kcal']:.0f} kcal" if c.get("kcal") else ""
        print(f"    · {c['name']:<14} ¥{c['price']:.1f}{kcal}")
    print(f"  人均 ¥{result['per_person_price']:.1f} / {result['per_person_kcal']:.0f} kcal")
    print(f"  合计 ¥{result['subtotal']:.2f}（预算 ¥{result['budget_total']:.2f}）")
    for n in result["notes"]:
        print(f"  ! {n}")
    if result.get("excluded_by_diet"):
        print(f"  已按忌口剔除 {len(result['excluded_by_diet'])} 项")
    print()
    print(f"✓ 报表：{out_xlsx.resolve()}")
    print(f"✓ 方案：{plan_json.resolve()}")
    return 0


def cmd_order(args) -> int:
    plan_file = Path(args.plan)
    if not plan_file.exists():
        raise SystemExit(f"找不到方案文件：{plan_file}")
    data = json.loads(plan_file.read_text(encoding="utf-8"))
    result, store = data["result"], data["store"]

    people = result["people"]
    cart = [{"code": c["code"], "quantity": people} for c in result["combo"]]

    print(f"门店：{store.get('name')}")
    for c in result["combo"]:
        print(f"  {c['name']} × {people}")
    print(f"应付：¥{result['subtotal']:.2f}")

    if not args.confirm:
        print("\n（预演模式，未产生真实订单。确认无误后加 --confirm 再执行）")
        return 0

    client = McdMcpClient()
    order = client.call("create-order", {**store.get("store", {}), "items": cart, "takeWay": args.takeway})
    print("\n✓ 下单成功：")
    print(json.dumps(order, ensure_ascii=False, indent=2)[:1200])
    return 0


# ------------------------------------------------------------------ 参数解析

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcd-meeting-butler", description="麦会点 · 会议室团餐管家")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="检查 Token 与可用工具").set_defaults(func=cmd_probe)

    pp = sub.add_parser("plan", help="生成会议点餐方案")
    pp.add_argument("--people", type=int, required=True, help="会议人数")
    pp.add_argument("--budget", type=float, required=True, help="人均预算（元）")
    pp.add_argument("--address", required=True, help="会议地点，用于定位门店")
    pp.add_argument("--start", default="", help="会议开始时间，如 '2026-10-24 15:00'")
    pp.add_argument("--duration", type=int, default=120, help="会议时长（分钟）")
    pp.add_argument("--avoid", default="", help="忌口/过敏原，逗号分隔")
    pp.add_argument("--need-coffee", action="store_true", help="需要咖啡")
    pp.add_argument("--max-kcal", type=float, default=None, help="人均热量上限")
    pp.add_argument("--mock", action="store_true", help="使用本地静态数据")
    pp.add_argument("--out", default="点餐方案.xlsx", help="Excel 输出路径")
    pp.set_defaults(func=cmd_plan)

    po = sub.add_parser("order", help="按方案下单")
    po.add_argument("--plan", required=True, help="plan 生成的 json 文件")
    po.add_argument("--takeway", default="1", help="取餐方式")
    po.add_argument("--confirm", action="store_true", help="确认真实下单")
    po.set_defaults(func=cmd_order)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except McpError as exc:
        print(f"✗ MCP 错误：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"✗ 无法生成方案：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
