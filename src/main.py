"""麦会点 · 会议室团餐管家 —— CLI 入口。

用法：
    python -m src.main probe
    python -m src.main plan --people 8 --budget 40 --start "2026-10-24 17:00" --avoid 牛肉,花生 --need-coffee
    python -m src.main order --plan 点餐方案.json --confirm
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import catalog, planner, report
from .mcd_client import McdMcpClient, McpError

ROOT = Path(__file__).resolve().parent.parent
MOCK_PATH = ROOT / "examples" / "mock_menu.json"

TIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M", "%m-%d %H:%M", "%H:%M")


def parse_meeting(text: str | None) -> datetime:
    if not text:
        return datetime.now().replace(second=0, microsecond=0)
    for fmt in TIME_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            now = datetime.now()
            dt = now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
        elif fmt == "%m-%d %H:%M":
            dt = dt.replace(year=datetime.now().year)
        return dt
    raise SystemExit(f"看不懂的时间格式：{text}，请写成 '2026-10-24 17:00'")


# ------------------------------------------------------------------ mock

def _mock_data() -> tuple[list[planner.Item], dict[str, Any], dict[str, Any]]:
    data = json.loads(MOCK_PATH.read_text(encoding="utf-8"))
    items = planner.normalize_meals(data["meals"])
    planner.attach_nutrition(items, data["nutrition"])
    store = {"name": data.get("store", {}).get("name", "示例门店（静态数据）"), "mock": True}
    extra = {
        "assistance": "保鲜速达（静态样本）",
        "assistance_all": [],
        "reservation": "",
        "reservation_note": "mock 模式不查询真实门店时段",
        "categories": [],
        "nutrition_hit": sum(1 for i in items if i.kcal),
    }
    return items, store, extra


# ------------------------------------------------------------------ 真实取数

def _live_data(client: McdMcpClient, args, meeting: datetime):
    print("→ delivery-query-addresses 取配送地址 ...")
    addr = catalog.resolve_address(client, args.address)
    print(f"  地址：{catalog.mask_address(addr.get('fullAddress', ''))}")
    if addr.get("_fallback"):
        print(f"  ! 账号里没有匹配「{args.address}」的地址，已退回第一条；门店会按该地址查")

    print(f"→ delivery-query-stores 找门店（beType={args.be_type}）...")
    store = catalog.find_store(client, addr["addressId"], args.be_type)
    print(f"  门店：{store.name}（storeCode={store.storeCode}）")

    reservation, note, period = catalog.suggest_reservation(store, meeting)
    print(f"  预约时段：{reservation}　{note}")

    print("→ query-meal-assistance 查助餐服务 ...")
    assist = catalog.query_assistance(client, store, reservation)
    best = catalog.pick_assistance(assist)
    for a in assist:
        flag = "可用" if a.enabled else f"不可用（{a.reason}）"
        print(f"  · {a.name}：{flag}")
    if best:
        print(f"  已选：{best.name}")
    hints = catalog.lead_time_hint(assist)

    print("→ query-meals 拉菜单 ...")
    items, cats = catalog.fetch_menu(client, store)
    print(f"  分类 {len(cats)} 个，餐品 {len(items)} 项")

    print("→ list-nutrition-foods 拉营养 ...")
    table = catalog.fetch_nutrition(client)
    hit = catalog.attach_nutrition(items, table)
    print(f"  营养表 {len(table)} 条，匹配 {hit} 项")

    extra = {
        "assistance": best.name if best else "",
        "assistance_code": best.code if best else "",
        "assistance_all": [
            {"name": a.name, "enabled": a.enabled, "reason": a.reason, "items": a.items} for a in assist
        ],
        "lead_time_hints": hints,
        "reservation": reservation,
        "reservation_note": note,
        "period": period,
        "categories": cats,
        "nutrition_hit": hit,
        "store_obj": store,
    }
    return items, {"name": store.name, "mock": False, "storeCode": store.storeCode}, extra


# ------------------------------------------------------------------ 子命令

def cmd_probe(args) -> int:
    client = McdMcpClient()
    info = client.initialize()
    print("✓ 握手成功")
    if info:
        print(f"  server: {info.get('serverInfo', {})}")
    tools = client.list_tools()
    print(f"✓ 可用工具 {len(tools)} 个")
    for t in tools:
        print(f"  - {t.get('name'):<30} {(t.get('description') or '').splitlines()[0][:56]}")
    return 0


def _probe_unavailable(
    client: McdMcpClient, store: Any, combo: list[dict[str, Any]], extra: dict[str, Any]
) -> list[str]:
    """逐个单品试探，找出 calculate-price 报「售罄/不可售」的餐品编码。

    菜单接口会返回门店当前不可售的品项，只有真正计价时才会暴露，
    且错误信息不带商品编码，所以只能单项试探。组合最多 3 项，开销可控。
    """
    bad: list[str] = []
    for c in combo:
        try:
            catalog.price_combo(
                client,
                store,
                [c],
                1,
                extra.get("assistance_code") or None,
                extra.get("reservation") or None,
            )
        except McpError as exc:
            msg = str(exc)
            if "售罄" in msg or "60006" in msg:
                bad.append(c["code"])
    return bad


def cmd_plan(args) -> int:
    meeting = parse_meeting(args.start)
    params = planner.MeetingParams(
        people=args.people,
        budget=args.budget,
        start=meeting.strftime("%Y-%m-%d %H:%M"),
        duration=args.duration,
        avoid=[w for w in (args.avoid or "").replace("，", ",").split(",") if w.strip()],
        need_coffee=args.need_coffee,
        max_kcal=args.max_kcal,
        address=args.address or "",
    )

    client: McdMcpClient | None = None
    if args.mock:
        print("（mock 模式：使用 examples/mock_menu.json 静态数据）")
        items, store, extra = _mock_data()
    else:
        client = McdMcpClient()
        items, store, extra = _live_data(client, args, meeting)

    if extra.get("period"):
        params.window_hint = extra["period"]

    # 求解 + 官方计价。菜单里会混入当前不可售的品项，calculate-price 遇到就整单
    # 报 60006。逐个试探定位罪魁，剔除后重新求解，而不是粗暴地砍掉主食。
    banned: set[str] = set()
    priced: dict[str, Any] | None = None
    result: dict[str, Any] = {}
    for attempt in range(6):
        pool = [i for i in items if i.code not in banned]
        result = planner.plan(pool, params)
        if client is None:
            break
        try:
            priced = catalog.price_combo(
                client,
                extra["store_obj"],
                result["combo"],
                params.people,
                extra.get("assistance_code") or None,
                extra.get("reservation") or None,
            )
            break
        except McpError as exc:
            msg = str(exc)
            if "售罄" not in msg and "60006" not in msg:
                raise
            bad = _probe_unavailable(client, extra["store_obj"], result["combo"], extra)
            if not bad:
                bad = [c["code"] for c in result["combo"] if c.get("category") in ("main", "combo")][:1]
            if not bad:
                raise
            banned.update(bad)
            print(f"  有 {len(bad)} 项餐品不可售，已剔除后重新求解（第 {attempt + 2} 次）...")
    else:
        alive_mains = [
            i for i in items
            if i.code not in banned and i.category in ("main", "combo")
        ]
        if not alive_mains:
            raise McpError(
                f"该门店在「{extra.get('reservation_note') or '当前时段'}」下主食全部不可售——"
                "菜单接口返回的是全量菜单，只有计价时才知道哪些能卖。"
                "请换到午餐/晚餐时段，或改用 --be-type 2 走麦乐送"
            )
        raise McpError("多次重试后仍无法得到可下单的组合，请换门店或改会议时间")

    result["need_coffee"] = params.need_coffee
    result["meeting_time"] = meeting.strftime("%Y-%m-%d %H:%M")
    result["assistance"] = extra.get("assistance", "")
    result["reservation"] = extra.get("reservation", "")
    result["reservation_note"] = extra.get("reservation_note", "")
    result["coupons"] = []
    result["discount"] = 0.0
    result["promotion"] = ""

    if banned:
        result["notes"].append(f"已剔除 {len(banned)} 项当前不可售的餐品")

    if not args.mock and not catalog.premium_service_available(meeting):
        result["notes"].append(
            "距会议不足 4 小时，专人分餐/摆台已不可预约；提前 4 小时以上下单可解锁摆台服务"
        )

    if priced:
        result["subtotal"] = priced["original"] or result["subtotal"]
        result["discount"] = priced["discount"]
        result["promotion"] = priced["promotion"]
        result["payable"] = priced["payable"]
        result["priced"] = priced
        if priced["promotion"]:
            result["coupons"] = [
                {
                    "name": priced["promotion"],
                    "amount": priced["discount"],
                    "note": f"官方计价自动命中，实付 ¥{priced['payable']:.2f}",
                }
            ]
        print(
            f"  原价 ¥{priced['original']:.2f} → 实付 ¥{priced['payable']:.2f}"
            f"（省 ¥{priced['discount']:.2f}）"
        )

        # 助餐服务是「服务」与「折扣」的权衡：专人摆台通常没有 78 折。
        # 多花一次计价，把价差明确告诉用户。
        code = extra.get("assistance_code") or ""
        if code and code != "GMS001":
            try:
                alt = catalog.price_combo(
                    client,
                    extra["store_obj"],
                    result["combo"],
                    params.people,
                    "GMS001",
                    extra.get("reservation") or None,
                )
            except McpError:
                alt = None
            if alt and alt["payable"] + 0.01 < priced["payable"]:
                diff = priced["payable"] - alt["payable"]
                label = alt["promotion"] or "保鲜速达价"
                result["notes"].append(
                    f"改用「保鲜速达」实付 ¥{alt['payable']:.2f}（{label}），可省 ¥{diff:.2f}，但没有摆台服务"
                )
                print(f"  对比：改用保鲜速达 ¥{alt['payable']:.2f}，可省 ¥{diff:.2f}")

    out_xlsx = Path(args.out or "点餐方案.xlsx")
    report.build(result, store, out_xlsx)

    plan_json = out_xlsx.with_suffix(".json")
    plan_json.write_text(
        json.dumps(
            {
                "params": params.__dict__,
                "result": result,
                "store": store,
                "extra": {k: v for k, v in extra.items() if k != "store_obj"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # ---------------------------------------------------------- 终端摘要
    print()
    print(f"■ {result['window_label']} · {params.people} 人 · 人均预算 ¥{params.budget:.0f}")
    print(f"  门店：{store.get('name', '—')}")
    if result.get("reservation"):
        print(f"  预约：{result['reservation']}")
    if result.get("assistance"):
        print(f"  助餐：{result['assistance']}")
    print("  每人一份：")
    for c in result["combo"]:
        if c.get("kcal"):
            mark = "≈" if c.get("estimated") else ""
            kcal = f"  {mark}{c['kcal']:.0f} kcal"
        else:
            kcal = ""
        print(f"    · {c['name']:<18} ¥{c['price']:.1f}{kcal}")
    print(f"  人均 ¥{result['per_person_price']:.1f} / {result['per_person_kcal']:.0f} kcal")
    payable = result.get("payable", result["subtotal"])
    print(f"  合计 ¥{payable:.2f}（预算 ¥{result['budget_total']:.2f}）")
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
    extra = data.get("extra") or {}

    people = result["people"]
    print(f"门店：{store.get('name')}")
    for c in result["combo"]:
        print(f"  {c['name']} × {people}")
    print(f"应付：¥{result.get('payable', result['subtotal']):.2f}")

    if not args.confirm:
        print("\n（预演模式，未产生真实订单。确认无误后加 --confirm 再执行）")
        return 0

    store_obj = extra.get("store_obj") or {}
    client = McdMcpClient()
    payload: dict[str, Any] = {
        "storeCode": store_obj.get("storeCode") or store.get("storeCode"),
        "beCode": store_obj.get("beCode"),
        "beType": store_obj.get("beType", 6),
        "orderType": store_obj.get("orderType", 2),
        "items": [{"productCode": c["code"], "quantity": people} for c in result["combo"]],
    }
    if extra.get("assistance_code"):
        payload["gmServiceCode"] = extra["assistance_code"]
    if result.get("reservation"):
        payload["reservationDate"] = result["reservation"]

    order = client.call("create-order", payload)
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
    pp.add_argument("--start", default="", help="会议时间，如 '2026-10-24 17:00'")
    pp.add_argument("--duration", type=int, default=120, help="会议时长（分钟）")
    pp.add_argument("--address", default="", help="用于匹配已保存的配送地址（留空取第一条）")
    pp.add_argument("--avoid", default="", help="忌口/过敏原，逗号分隔")
    pp.add_argument("--need-coffee", action="store_true", help="需要咖啡")
    pp.add_argument("--max-kcal", type=float, default=None, help="人均热量上限")
    pp.add_argument("--be-type", type=int, default=6, help="6=企业团餐（默认），2=麦乐送")
    pp.add_argument("--mock", action="store_true", help="使用本地静态数据")
    pp.add_argument("--out", default="点餐方案.xlsx", help="Excel 输出路径")
    pp.set_defaults(func=cmd_plan)

    po = sub.add_parser("order", help="按方案下单")
    po.add_argument("--plan", required=True, help="plan 生成的 json 文件")
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
    except (ValueError, RuntimeError) as exc:
        print(f"✗ 无法生成方案：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
