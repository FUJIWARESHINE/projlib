# -*- coding: utf-8 -*-
"""项目库导入器：解析「A-加急-返修-库存 信息更新.xlsx」的三张 sheet。

源表结构（三张表一致）：
  第 1 行：表名（合并标题）
  第 3-4 行：两级表头
    A 项目编号 | B 项目名 | C-G 平台组 | H-I 审核组 | J 发送日期
    C 细胞 / D 分子 / E 病理 / F 动物 / G 报价(加急、库存) 或 返修要求(返修)
    H 孙 / I 徐
  第 5 行起：数据
"""
from __future__ import annotations

import re

from openpyxl import load_workbook

import db

# sheet 名 -> 项目类型（前缀匹配，容错空格）
SHEET_TYPE_RULES = [
    ("真实", "real"),
    ("加急", "urgent"),
    ("返修", "repair"),
    ("库存", "stock"),
]

PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)")


def normalize_status(v, code: str = "") -> str:
    """把单元格值归一化成平台状态：/ ✅ ❌ 或空串。

    兼容「参与平台」式写法：单元格里填的是项目编号本身（如 E4=A381），
    表示该项目在该平台参与 → 视为已完成。
    """
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    if s in ("/", "／"):
        return db.ST_NONE
    if s in ("✅", "√", "✓", "是", "完成", "已完成", "ok", "OK"):
        return db.ST_DONE
    if s in ("❌", "×", "x", "X", "否", "未完成"):
        return db.ST_UNDONE
    # 单元格填的是本项目编号 → 表示在该平台参与
    if code and s.upper() == code.upper():
        return db.ST_DONE
    # 其它文本尽量保留原值（如 "已完成（12.3）"）
    if "✅" in s or "已完成" in s:
        return db.ST_DONE
    if "❌" in s or "未完成" in s:
        return db.ST_UNDONE
    return s


def parse_price(extra) -> float:
    """从 G 列文本/数字里解析报价金额；解析不出返回 0。"""
    if extra is None:
        return 0.0
    if isinstance(extra, (int, float)):
        return float(extra)
    s = str(extra).strip()
    if not s:
        return 0.0
    # 多个数字时取总和（如 "500+800"），区间取平均
    nums = [float(x) for x in PRICE_RE.findall(s)]
    if not nums:
        return 0.0
    if re.search(r"[-~～—]", s) and len(nums) >= 2:
        return round((nums[0] + nums[1]) / 2, 2)
    if re.search(r"\+", s):
        return round(sum(nums), 2)
    return nums[0]


def _fmt_date(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    # datetime / date
    try:
        return v.strftime("%Y-%m-%d")
    except Exception:
        return str(v)


# 日期形如 2026.1.16 / 2026-01-16 / 2026/1/16 / 2026年1月16日，或纯数字年份
DATE_RE = re.compile(r"^\s*\d{4}\s*[.\-/年]\s*\d{1,2}")


def split_date_note(v) -> tuple:
    """拆分「发送日期」列：是日期就返回 (日期, 空)，否则视为备注 (空, 备注)。

    源表里 J 列偶尔被填成备注（如「暂停」「加加急」「留实物+实验记录本」），
    这里自动归位到 note，避免备注混进发送日期。
    """
    if v is None:
        return "", ""
    if not isinstance(v, str):
        # datetime/date 对象 → 一定是日期
        d = _fmt_date(v)
        return (d, "") if d else ("", "")
    s = v.strip()
    if not s:
        return "", ""
    if DATE_RE.match(s):
        return s, ""
    return "", s


def _type_of_sheet(title: str) -> str:
    t = (title or "").strip()
    for prefix, key in SHEET_TYPE_RULES:
        if t.startswith(prefix):
            return key
    return ""


def parse_workbook(path: str) -> dict:
    """解析 xlsx，返回 {ptype: [row, ...]} 与识别到的 sheet 摘要。"""
    wb = load_workbook(path, data_only=True)
    result = {}
    sheets = []
    for ws in wb.worksheets:
        ptype = _type_of_sheet(ws.title)
        if not ptype:
            sheets.append({"sheet": ws.title, "type": "", "rows": 0, "skipped": True})
            continue
        rows = []
        for r in range(5, ws.max_row + 1):
            code = ws.cell(row=r, column=1).value
            name = ws.cell(row=r, column=2).value
            code = "" if code is None else str(code).strip()
            name = "" if name is None else str(name).strip()
            if not code and not name:
                continue
            # 编号为空但名称有内容的行：跳过（无编号无法按编号导出）
            if not code:
                continue
            extra = ws.cell(row=r, column=7).value
            send_date, date_note = split_date_note(ws.cell(row=r, column=10).value)
            rows.append({
                "ptype": ptype,
                "code": code,
                "name": name,
                "src_row": r,
                "cell": normalize_status(ws.cell(row=r, column=3).value, code),
                "mol": normalize_status(ws.cell(row=r, column=4).value, code),
                "path": normalize_status(ws.cell(row=r, column=5).value, code),
                "animal": normalize_status(ws.cell(row=r, column=6).value, code),
                "extra": "" if extra is None else str(extra).strip(),
                "price": parse_price(extra) if ptype != "repair" else 0.0,
                "audit_sun": normalize_status(ws.cell(row=r, column=8).value),
                "audit_xu": normalize_status(ws.cell(row=r, column=9).value),
                "send_date": send_date,
                # 源表把备注写进了日期列时，自动归位到 note
                "note": date_note,
            })
        result[ptype] = rows
        sheets.append({"sheet": ws.title, "type": ptype, "rows": len(rows), "skipped": False})
    return {"data": result, "sheets": sheets}


def import_workbook(path: str, mode: str = "upsert", source_label: str = "") -> dict:
    """导入 xlsx 到数据库。

    mode = "upsert"（默认，按 类型+编号 覆盖更新）
    mode = "rebuild"（先清空再全量写入）
    """
    parsed = parse_workbook(path)
    data = parsed["data"]
    if not data:
        return {"ok": False, "error": "未识别到任何 sheet（应包含 真实 / 加急 / 返修 / 库存 类）"}

    created = updated = 0
    skipped = {}
    if mode == "rebuild":
        with db.tx() as conn:
            conn.execute("DELETE FROM projects")
    batch = []
    for ptype, rows in data.items():
        seen = set()
        for row in rows:
            key = (row["code"], (row.get("extra") or "").strip())
            if key in seen:
                # 同类型 + 同编号 + 同内容：视为重复行，跳过（不重复入库）
                skipped[row["code"]] = skipped.get(row["code"], 0) + 1
                continue
            seen.add(key)
            batch.append(dict(row))
    # 单事务批量写入（218 条也只需一次连接/提交，避免逐条 fsync 拖慢到几十秒）
    created, updated = db.upsert_projects_bulk(batch)
    db.set_meta("source_file", source_label or path)
    db.set_meta("last_import", __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "sheets": parsed["sheets"],
        "by_type": {k: len(v) for k, v in data.items()},
    }
