# -*- coding: utf-8 -*-
"""Excel 生成：按编号导出项目汇总、导出整库。"""
from __future__ import annotations

import os
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import db

# ------------------------------------------------------------------ 样式
FONT = "微软雅黑"
BLUE = "1F4E79"
BLUE_LIGHT = "D9E2F3"
GREY = "F2F2F2"
GREEN = "16A34A"
RED = "DC2626"
AMBER = "D97706"

THIN = Side(style="thin", color="9DA9BF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

_HEAD_FILL = PatternFill("solid", fgColor=BLUE)
_HEAD_FONT = Font(name=FONT, size=10, bold=True, color="FFFFFF")
_BODY_FONT = Font(name=FONT, size=10)
_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
_CENTER = Alignment(horizontal="center", vertical="center")
_RIGHT = Alignment(horizontal="right", vertical="center")


def _setup_page(ws) -> None:
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = 9  # A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.oddFooter.center.text = "第 &P 页 / 共 &N 页"
    ws.oddFooter.center.size = 9
    ws.oddFooter.center.font = "宋体"


def _safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", str(text or ""))[:40]


def _status_style(cell, value: str) -> None:
    v = (value or "").strip()
    if v == db.ST_DONE:
        cell.font = Font(name=FONT, size=10, bold=True, color=GREEN)
        cell.fill = PatternFill("solid", fgColor="DCFCE7")
    elif v == db.ST_UNDONE:
        cell.font = Font(name=FONT, size=10, bold=True, color=RED)
        cell.fill = PatternFill("solid", fgColor="FEE2E2")
    elif v == db.ST_NONE:
        cell.font = Font(name=FONT, size=10, color="94A3B8")
        cell.fill = PatternFill("solid", fgColor="F1F5F9")
    else:
        cell.font = _BODY_FONT


# ------------------------------------------------------------------ 按编号导出（核心）
# 版式参照「技术部项目参与与完成情况」：
#   A 大栏（真实 / 压缩包 / 返修，纵向合并） | B 项目编号 | C 项目名 | D 报价
#   E-H 参与平台（细胞/分子/病理/动物，单元格内填该行编号表示参与） | I 完成情况
STAT_HEADERS = ["", "项目编号", "项目名", "报价", "参与平台", "", "", "", "完成情况"]
STAT_SUBHEADERS = ["", "", "", "", "细胞", "分子", "病理", "动物", ""]
STAT_WIDTHS = [10.5, 13.5, 47, 14, 10.5, 10.5, 10.5, 10.5, 12]

STAT_FONT = "宋体"
STAT_HEAD_FILL = PatternFill("solid", fgColor="FFFF00")   # 表头黄底
STAT_BODY_FONT = Font(name=STAT_FONT, size=11)
STAT_HEAD_FONT = Font(name="Times New Roman", size=11, bold=True)
STAT_TITLE_FONT = Font(name=STAT_FONT, size=11, bold=True)
STAT_MISSING_FONT = Font(name=STAT_FONT, size=11, color="FF0000")

# 完成情况判定
def _overall_done(r: dict, platforms: list) -> str:
    """项目整体是否完成。
    参与平台 = 所有「设计了」的平台（值 ≠ "/"，**含空白**）；
    空白 = 设计了但未完成 → 计入未完成；
    「/」= 未设计 → 不计入。
    全部参与平台 ✅ → 已完成；否则未完成。"""
    vals = [(r.get(k) or "").strip() for k, _ in platforms]
    vals = [v for v in vals if v != db.ST_NONE]   # 只排除「/」未设计
    if not vals:
        return "已完成"
    return "已完成" if all(v == db.ST_DONE for v in vals) else "未完成"


def _price_cell(r: dict):
    if r.get("ptype") == "repair":
        return "/"
    p = r.get("price") or 0
    return p if p else "/"


def export_summary(rows: list, missing: list = None, title: str = "",
                   platforms=None) -> str:
    """按编号导出统计表（版式对齐「技术部项目参与与完成情况」）。

    大栏顺序：真实 / 压缩包（加急+库存）/ 返修；
    编号顺序按传入 rows 的顺序分栏归组。
    """
    platforms = platforms or db.PLATFORMS
    missing = missing or []

    wb = Workbook()
    ws = wb.active
    ws.title = "项目统计"
    for i, w in enumerate(STAT_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    _setup_page(ws)

    # ---- 第 1-2 行：表头（含两级合并）
    ws.merge_cells("A1:A2")
    ws.merge_cells("B1:B2")
    ws.merge_cells("C1:C2")
    ws.merge_cells("D1:D2")
    ws.merge_cells("E1:H1")
    ws.merge_cells("I1:I2")
    head_cells = {
        "A1": "", "B1": "项目编号", "C1": "项目名", "D1": "报价",
        "E1": "参与平台", "E2": "细胞", "F2": "分子", "G2": "病理", "H2": "动物",
        "I1": "完成情况",
    }
    for ref, val in head_cells.items():
        cell = ws[ref]
        cell.value = val
        cell.font = STAT_HEAD_FONT
        cell.fill = STAT_HEAD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[1].height = 42
    ws.row_dimensions[2].height = 20

    # ---- 第 3 行：合计 + 说明
    n_total = len({(r.get("ptype"), r.get("code")) for r in rows})
    n_real = len({r["code"] for r in rows if r.get("ptype") == "real"})
    n_pack = len({r["code"] for r in rows if r.get("ptype") in ("urgent", "stock")})
    n_repair = len({r["code"] for r in rows if r.get("ptype") == "repair"})
    price_sum = round(sum((r.get("price") or 0) for r in rows if r.get("ptype") != "repair"), 2)
    note = (f"共参与项目 {n_total} 项，其中真实 {n_real} 项、压缩包 {n_pack} 项、返修 {n_repair} 项"
            + (f"，报价合计 {price_sum:,.0f} 元" if price_sum else ""))
    if missing:
        note += f"    ⚠ 未匹配编号：{'、'.join(missing)}"
    ws.merge_cells("B3:I3")
    ws["A3"] = "合计"
    ws["A3"].font = STAT_TITLE_FONT
    ws["A3"].alignment = Alignment(horizontal="center", vertical="center")
    ws["B3"] = note
    ws["B3"].font = STAT_MISSING_FONT if missing else Font(name=STAT_FONT, size=11, color="FF0000")
    ws["B3"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    for col in range(1, 10):
        ws.cell(row=3, column=col).border = BORDER
    ws.row_dimensions[3].height = 34

    # ---- 数据行：按大栏分组
    row = 4
    groups_written = []
    for gkey, glabel, members in db.EXPORT_GROUPS:
        grp_rows = [r for r in rows if r.get("ptype") in members]
        if not grp_rows:
            continue
        start = row
        for r in grp_rows:
            code = r.get("code") or ""
            ws.cell(row=row, column=2, value=code)
            ws.cell(row=row, column=3, value=r.get("name") or "")
            ws.cell(row=row, column=4, value=_price_cell(r))
            # 参与平台：只要「设计了」（值 ≠ "/"，含空白=已设计未完成）就写上编号
            for pi, (pkey, _plabel) in enumerate(platforms):
                pv = (r.get(pkey) or "").strip()
                if pv != db.ST_NONE:          # 排除「/」未设计
                    ws.cell(row=row, column=5 + pi, value=code)
            ws.cell(row=row, column=9, value=_overall_done(r, platforms))
            for col in range(1, 10):
                cell = ws.cell(row=row, column=col)
                cell.border = BORDER
                cell.font = STAT_BODY_FONT
                if col in (1, 2, 4, 5, 6, 7, 8, 9):
                    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            ws.row_dimensions[row].height = 30
            row += 1
        ws.merge_cells(start_row=start, start_column=1, end_row=row - 1, end_column=1)
        gc = ws.cell(row=start, column=1, value=glabel)
        gc.font = STAT_TITLE_FONT
        gc.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        gc.border = BORDER
        for rr in range(start, row):
            ws.cell(row=rr, column=1).border = BORDER
        groups_written.append(glabel)

    if not groups_written:
        ws.merge_cells("A4:I4")
        ws["A4"] = "没有匹配到任何项目编号，请检查编号或所选类型。"
        ws["A4"].font = Font(name=STAT_FONT, size=11, color="999999")
        ws["A4"].alignment = Alignment(horizontal="center", vertical="center")
        ws["A4"].border = BORDER

    save_dir = _export_dir()
    name = f"项目统计_{_safe_name(title)}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    path = os.path.join(save_dir, name)
    wb.save(path)
    return path


def _export_dir() -> str:
    settings = db.get_settings()
    d = (settings.get("export_dir") or "").strip()
    if not d:
        d = os.path.join(db.get_data_dir(), "导出")
    os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------------ 整库导出
FULL_HEADERS = [
    "项目类型", "项目编号", "项目名",
    "细胞", "分子", "病理", "动物",
    "报价 / 返修要求", "报价(元)", "孙审核", "徐审核", "发送日期", "备注",
]
FULL_WIDTHS = [11, 13, 46, 8, 8, 8, 8, 26, 12, 9, 9, 13, 22]


def export_all(ptype: str = "") -> str:
    rows = db.all_projects(ptype)
    wb = Workbook()
    ws = wb.active
    ws.title = "项目库"
    for i, w in enumerate(FULL_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    last = get_column_letter(len(FULL_HEADERS))
    _setup_page(ws)
    for idx, name in enumerate(FULL_HEADERS, start=1):
        cell = ws.cell(row=1, column=idx, value=name)
        cell.font = _HEAD_FONT
        cell.fill = _HEAD_FILL
        cell.alignment = _CENTER
        cell.border = BORDER
    ws.row_dimensions[1].height = 26
    ws.freeze_panes = "A2"
    for i, r in enumerate(rows, start=1):
        row = i + 1
        values = [
            db.TYPE_NAME.get(r.get("ptype"), ""),
            r.get("code") or "", r.get("name") or "",
            r.get("cell") or "", r.get("mol") or "",
            r.get("path") or "", r.get("animal") or "",
            r.get("extra") or "",
            (r.get("price") or 0) if r.get("ptype") != "repair" else "",
            r.get("audit_sun") or "", r.get("audit_xu") or "",
            r.get("send_date") or "", r.get("note") or "",
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.font = _BODY_FONT
            cell.border = BORDER
            if col in (4, 5, 6, 7):
                cell.alignment = _CENTER
                _status_style(cell, value)
            elif col in (1, 2, 10, 11, 12):
                cell.alignment = _CENTER
            elif col == 9:
                cell.alignment = _RIGHT
                cell.number_format = "#,##0.00"
            else:
                cell.alignment = _LEFT
        ws.row_dimensions[row].height = 20
    suffix = f"_{db.TYPE_NAME[ptype]}" if ptype in db.TYPE_NAME else ""
    path = os.path.join(_export_dir(), f"项目库导出{suffix}_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    wb.save(path)
    return path


# ------------------------------------------------------------------ 明细报告
def _write_stat_sheet(ws, rows: list, missing: list, title: str, platforms) -> None:
    """把一批记录按「技术部项目参与与完成情况」版式写进一个 sheet。"""
    for i, w in enumerate(STAT_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    _setup_page(ws)
    ws.merge_cells("A1:A2"); ws.merge_cells("B1:B2"); ws.merge_cells("C1:C2")
    ws.merge_cells("D1:D2"); ws.merge_cells("E1:H1"); ws.merge_cells("I1:I2")
    head_cells = {
        "A1": "", "B1": "项目编号", "C1": "项目名", "D1": "报价",
        "E1": "参与平台", "E2": "细胞", "F2": "分子", "G2": "病理", "H2": "动物",
        "I1": "完成情况",
    }
    for ref, val in head_cells.items():
        cell = ws[ref]
        cell.value = val
        cell.font = STAT_HEAD_FONT
        cell.fill = STAT_HEAD_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[1].height = 42
    ws.row_dimensions[2].height = 20

    n_total = len({(r.get("ptype"), r.get("code")) for r in rows})
    price_sum = round(sum((r.get("price") or 0) for r in rows if r.get("ptype") != "repair"), 2)
    note = f"共 {n_total} 项" + (f"，报价合计 {price_sum:,.0f} 元" if price_sum else "")
    if missing:
        note += f"    ⚠ 未匹配编号：{'、'.join(missing)}"
    ws.merge_cells("B3:I3")
    ws["A3"] = "合计"
    ws["A3"].font = STAT_TITLE_FONT
    ws["A3"].alignment = Alignment(horizontal="center", vertical="center")
    ws["B3"] = note
    ws["B3"].font = Font(name=STAT_FONT, size=11, color="FF0000")
    ws["B3"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    for col in range(1, 10):
        ws.cell(row=3, column=col).border = BORDER
    ws.row_dimensions[3].height = 34

    row = 4
    wrote = False
    for _gkey, glabel, members in db.EXPORT_GROUPS:
        grp_rows = [r for r in rows if r.get("ptype") in members]
        if not grp_rows:
            continue
        start = row
        for r in grp_rows:
            code = r.get("code") or ""
            ws.cell(row=row, column=2, value=code)
            ws.cell(row=row, column=3, value=r.get("name") or "")
            ws.cell(row=row, column=4, value=_price_cell(r))
            for pi, (pkey, _pl) in enumerate(platforms):
                pv = (r.get(pkey) or "").strip()
                if pv != db.ST_NONE:      # 排除「/」未设计；空白=已设计未完成，要写
                    ws.cell(row=row, column=5 + pi, value=code)
            ws.cell(row=row, column=9, value=_overall_done(r, platforms))
            for col in range(1, 10):
                cell = ws.cell(row=row, column=col)
                cell.border = BORDER
                cell.font = STAT_BODY_FONT
                cell.alignment = Alignment(
                    horizontal="center" if col != 3 else "left",
                    vertical="center", wrap_text=True)
            ws.row_dimensions[row].height = 30
            row += 1
        ws.merge_cells(start_row=start, start_column=1, end_row=row - 1, end_column=1)
        gc = ws.cell(row=start, column=1, value=glabel)
        gc.font = STAT_TITLE_FONT
        gc.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for rr in range(start, row):
            ws.cell(row=rr, column=1).border = BORDER
        wrote = True
    if not wrote:
        ws.merge_cells("A4:I4")
        ws["A4"] = "没有匹配到任何项目编号。"
        ws["A4"].font = Font(name=STAT_FONT, size=11, color="999999")
        ws["A4"].alignment = Alignment(horizontal="center", vertical="center")


def export_lookup_report(rows_by_type: dict, missing_by_type: dict, title: str = "") -> str:
    """多类型一次导出：整体一张统计表（真实 / 压缩包 / 返修）。"""
    all_rows = []
    for ptype, _label in db.TYPES:
        all_rows.extend(rows_by_type.get(ptype) or [])
    missing = []
    for ptype, _label in db.TYPES:
        missing.extend(missing_by_type.get(ptype) or [])

    wb = Workbook()
    ws = wb.active
    ws.title = "项目统计"
    _write_stat_sheet(ws, all_rows, missing, title, db.PLATFORMS)
    path = os.path.join(_export_dir(), f"项目统计_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    wb.save(path)
    return path


# ================================================================== 月末项目统计表
# 新版式：标题区 + 四平台分区（细胞 / 分子 / 病理 / 动物）
# 每个平台分区内按 类型（真实/加急/库存/返修）分组，列出当月完成的编号。
MONTH_FONT = "微软雅黑"
M_HEAD = "1F4E79"          # 深蓝表头
M_TITLE = "17375E"
M_PLAT_COLORS = ["1D9E75", "378ADD", "BA7517", "7F77DD"]   # 细胞/分子/病理/动物
M_TYPE_TAG = {              # 类型标签底色
    "real": "EAF3DE", "urgent": "FAEEDA", "stock": "E6F1FB", "repair": "FCEBEB",
}
M_TYPE_TEXT = {"real": "3B6D11", "urgent": "854F0B", "stock": "185FA5", "repair": "A32D2D"}

M_WIDTHS = [7, 15, 42, 12, 10, 10, 10, 10, 13]


def export_month_stat(detail: dict) -> str:
    """导出月末项目统计表（按月 + 四平台板块）。"""
    month = detail.get("month") or {}
    platforms = detail.get("platforms") or []
    mname = month.get("name") or "月末项目统计"

    wb = Workbook()
    ws = wb.active
    ws.title = "月末统计"
    for i, w in enumerate(M_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    _setup_page(ws)

    ncols = len(M_WIDTHS)
    last_col = get_column_letter(ncols)

    # ---- 标题
    ws.merge_cells(f"A1:{last_col}1")
    t = ws["A1"]
    t.value = f"{mname} · 项目完成情况统计"
    t.font = Font(name=MONTH_FONT, size=16, bold=True, color="FFFFFF")
    t.fill = PatternFill("solid", fgColor=M_TITLE)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 40

    # ---- 副标题（总计）
    total = detail.get("total") or 0
    parts = " · ".join(f"{p['label']} {p['count']} 项" for p in platforms)
    ws.merge_cells(f"A2:{last_col}2")
    s = ws["A2"]
    s.value = f"共完成 {total} 项    |    {parts}"
    s.font = Font(name=MONTH_FONT, size=10, color="FFFFFF")
    s.fill = PatternFill("solid", fgColor="2E5C8A")
    s.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 24

    row = 4
    for pi, plat in enumerate(platforms):
        color = M_PLAT_COLORS[pi % len(M_PLAT_COLORS)]
        # ---- 平台分区标题条
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        head = ws.cell(row=row, column=1)
        head.value = f"  {plat['label']}平台    （{plat['count']} 项）"
        head.font = Font(name=MONTH_FONT, size=12, bold=True, color="FFFFFF")
        head.fill = PatternFill("solid", fgColor=color)
        head.alignment = Alignment(horizontal="left", vertical="center")
        for c in range(1, ncols + 1):
            ws.cell(row=row, column=c).border = BORDER
            ws.cell(row=row, column=c).fill = PatternFill("solid", fgColor=color)
        ws.row_dimensions[row].height = 26
        row += 1

        items = plat.get("items") or []
        if not items:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
            e = ws.cell(row=row, column=1)
            e.value = "（该平台本月暂无填写）"
            e.font = Font(name=MONTH_FONT, size=10, color="94A3B8")
            e.alignment = Alignment(horizontal="center", vertical="center")
            for c in range(1, ncols + 1):
                ws.cell(row=row, column=c).border = BORDER
            ws.row_dimensions[row].height = 22
            row += 2
            continue

        # ---- 表头
        headers = ["序号", "项目编号", "项目名", "报价", "真实", "加急", "库存", "返修", "备注"]
        for ci, h in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=ci, value=h)
            cell.font = Font(name=MONTH_FONT, size=10, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=M_HEAD)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = BORDER
        ws.row_dimensions[row].height = 22
        row += 1

        # ---- 明细
        for idx, it in enumerate(items, start=1):
            vals = [idx, it.get("code") or "", it.get("name") or "",
                    (it.get("price") or 0) or "", "", "", "", "", ""]
            pt = it.get("ptype") or ""
            # 在对应类型列打「√」
            type_col = {"real": 5, "urgent": 6, "stock": 7, "repair": 8}.get(pt)
            if type_col:
                vals[type_col - 1] = "√"
            for ci, v in enumerate(vals, start=1):
                cell = ws.cell(row=row, column=ci, value=v)
                cell.font = Font(name=MONTH_FONT, size=10)
                cell.border = BORDER
                cell.alignment = Alignment(
                    horizontal="left" if ci == 3 else "center", vertical="center",
                    wrap_text=(ci == 3))
                if ci == 4 and v not in ("", None):
                    cell.number_format = "#,##0"
            # 类型列着色
            if type_col:
                tc = ws.cell(row=row, column=type_col)
                tc.font = Font(name=MONTH_FONT, size=10, bold=True,
                               color=M_TYPE_TEXT.get(pt, "000000"))
                tc.fill = PatternFill("solid", fgColor=M_TYPE_TAG.get(pt, "FFFFFF"))
            if not it.get("matched", True):
                ws.cell(row=row, column=2).font = Font(
                    name=MONTH_FONT, size=10, color="DC2626", bold=True)
                ws.cell(row=row, column=9, value="库中未找到该编号")
                ws.cell(row=row, column=9).font = Font(
                    name=MONTH_FONT, size=9, color="DC2626")
            ws.row_dimensions[row].height = 20
            row += 1
        row += 1   # 平台之间空一行

    # ---- 页脚说明
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    f = ws.cell(row=row, column=1)
    f.value = (f"导出时间：{datetime.now():%Y-%m-%d %H:%M}    "
               f"｜ 本表由「项目库统计程序」自动生成")
    f.font = Font(name=MONTH_FONT, size=9, color="5F5E5A")
    f.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[row].height = 20

    name = f"月末项目统计_{_safe_name(mname)}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    path = os.path.join(_export_dir(), name)
    wb.save(path)
    return path

