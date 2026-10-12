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
    # 按「行」计数：同一编号返修多次会列出多行，合计要和列出来的行数对得上
    n_total = len(rows)
    n_real = sum(1 for r in rows if r.get("ptype") == "real")
    n_pack = sum(1 for r in rows if r.get("ptype") in ("urgent", "stock"))
    n_repair = sum(1 for r in rows if r.get("ptype") == "repair")
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

    n_total = len(rows)   # 按行计数，与下面列出的行数一致（同编号多次会列多行）
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
# 一级表头底色：用户 2026-10-12 指定 —— 第 3 行整行统一用「项目名」列的颜色 + 白字
M_HEAD = "2E75B6"
M_TITLE = "17375E"
# 副标题（第 2 行）：用户指定 黄底 + 红字
M_SUB_FILL = "FFD966"
M_SUB_TEXT = "C00000"
# 四平台色：**只用于数据区「√」的字色**（表头两行已统一成 M_HEAD，不要再拿去铺表头）
M_PLAT_COLORS = ["1D9E75", "378ADD", "BA7517", "7F77DD"]   # 细胞/分子/病理/动物
M_TYPE_TAG = {              # 类型标签底色
    "real": "EAF3DE", "urgent": "FAEEDA", "stock": "E6F1FB", "repair": "FCEBEB",
}
M_TYPE_TEXT = {"real": "3B6D11", "urgent": "854F0B", "stock": "185FA5", "repair": "A32D2D"}
# 数据区降噪（2026-10-12）：参与情况的「√」只上平台字色、不铺底色；
# 「未完成 / 库中未找到」只上字色、不铺底色（仅「已完成」保留浅绿底）。
# A 列大栏名（竖向四大模块）
M_BLOCK_LABELS = {"real": "真实", "urgent": "加急", "stock": "库存", "repair": "返修"}

M_WIDTHS = [7, 15, 42, 12, 10, 10, 10, 10, 13]


def _code_sort_key(code: str):
    """编号排序：首字母 → 长度 → 字典序（与项目库列表的默认排序一致）。"""
    c = str(code or "").upper()
    return (c[:1], len(c), c)


def _month_done(lib_row, plats: list) -> str:
    """月末表的「完成情况」：按**项目库**判定（与项目库列表口径一致）。

    已设计的平台（值 ≠ "/"）全部 ✅ → 已完成；有一个未完成（含空白=已设计未完成）→ 未完成；
    项目库里根本没有这个 (类型, 编号) → 库中未找到。
    """
    if not lib_row:
        return "库中未找到"
    return _overall_done(lib_row, plats)


