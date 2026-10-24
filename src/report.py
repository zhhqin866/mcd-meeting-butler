"""Excel 报表生成：报销要凭据，分摊要明细，所以最终交付物是表格而不是一段话。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEAD_FILL = PatternFill("solid", fgColor="DA291C")   # 麦记红
HEAD_FONT = Font(color="FFFFFF", bold=True, size=11)
TITLE_FONT = Font(bold=True, size=14)
NOTE_FONT = Font(color="7F7F7F", size=9, italic=True)
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

CATEGORY_LABEL = {
    "main": "主食",
    "combo": "套餐",
    "side": "小食",
    "drink": "饮品",
    "coffee": "咖啡",
    "other": "其他",
}


def _sheet(wb: Workbook, title: str):
    ws = wb.create_sheet(title)
    ws.sheet_view.showGridLines = False
    return ws


def _header(ws, row: int, headers: list[str], widths: list[int]) -> None:
    for idx, (h, w) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=row, column=idx, value=h)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = CENTER
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.row_dimensions[row].height = 22


def _title(ws, text: str, span: int, subtitle: str = "") -> int:
    ws.cell(row=1, column=1, value=text).font = TITLE_FONT
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    row = 2
    if subtitle:
        ws.cell(row=2, column=1, value=subtitle).font = NOTE_FONT
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=span)
        row = 3
    ws.row_dimensions[1].height = 26
    return row + 1


def build(result: dict[str, Any], store: dict[str, Any], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    wb = Workbook()
    wb.remove(wb.active)

    people = result["people"]
    combo = result["combo"]
    subtotal = result["subtotal"]
    discount = float(result.get("discount") or 0.0)
    payable = round(subtotal - discount, 2)
    per_head = round(payable / people, 2) if people else 0.0

    # ---------------------------------------------------------- Sheet 1 点餐总表
    ws = _sheet(wb, "点餐总表")
    row = _title(
        ws,
        f"麦会点 · 会议点餐单（{people} 人）",
        6,
        f"门店：{store.get('name', '待定')}　时段：{result['window_label']}　"
        f"人均预算：¥{result['budget_per_person']:.1f}",
    )
    _header(ws, row, ["序号", "餐品", "类别", "单价(元)", "份数", "小计(元)"], [8, 32, 10, 12, 8, 12])
    row += 1
    for i, item in enumerate(combo, start=1):
        values = [
            i,
            item["name"],
            CATEGORY_LABEL.get(item["category"], item["category"]),
            round(item["price"], 2),
            people,
            round(item["price"] * people, 2),
        ]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=v)
            cell.border = BORDER
            cell.alignment = LEFT if col == 2 else CENTER
        row += 1

    for label, value, bold in (
        ("合计（未用券）", round(subtotal, 2), False),
        ("优惠减免", -round(discount, 2), False),
        ("应付总额", payable, True),
    ):
        ws.cell(row=row, column=5, value=label).alignment = Alignment(horizontal="right")
        cell = ws.cell(row=row, column=6, value=value)
        cell.number_format = "0.00"
        cell.alignment = CENTER
        cell.border = BORDER
        if bold:
            cell.font = Font(bold=True)
            ws.cell(row=row, column=5).font = Font(bold=True)
        row += 1

    row += 1
    for note in result.get("notes", []):
        ws.cell(row=row, column=1, value=f"· {note}").font = NOTE_FONT
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        row += 1

    # ---------------------------------------------------------- Sheet 2 分摊账单
    ws2 = _sheet(wb, "分摊账单")
    row = _title(ws2, "AA 分摊明细", 4, "金额已四舍五入到分，尾差计入最后一位")
    _header(ws2, row, ["序号", "参与人", "应分摊(元)", "备注"], [8, 20, 14, 24])
    row += 1

    base = int(per_head * 100)
    remainder = int(round(payable * 100)) - base * people
    for i in range(people):
        cents = base + (1 if i >= people - remainder else 0)
        values = [i + 1, f"参会人 {i + 1:02d}", round(cents / 100, 2), "含咖啡" if result.get("need_coffee") else ""]
        for col, v in enumerate(values, start=1):
            cell = ws2.cell(row=row, column=col, value=v)
            cell.border = BORDER
            cell.alignment = CENTER if col != 4 else LEFT
            if col == 3:
                cell.number_format = "0.00"
        row += 1

    ws2.cell(row=row, column=2, value="合计").font = Font(bold=True)
    total_cell = ws2.cell(row=row, column=3, value=payable)
    total_cell.font = Font(bold=True)
    total_cell.number_format = "0.00"
    total_cell.alignment = CENTER
    total_cell.border = BORDER

    # ---------------------------------------------------------- Sheet 3 营养汇总
    ws3 = _sheet(wb, "营养汇总")
    row = _title(
        ws3,
        "营养汇总",
        4,
        f"该时段建议人均 {result['kcal_range'][0]:.0f}–{result['kcal_range'][1]:.0f} kcal（仅供搭配参考，不构成营养建议）",
    )
    _header(ws3, row, ["指标", "人均", "全员合计", "评价"], [18, 14, 16, 28])
    row += 1
    kcal = result.get("per_person_kcal", 0)
    lo, hi = result["kcal_range"]
    verdict = "偏高" if kcal > hi else ("偏低" if kcal < lo else "合适")
    rows = [
        ("热量 (kcal)", kcal, round(kcal * people, 1), verdict),
        ("价格 (元)", result["per_person_price"], round(subtotal, 2),
         "超预算" if not result.get("within_budget", True) else "在预算内"),
    ]
    for values in rows:
        for col, v in enumerate(values, start=1):
            cell = ws3.cell(row=row, column=col, value=v)
            cell.border = BORDER
            cell.alignment = LEFT if col in (1, 4) else CENTER
        row += 1

    # ---------------------------------------------------------- Sheet 4 省钱明细
    ws4 = _sheet(wb, "省钱明细")
    row = _title(ws4, "优惠券与省钱明细", 3, "优惠以 calculate-price 官方计价结果为准")
    _header(ws4, row, ["项目", "金额(元)", "说明"], [26, 14, 40])
    row += 1
    coupons = result.get("coupons") or []
    for c in coupons:
        values = [c.get("name", "优惠券"), -round(float(c.get("amount", 0)), 2), c.get("note", "")]
        for col, v in enumerate(values, start=1):
            cell = ws4.cell(row=row, column=col, value=v)
            cell.border = BORDER
            cell.alignment = LEFT if col != 2 else CENTER
            if col == 2:
                cell.number_format = "0.00"
        row += 1
    if not coupons:
        ws4.cell(row=row, column=1, value="本单未匹配到可用券").font = NOTE_FONT
        row += 1

    values = ["实付总额", payable, f"较原价省 ¥{discount:.2f}" if discount else "无优惠"]
    for col, v in enumerate(values, start=1):
        cell = ws4.cell(row=row, column=col, value=v)
        cell.font = Font(bold=True)
        cell.border = BORDER
        cell.alignment = LEFT if col != 2 else CENTER
        if col == 2:
            cell.number_format = "0.00"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