def _head_cell(cell, color: str) -> None:
    cell.font = Font(name=MONTH_FONT, size=10, bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor=color)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def export_month_stat(detail: dict) -> str:
    """导出月末项目统计表。

    版式（竖向四大模块 + 横向平台/完成情况）：
      A 大栏（真实 / 加急 / 库存 / 返修，纵向合并）
      B 项目编号 ｜ C 项目名 ｜ D 报价
      E-H 参与情况（细胞 / 分子 / 病理 / 动物：本月哪个平台填了这个编号就打 √）
      I 完成情况（按项目库判定：该项目所有已设计平台都是 ✅ → 已完成，否则未完成）

    同一编号被多个平台填报时只占一行，√ 落在各自平台列下。
    """
    month = detail.get("month") or {}
    platforms = detail.get("platforms") or []
    plats = [(p.get("key") or "", p.get("label") or "") for p in platforms]
    mname = month.get("name") or "月末项目统计"

    # ---- 各平台填报按 (类型, 编号) 合并成一行
    merged, order = {}, []
    for p in platforms:
        pkey = p.get("key") or ""
        for it in p.get("items") or []:
            key = ((it.get("ptype") or ""), str(it.get("code") or "").upper())
            r = merged.get(key)
            if r is None:
                r = {"ptype": key[0], "code": it.get("code") or "",
                     "name": it.get("name") or "", "price": it.get("price") or 0,
                     "matched": bool(it.get("matched")), "plats": []}
                merged[key] = r
                order.append(key)
            elif it.get("matched") and not r["matched"]:
                # 同编号被多个平台填报：名称 / 报价取匹配到项目库的那一份
                r["name"] = it.get("name") or ""
                r["price"] = it.get("price") or 0
                r["matched"] = True
            if pkey and pkey not in r["plats"]:
                r["plats"].append(pkey)

    # ---- 项目库索引（完成情况要看平台状态列）
    lib = {}
    for row in db.all_projects():
        lib.setdefault((row["ptype"], str(row["code"]).upper()), row)
    for key in order:
        r = merged[key]
        r["lib"] = lib.get(key)
        r["done"] = _month_done(r["lib"], plats)

    # ---- 建表
    wb = Workbook()
    ws = wb.active
    ws.title = "月末统计"
    ncols = 4 + len(plats) + 1        # A 大栏 + 编号/项目名/报价 + 平台列 + 完成情况
    widths = ([9, 15, 47, 13] + [11] * len(plats) + [14])
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    _setup_page(ws)
    last_col = get_column_letter(ncols)

    n_total = len(order)             # 项目数（同一编号多平台填报只算一个）
    n_done = sum(1 for k in order if merged[k]["done"] == "已完成")
    n_undone = sum(1 for k in order if merged[k]["done"] == "未完成")
    n_miss = sum(1 for k in order if merged[k]["done"] == "库中未找到")

    # ---- 标题 / 副标题（沿用原来的配色）
    ws.merge_cells(f"A1:{last_col}1")
    t = ws["A1"]
    t.value = f"{mname} · 项目参与与完成情况"
    t.font = Font(name=MONTH_FONT, size=16, bold=True, color="FFFFFF")
    t.fill = PatternFill("solid", fgColor=M_TITLE)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 40

    # 副标题（第 2 行）——用户 2026-10-12 指定格式：
    #   共 N 个项目    |    已完成 X · 未完成 Y    |    真实 a 项 · 加急 b 项 · 库存 c 项 · 返修 d 项
    # 只统计项目数（不写填报条数）、只写已完成/未完成、分类维度用四大类型（不再按平台）
    sub = f"共 {n_total} 个项目    |    已完成 {n_done} · 未完成 {n_undone}"
    if n_miss:
        sub += f" · 库中未找到 {n_miss}"
    type_counts = [
        f"{M_BLOCK_LABELS.get(ptype, ptype)} "
        f"{sum(1 for k in order if k[0] == ptype)} 项"
        for ptype, _plabel in db.TYPES
    ]
    if type_counts:
        sub += "    |    " + " · ".join(type_counts)
    ws.merge_cells(f"A2:{last_col}2")
    s = ws["A2"]
    s.value = sub
    s.font = Font(name=MONTH_FONT, size=10, color=M_SUB_TEXT)
    s.fill = PatternFill("solid", fgColor=M_SUB_FILL)
    s.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 24

    # ---- 两级表头（第 3-4 行）
    h1, h2 = 3, 4
    ws.merge_cells("A3:A4")
    ws.merge_cells("B3:B4")
    ws.merge_cells("C3:C4")
    ws.merge_cells("D3:D4")
    if plats:
        ws.merge_cells(start_row=h1, start_column=5, end_row=h1, end_column=4 + len(plats))
    ws.merge_cells(start_row=h1, start_column=ncols, end_row=h2, end_column=ncols)
    # 表头两行（第 3-4 行）整体统一底色（用户指定：用「项目名」列的颜色 + 白字）
    for col, val in ((1, ""), (2, "项目编号"), (3, "项目名"), (4, "报价"),
                     (5, "参与情况"), (ncols, "完成情况")):
        _head_cell(ws.cell(row=h1, column=col, value=val), M_HEAD)
    for i, (_k, lab) in enumerate(plats):
        _head_cell(ws.cell(row=h2, column=5 + i, value=lab), M_HEAD)
    for rr in (h1, h2):
        for col in range(1, ncols + 1):
            ws.cell(row=rr, column=col).border = BORDER
    ws.row_dimensions[h1].height = 26
    ws.row_dimensions[h2].height = 22

    # ---- 四大模块（真实 → 加急 → 库存 → 返修），模块名写 A 列并纵向合并
    row = h2 + 1
    wrote = False
    for ptype, _plabel in db.TYPES:
        grp = [merged[k] for k in order if k[0] == ptype]
        if not grp:
            continue
        grp.sort(key=lambda r: _code_sort_key(r["code"]))
        start = row
        for r in grp:
            ws.cell(row=row, column=2, value=r["code"])
            ws.cell(row=row, column=3,
                    value=(r["name"] if r["matched"] else "（项目库中没有这个编号）"))
            ws.cell(row=row, column=4, value=_price_cell(r["lib"] or {"ptype": ptype}))
            for i, (pkey, _lab) in enumerate(plats):
                if pkey in r["plats"]:
                    ws.cell(row=row, column=5 + i, value="√")
            ws.cell(row=row, column=ncols, value=r["done"])

            # 先铺默认样式，再逐个覆盖
            for col in range(1, ncols + 1):
                cell = ws.cell(row=row, column=col)
                cell.border = BORDER
                cell.font = Font(name=MONTH_FONT, size=10)
                cell.alignment = Alignment(
                    horizontal="left" if col == 3 else "center",
                    vertical="center", wrap_text=(col == 3))
            c2 = ws.cell(row=row, column=2)
            c2.font = Font(name=MONTH_FONT, size=10, bold=True)
            if not r["matched"]:
                c2.font = Font(name=MONTH_FONT, size=10, bold=True, color="DC2626")
                ws.cell(row=row, column=3).font = Font(
                    name=MONTH_FONT, size=10, color="DC2626")
            if isinstance(ws.cell(row=row, column=4).value, (int, float)):
                ws.cell(row=row, column=4).number_format = "#,##0"
            for i, (pkey, _lab) in enumerate(plats):
                if pkey in r["plats"]:
                    cc = ws.cell(row=row, column=5 + i)
                    cc.font = Font(name=MONTH_FONT, size=12, bold=True,
                                   color=M_PLAT_COLORS[i % len(M_PLAT_COLORS)])
            dc = ws.cell(row=row, column=ncols)
            if r["done"] == "已完成":
                dc.font = Font(name=MONTH_FONT, size=10, bold=True, color="16A34A")
                dc.fill = PatternFill("solid", fgColor="DCFCE7")
            elif r["done"] == "未完成":
                dc.font = Font(name=MONTH_FONT, size=10, color="000000")
            else:
                dc.font = Font(name=MONTH_FONT, size=10, color="D97706")
            row += 1

        if row - 1 > start:          # 只有一行时不合并（避免 A12:A12 这种空合并）
            ws.merge_cells(start_row=start, start_column=1, end_row=row - 1, end_column=1)
        gc = ws.cell(row=start, column=1, value=M_BLOCK_LABELS.get(ptype, ptype))
        gc.font = Font(name=MONTH_FONT, size=11, bold=True,
                       color=M_TYPE_TEXT.get(ptype, "000000"))
        gc.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for rr in range(start, row):
            a = ws.cell(row=rr, column=1)
            a.border = BORDER
            a.fill = PatternFill("solid", fgColor=M_TYPE_TAG.get(ptype, "FFFFFF"))
        wrote = True

    if not wrote:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        e = ws.cell(row=row, column=1, value="本月暂无填报数据")
        e.font = Font(name=MONTH_FONT, size=11, color="94A3B8")
        e.alignment = Alignment(horizontal="center", vertical="center")
        for col in range(1, ncols + 1):
            ws.cell(row=row, column=col).border = BORDER
        ws.row_dimensions[row].height = 26

    name = f"月末项目统计_{_safe_name(mname)}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    path = os.path.join(_export_dir(), name)
    wb.save(path)
    return path

