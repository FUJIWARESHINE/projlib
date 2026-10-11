# -*- coding: utf-8 -*-
"""HTTP 服务：静态资源 + REST API + 多用户鉴权。"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import auth
import db
import export_excel
import importer
import seed

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}

# 无需登录即可访问的接口
PUBLIC_ROUTES = {
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/register"),
    ("POST", "/api/auth/login"),
}

# 仅管理员可访问的接口
ADMIN_ROUTES = {
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PUT", "/api/users"),
    ("DELETE", "/api/users"),
    ("POST", "/api/projects"),
    ("PUT", "/api/projects"),
    ("DELETE", "/api/projects"),
    ("POST", "/api/projects/delete"),
    ("POST", "/api/projects/clear"),
    ("POST", "/api/import/preview"),
    ("POST", "/api/import/run"),
    ("POST", "/api/import/path"),
    ("POST", "/api/system/pick-file"),
    ("GET", "/api/export/all"),
}


def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, rel)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel)


def find_free_port(start: int = 8775, tries: int = 60) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("未找到可用端口")


IS_WINDOWS = os.name == "nt"


def open_path(path: str, select: bool = False) -> bool:
    """在服务端打开文件 / 目录。仅在 Windows 桌面模式下有效，
    容器（Linux 无桌面）下返回 False，由前端改用下载方式。"""
    if not IS_WINDOWS:
        return False
    path = os.path.abspath(path)
    try:
        if select:
            subprocess.Popen(["explorer", "/select,", path])
        else:
            os.startfile(path)
        return True
    except Exception:  # noqa: BLE001
        return False


def ok(data=None, **extra):
    payload = {"ok": True, "data": data}
    payload.update(extra)
    return payload


def fail(msg: str, code: int = 400):
    return {"ok": False, "error": msg}, code


def api_logout(_q, _b):
    return ok(True)


# ------------------------------------------------------------------ 工具
def _split_codes(text: str) -> list:
    """把粘贴的编号文本切成有序去重列表：支持换行/逗号/空格/分号分隔。"""
    parts = re.split(r"[\s,，;；、|]+", str(text or ""))
    seen, out = set(), []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


# ------------------------------------------------------------------ 业务
def api_auth_status(_q, _b):
    """无需登录：告知前端可选的注册岗位。
    ⚠️ 不回传任何账号信息（含内置管理员用户名），避免匿名泄露凭据线索。"""
    return ok({
        "setup_needed": False,
        "posts": [{"key": k, "label": v, "readonly": ro}
                  for k, v, ro in db.SIGNUP_OPTIONS],
    })


def api_auth_register(_q, body):
    """自助注册：一律普通用户（必须选岗位）。"""
    try:
        user = auth.register(
            username=body.get("username"), password=body.get("password"),
            display_name=body.get("display_name"),
            platform=(body.get("platform") or "").strip(),
        )
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))
    return ok({"user": user})


def api_auth_login(_q, body):
    try:
        user = auth.login(body.get("username"), body.get("password"))
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc), 401)
    return ok({"user": user, "token": auth.create_session(user["id"])})


def api_auth_logout(ctx, _q, _b):
    return ok(True)


def api_auth_me(_q, _b, ctx=None):
    user = (ctx or {}).get("user")
    if not user:
        return fail("未登录", 401)
    return ok({"user": user})


def api_dashboard(_q, _b):
    return ok(db.dashboard())


def api_types(_q, _b):
    return ok([{"key": k, "label": v} for k, v in db.TYPES])


def api_list_projects(query, _b):
    return ok(db.list_projects(
        ptype=query.get("ptype", [""])[0],
        search=query.get("search", [""])[0],
        platform=query.get("platform", [""])[0],
        status=query.get("status", [""])[0],
        page=int(query.get("page", [1])[0] or 1),
        page_size=int(query.get("page_size", [50])[0] or 50),
        order=query.get("order", ["asc"])[0] or "asc",
    ))


def api_get_project(_q, _b, pid):
    row = db.get_project(pid)
    if not row:
        return fail("项目不存在", 404)
    return ok(row)


def api_create_project(_q, body):
    try:
        return ok({"id": db.upsert_project(body)})
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


def api_update_project(_q, body, pid):
    db.update_project(pid, body)
    return ok(True)


def api_set_platform_status(_q, body, pid, ctx=None):
    """只改某个项目的**一个平台**状态（实验员日常填报用）。
    服务端强制校验：只能改自己岗位的平台；运营/设计只读；管理员全平台。
    只允许写 cell/mol/path/animal 四列，杜绝借此改其他字段。"""
    user = (ctx or {}).get("user") or {}
    plat = (body.get("platform") or "").strip()
    value = body.get("value")
    value = "" if value is None else str(value).strip()
    if plat not in ("cell", "mol", "path", "animal"):
        return fail("平台参数不合法", 400)
    if value not in ("", "/", "✅", "❌"):
        return fail("状态值不合法", 400)
    if not auth.can_edit_platform(user, plat):
        return fail("没有该平台的填报权限", 403)
    if not db.get_project(pid):
        return fail("项目不存在", 404)
    db.update_project(pid, {plat: value})
    return ok({"platform": plat, "value": value})


def api_delete_project(_q, _b, pid):
    db.delete_projects([pid])
    return ok(True)


def api_delete_projects(_q, body):
    n = db.delete_projects(body.get("ids") or [])
    return ok({"deleted": n})


def api_clear(_q, body):
    n = db.clear_type(body.get("ptype") or "")
    return ok({"deleted": n})


# ---------------------------------------------------------------- 导入
def api_import_preview(_q, body):
    """上传 xlsx 后先预览识别结果（不写库）。"""
    path = _save_upload(body)
    if isinstance(path, tuple):
        return path
    try:
        parsed = importer.parse_workbook(path)
    except Exception as exc:  # noqa: BLE001
        return fail(f"解析失败：{exc}")
    finally:
        _cleanup_upload(path)
    by_type = {k: len(v) for k, v in parsed["data"].items()}
    sample = {}
    for k, rows in parsed["data"].items():
        sample[k] = rows[:6]
    return ok({"sheets": parsed["sheets"], "by_type": by_type, "sample": sample})


def api_import_run(_q, body):
    path = _save_upload(body)
    if isinstance(path, tuple):
        return path
    mode = body.get("mode") or "upsert"
    label = body.get("filename") or os.path.basename(path)
    try:
        result = importer.import_workbook(path, mode=mode, source_label=label)
        # 保留一份原始文件副本，便于日后核对
        if result.get("ok"):
            keep = os.path.join(db.get_data_dir(), "导入源文件",
                                f"项目库_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
            try:
                os.makedirs(os.path.dirname(keep), exist_ok=True)
                shutil.copy2(path, keep)
                result["kept"] = keep
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        return fail(f"导入失败：{exc}")
    finally:
        _cleanup_upload(path)
    if not result.get("ok"):
        return fail(result.get("error") or "导入失败")
    return ok(result)


def api_import_path(_q, body):
    """直接按本机路径导入（无需上传）。"""
    path = (body.get("path") or "").strip().strip('"')
    if not path or not os.path.isfile(path):
        return fail("文件不存在：" + path)
    try:
        result = importer.import_workbook(path, mode=body.get("mode") or "upsert")
    except Exception as exc:  # noqa: BLE001
        return fail(f"导入失败：{exc}")
    if not result.get("ok"):
        return fail(result.get("error") or "导入失败")
    return ok(result)


def _save_upload(body):
    content = body.get("content") or ""
    filename = body.get("filename") or "import.xlsx"
    if not content:
        return fail("未收到文件内容")
    tmp_dir = tempfile.mkdtemp(prefix="projlib_")
    tmp_path = os.path.join(tmp_dir, os.path.basename(filename))
    try:
        with open(tmp_path, "wb") as f:
            f.write(base64.b64decode(content.split(",")[-1]))
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return fail(f"文件写入失败：{exc}")
    return tmp_path


def _cleanup_upload(path):
    try:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def api_pick_file(_q, _b):
    """弹出系统文件选择框（仅 Windows 桌面模式可用）。"""
    if not IS_WINDOWS:
        return fail("当前为服务端部署，请直接上传文件")
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="选择项目库 Excel 文件",
            filetypes=[("Excel 文件", "*.xlsx *.xlsm"), ("所有文件", "*.*")],
        )
        root.destroy()
        return ok({"path": path or ""})
    except Exception as exc:  # noqa: BLE001
        return fail(f"选择文件失败：{exc}")


# ---------------------------------------------------------------- 查询 / 导出

def api_export_summary(_q, body):
    """按编号导出统计表。

    支持两种入参：
      ptype + codes                —— 单一类型（兼容旧用法）
      items = {ptype: codes_text}  —— 多类型一次导出（真实/加急/库存/返修）
    导出结果统一成「真实 / 压缩包 / 返修」三大栏的统计表。
    """
    items = body.get("items")
    title = (body.get("title") or "").strip()
    rows = []
    missing = []
    if isinstance(items, dict) and items:
        for ptype, codes_text in items.items():
            if ptype not in db.TYPE_NAME:
                continue
            codes = _split_codes(codes_text)
            if not codes:
                continue
            sub = db.find_by_codes(ptype, codes)
            hit = {r["code"] for r in sub}
            rows.extend(sub)
            missing.extend([c for c in codes if c not in hit])
    else:
        ptype = (body.get("ptype") or "").strip()
        if ptype not in db.TYPE_NAME:
            return fail("请选择项目类型")
        codes = _split_codes(body.get("codes") or "")
        if not codes:
            return fail("请输入至少一个项目编号")
        rows = db.find_by_codes(ptype, codes)
        hit = {r["code"] for r in rows}
        missing = [c for c in codes if c not in hit]
    if not rows:
        return fail("没有匹配到任何项目编号：" + "、".join(missing or []))
    path = export_excel.export_summary(rows, missing=missing, title=title)
    return ok({"path": path, "name": os.path.basename(path),
               "count": len(rows), "missing": missing})


def api_export_multi(_q, body):
    """多类型一次导出（合并为一张统计表）。"""
    items = body.get("items") or {}
    title = (body.get("title") or "").strip()
    rows_by_type, missing_by_type = {}, {}
    for ptype, codes_text in items.items():
        if ptype not in db.TYPE_NAME:
            continue
        codes = _split_codes(codes_text)
        if not codes:
            continue
        rows = db.find_by_codes(ptype, codes)
        hit = {r["code"] for r in rows}
        rows_by_type[ptype] = rows
        missing_by_type[ptype] = [c for c in codes if c not in hit]
    if not any(rows_by_type.values()) and not any(missing_by_type.values()):
        return fail("请输入至少一个项目编号")
    path = export_excel.export_lookup_report(rows_by_type, missing_by_type, title=title)
    return ok({"path": path, "name": os.path.basename(path)})


def api_export_all(query, _b):
    path = export_excel.export_all(query.get("ptype", [""])[0])
    return ok({"path": path, "name": os.path.basename(path)})


# ---------------------------------------------------------------- 用户管理
def api_list_users(_q, _b):
    return ok(auth.list_users())


def api_create_user(_q, body):
    """管理员新增账号：role=admin 建管理员，否则建普通用户。"""
    role = (body.get("role") or auth.ROLE_USER).strip()
    try:
        if role == auth.ROLE_ADMIN:
            user = auth.create_admin(
                username=body.get("username"), password=body.get("password"),
                display_name=body.get("display_name"),
            )
        else:
            user = auth.register(
                username=body.get("username"), password=body.get("password"),
                display_name=body.get("display_name"),
                platform=(body.get("platform") or "").strip(),
            )
        return ok({"user": user})
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


def api_update_user(_q, body, uid):
    """管理员修改账号：显示名 / 角色 / 岗位 / 启用状态 / 密码。"""
    try:
        if body.get("password"):
            auth.set_user_password(uid, body["password"])
        fields = {}
        if "display_name" in body:
            fields["display_name"] = body.get("display_name")
        if "role" in body:
            fields["role"] = body.get("role")
        if "platform" in body:
            fields["platform"] = (body.get("platform") or "").strip()
        if "active" in body:
            fields["active"] = bool(body["active"])
        if fields:
            auth.update_user(uid, **fields)
        return ok({"user": auth.get_user(uid)})
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


def api_delete_user(_q, _b, uid):
    try:
        auth.delete_user(uid)
        return ok(True)
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


# ---------------------------------------------------------------- 月末项目统计
def api_months(_q, _b):
    return ok({"months": db.list_months()})


def api_create_month(_q, body, ctx):
    try:
        m = db.create_month(
            name=body.get("name"), year=body.get("year") or 0,
            month=body.get("month") or 0,
            created_by=(ctx.get("user") or {}).get("id") or 0,
        )
        return ok({"month": m})
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


def api_delete_month(_q, _b, mid):
    db.delete_month(mid)
    return ok(True)


def api_month_detail(_q, _b, mid, ctx=None):
    detail = db.month_detail(mid)
    if not detail:
        return fail("该月份不存在", 404)
    user = (ctx or {}).get("user") or {}
    detail["can_edit_all"] = auth.is_admin(user)
    detail["my_platform"] = user.get("platform") or ""
    detail["platforms"] = [
        {**p, "can_edit": auth.can_edit_platform(user, p["key"])}
        for p in detail["platforms"]
    ]
    return ok(detail)


def api_month_add_entries(_q, body, mid, ctx):
    """填报完成编号。

    两种入参（向后兼容）：
      - 单类型：{"platform","ptype","codes"}（旧调用方 / 脚本仍在用）
      - 多类型：{"platform", "groups":[{"ptype","codes"}, ...]}
        —— 前端「一次填四类」走这条，真实/加急/库存/返修可一并提交。
    """
    user = (ctx or {}).get("user") or {}
    platform = (body.get("platform") or "").strip()
    if not auth.can_edit_platform(user, platform):
        return fail("你没有该平台板块的填写权限", 403)
    month = db.get_month(mid)
    if not month:
        return fail("该月份不存在", 404)
    if month["status"] == "locked":
        return fail("该月份已锁定，无法修改")

    # 归一化多组：groups 优先，否则退化为单类型
    groups = body.get("groups")
    if not isinstance(groups, list) or not groups:
        groups = [{"ptype": body.get("ptype"), "codes": body.get("codes")}]

    norm = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        ptype = (g.get("ptype") or "").strip()
        codes = g.get("codes")
        if isinstance(codes, str):
            codes = _split_codes(codes)
        codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
        if ptype and codes:
            norm.append((ptype, codes))
    if not norm:
        return fail("请至少填写一个编号", 400)

    uid = user.get("id") or 0
    total_added, all_skipped, by_type = 0, [], {}
    try:
        for ptype, codes in norm:
            r = db.add_entries(mid, platform, ptype, codes, user_id=uid)
            total_added += r["added"]
            all_skipped.extend(r["skipped"])
            by_type[ptype] = r["added"]
        return ok({"added": total_added, "skipped": all_skipped, "by_type": by_type})
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


def api_month_delete_entry(_q, _b, mid, eid, ctx):
    user = (ctx or {}).get("user") or {}
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM month_entries WHERE id=?", (eid,)).fetchone()
    if not row:
        return fail("记录不存在", 404)
    if not auth.can_edit_platform(user, dict(row)["platform"]):
        return fail("你没有该平台板块的操作权限", 403)
    db.delete_entry(eid)
    return ok(True)


def api_month_lock(_q, body, mid):
    try:
        db.set_month_status(mid, "locked" if body.get("locked", True) else "open")
        return ok({"month": db.get_month(mid)})
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


def api_month_export(_q, body, mid, ctx=None):
    detail = db.month_detail(mid)
    if not detail:
        return fail("该月份不存在", 404)
    if detail["total"] == 0:
        return fail("该月份还没有任何人填写数据，无法导出")
    try:
        path = export_excel.export_month_stat(detail)
    except Exception as exc:  # noqa: BLE001
        return fail(f"导出失败：{exc}")
    db.touch_month_exported(mid)
    user = (ctx or {}).get("user") or {}
    db.save_export_record(mid, user.get("id") or 0,
                          os.path.basename(path), path)
    detail["month"] = db.get_month(mid)
    return ok({"path": path, "name": os.path.basename(path),
               "total": detail["total"], "month": detail["month"]})


def api_health(_q, _b):
    return ok({"status": "up", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


# ---------------------------------------------------------------- 系统
def api_get_settings(_q, _b):
    return ok(db.get_settings())


# ------------------------------------------------------------------ 工作日志
# 个人私有：接口对所有登录用户开放，但**数据一律按 ctx.user.id 过滤**，
# 任何人（含管理员）都无法读取、修改、删除别人的日志。
def _uid(ctx) -> int:
    return int(((ctx or {}).get("user") or {}).get("id") or 0)


def api_worklogs_list(query, _b, ctx):
    uid = _uid(ctx)
    month = query.get("month", [""])[0]
    keyword = query.get("q", [""])[0]
    items = db.list_worklogs(uid, month=month, keyword=keyword)
    return ok({
        "items": items,
        "total": len(items),
        "months": db.worklog_months(uid),
        "today": datetime.now().strftime("%Y-%m-%d"),
    })


def api_worklog_create(_q, body, ctx):
    uid = _uid(ctx)
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()
    if not title and not content:
        return fail("请填写标题或内容", 400)
    lid = db.add_worklog(uid, body.get("date") or "", title, content)
    return ok({"id": lid, "log": db.get_worklog(lid, uid)})


def api_worklog_update(_q, body, lid, ctx):
    uid = _uid(ctx)
    if not db.get_worklog(lid, uid):
        return fail("日志不存在", 404)
    okk = db.update_worklog(lid, uid, body)
    return ok({"updated": okk, "log": db.get_worklog(lid, uid)})

def api_worklog_delete(_q, _b, lid, ctx):
    uid = _uid(ctx)
    if not db.delete_worklog(lid, uid):
        return fail("日志不存在", 404)
    return ok(True)


def api_worklog_summary(query, _b, ctx):
    uid = _uid(ctx)
    now = datetime.now()
    try:
        year = int(query.get("year", [now.year])[0] or now.year)
        month = int(query.get("month", [now.month])[0] or now.month)
    except (TypeError, ValueError):
        return fail("年月参数不正确", 400)
    if not (1 <= month <= 12):
        return fail("月份应在 1~12 之间", 400)
    return ok(db.worklog_summary(uid, year, month))


def api_update_settings(_q, body):
    return ok(db.update_settings(body))


def api_open(_q, body):
    path = body.get("path") or ""
    if not path or not os.path.exists(path):
        return fail("文件不存在：" + path)
    if not open_path(path, select=bool(body.get("select"))):
        return fail("当前环境无法直接打开文件，请使用下载")
    return ok(True)


def api_open_data_dir(_q, _b):
    if not open_path(db.get_data_dir()):
        return ok({"path": db.get_data_dir(), "no_gui": True})
    return ok({"path": db.get_data_dir()})


def api_download(query, _b):
    """下载导出的文件（容器环境下用户无法直接访问容器内路径）。"""
    path = query.get("path", [""])[0]
    if not path or not os.path.isfile(path):
        return fail("文件不存在")
    # 只允许下载导出目录内的文件，防止越权读取
    try:
        real = os.path.realpath(path)
        exp = os.path.realpath(export_excel._export_dir())
        if not real.startswith(exp):
            return fail("不允许下载该文件")
    except Exception:  # noqa: BLE001
        return fail("路径校验失败")
    with open(path, "rb") as f:
        content = f.read()
    return ("FILE", content, "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet", os.path.basename(path))


def api_system_info(_q, _b):
    info = db.data_info()
    info["seed_available"] = seed.has_seed()
    info["seed_version"] = db.get_meta(seed.SEED_META_KEY, "")
    return ok(info)


def api_reset_to_seed(_q, _b):
    """清空项目库并恢复成内置出厂数据（仅动项目库，不影响账号/设置/月末统计）。"""
    try:
        result = seed.reset_to_seed()
    except Exception as exc:  # noqa: BLE001
        return fail(f"恢复出厂数据失败：{exc}")
    if not result.get("ok"):
        return fail(result.get("error") or "恢复出厂数据失败")
    return ok(result)


def api_backup(_q, _b):
    os.makedirs(os.path.join(db.get_data_dir(), "backups"), exist_ok=True)
    target = os.path.join(
        db.get_data_dir(), "backups", f"projectlib_backup_{datetime.now():%Y%m%d_%H%M%S}.db"
    )
    with db.tx() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(db.get_db_path(), target)
    return ok({"path": target})


def api_package(_q, _b):
    if not getattr(sys, "frozen", False):
        return fail("此功能仅打包后的 exe 支持")
    src_db = db.get_db_path()
    if not os.path.exists(src_db):
        return fail("尚未创建数据库，请先使用一次程序")
    name = f"项目库统计程序_分发包_{datetime.now():%Y%m%d}.zip"
    path = os.path.join(db.get_app_dir(), name)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(sys.executable, os.path.basename(sys.executable))
            z.write(src_db, os.path.join("data", "projectlib.db"))
            z.writestr(os.path.join("data", "portable.flag"), "portable")
    except Exception as exc:  # noqa: BLE001
        return fail(f"打包失败：{exc}")
    return ok({"path": path, "name": name})


ROUTES = [
    # ---- 认证
    ("GET", re.compile(r"^/api/auth/status$"), api_auth_status),
    ("POST", re.compile(r"^/api/auth/register$"), api_auth_register),
    ("POST", re.compile(r"^/api/auth/login$"), api_auth_login),
    ("POST", re.compile(r"^/api/auth/logout$"), api_logout),
    ("GET", re.compile(r"^/api/auth/me$"), api_auth_me),
    # ---- 用户管理（仅管理员）
    ("GET", re.compile(r"^/api/users$"), api_list_users),
    ("POST", re.compile(r"^/api/users$"), api_create_user),
    ("PUT", re.compile(r"^/api/users/(\d+)$"), api_update_user),
    ("DELETE", re.compile(r"^/api/users/(\d+)$"), api_delete_user),
    # ---- 基础数据
    ("GET", re.compile(r"^/api/dashboard$"), api_dashboard),
    ("GET", re.compile(r"^/api/types$"), api_types),
    ("GET", re.compile(r"^/api/projects$"), api_list_projects),
    ("POST", re.compile(r"^/api/projects/delete$"), api_delete_projects),
    ("POST", re.compile(r"^/api/projects$"), api_create_project),
    ("PUT", re.compile(r"^/api/projects/(\d+)$"), api_update_project),
    ("PUT", re.compile(r"^/api/projects/(\d+)/status$"), api_set_platform_status),
    ("GET", re.compile(r"^/api/projects/(\d+)$"), api_get_project),
    ("DELETE", re.compile(r"^/api/projects/(\d+)$"), api_delete_project),
    ("POST", re.compile(r"^/api/projects/clear$"), api_clear),
    ("POST", re.compile(r"^/api/import/preview$"), api_import_preview),
    ("POST", re.compile(r"^/api/import/run$"), api_import_run),
    ("POST", re.compile(r"^/api/import/path$"), api_import_path),
    ("POST", re.compile(r"^/api/system/pick-file$"), api_pick_file),
    ("POST", re.compile(r"^/api/export/summary$"), api_export_summary),
    ("POST", re.compile(r"^/api/export/multi$"), api_export_multi),
    ("GET", re.compile(r"^/api/export/all$"), api_export_all),
    # ---- 月末项目统计
    ("GET", re.compile(r"^/api/months$"), api_months),
    ("POST", re.compile(r"^/api/months$"), api_create_month),
    ("GET", re.compile(r"^/api/months/(\d+)$"), api_month_detail),
    ("DELETE", re.compile(r"^/api/months/(\d+)$"), api_delete_month),
    ("POST", re.compile(r"^/api/months/(\d+)/entries$"), api_month_add_entries),
    ("DELETE", re.compile(r"^/api/months/(\d+)/entries/(\d+)$"), api_month_delete_entry),
    ("POST", re.compile(r"^/api/months/(\d+)/lock$"), api_month_lock),
    ("POST", re.compile(r"^/api/months/(\d+)/export$"), api_month_export),
    ("GET", re.compile(r"^/api/settings$"), api_get_settings),
    ("PUT", re.compile(r"^/api/settings$"), api_update_settings),
    # ---- 工作日志（所有登录用户可用；数据按个人隔离）
    ("GET", re.compile(r"^/api/worklogs$"), api_worklogs_list),
    ("POST", re.compile(r"^/api/worklogs$"), api_worklog_create),
    ("GET", re.compile(r"^/api/worklogs/summary$"), api_worklog_summary),
    ("PUT", re.compile(r"^/api/worklogs/(\d+)$"), api_worklog_update),
    ("DELETE", re.compile(r"^/api/worklogs/(\d+)$"), api_worklog_delete),
    ("POST", re.compile(r"^/api/system/open$"), api_open),
    ("GET", re.compile(r"^/api/system/data-dir$"), api_open_data_dir),
    ("GET", re.compile(r"^/api/system/download$"), api_download),
    ("GET", re.compile(r"^/api/download$"), api_download),
    ("GET", re.compile(r"^/api/system/info$"), api_system_info),
    ("POST", re.compile(r"^/api/system/backup$"), api_backup),
    ("POST", re.compile(r"^/api/system/package$"), api_package),
    ("POST", re.compile(r"^/api/system/reset-to-seed$"), api_reset_to_seed),
    ("GET", re.compile(r"^/api/health$"), api_health),
]


# 处理函数需要拿到「当前登录用户」的接口（作为关键字参数 ctx 传入）
CTX_AWARE = {api_auth_me, api_create_month, api_month_detail,
             api_month_add_entries, api_month_delete_entry, api_month_export,
             api_set_platform_status,
             api_worklogs_list, api_worklog_create, api_worklog_update,
             api_worklog_delete, api_worklog_summary}

# 启动自检：凡在 CTX_AWARE 里的处理函数，必须把 ctx 声明为关键字参数（不能占前三个位置）
# 否则 _call 会因「位置参数 + 关键字参数」冲突抛 TypeError。
def _check_ctx_signatures():
    import inspect
    for fn in CTX_AWARE:
        params = list(inspect.signature(fn).parameters.values())
        if not params or params[-1].name != "ctx":
            raise RuntimeError(
                f"{fn.__name__} 声明在 CTX_AWARE 中，但最后一个参数不是 ctx："
                f"{[p.name for p in params]}")

_check_ctx_signatures()

def _route_key(method: str, path: str) -> str:
    """把具体路径归一化成路由模板（数字段替换为 *）。"""
    parts = [p for p in path.split("/") if p]
    return "/" + "/".join("*" if p.isdigit() else p for p in parts)


def dispatch(method: str, path: str, query: dict, body: dict | None,
             session_token: str = ""):
    """返回 (payload, status) 或 None（404）。"""
    for verb, pattern, func in ROUTES:
        m = pattern.match(path)
        if not (m and verb == method):
            continue
        args = [int(x) if str(x).isdigit() else x for x in m.groups()]

        key = _route_key(method, path)
        # 1) 公开接口
        if key in PUBLIC_KEY_SET:
            ctx = {"user": auth.resolve_session(session_token)}
            return _call(func, query, body, args, ctx)
        # 2) 需要登录
        user = auth.resolve_session(session_token)
        if not user:
            return {"ok": False, "error": "请先登录"}, 401
        # 3) 管理员专属
        if (key, method) in ADMIN_KEY_SET and not auth.is_admin(user):
            return {"ok": False, "error": "需要管理员权限"}, 403
        ctx = {"user": user}
        return _call(func, query, body, args, ctx)
    return None


def _call(func, query, body, args, ctx):
    """统一调用处理函数：需要 ctx 的注入关键字参数。"""
    if func in CTX_AWARE:
        return func(query, body or {}, *args, ctx=ctx)
    return func(query, body or {}, *args) if args else func(query, body or {})


# 路由模板集合（用于鉴权判断，按 (模板, 方法) 精确匹配）
PUBLIC_KEY_SET = {"/api/auth/status", "/api/auth/register",
                  "/api/auth/login", "/api/health"}
ADMIN_KEY_SET = {
    ("/api/users", "GET"), ("/api/users", "POST"),
    ("/api/users/*", "PUT"), ("/api/users/*", "DELETE"),
    ("/api/projects", "POST"),
    ("/api/projects/*", "PUT"), ("/api/projects/*", "DELETE"),
    ("/api/projects/delete", "POST"), ("/api/projects/clear", "POST"),
    ("/api/import/preview", "POST"), ("/api/import/run", "POST"),
    ("/api/import/path", "POST"),
    ("/api/system/pick-file", "POST"),
    ("/api/export/all", "GET"), ("/api/export/multi", "POST"),
    ("/api/system/backup", "POST"), ("/api/system/package", "POST"),
    ("/api/system/reset-to-seed", "POST"),
    ("/api/months", "POST"),            # 创建月份仅管理员
    ("/api/months/*", "DELETE"),
    ("/api/months/*/lock", "POST"),
    ("/api/months/*/export", "POST"),
    # 设置 / 系统维护：与侧边栏一致，仅管理员可见可用
    ("/api/settings", "GET"), ("/api/settings", "PUT"),
    ("/api/system/info", "GET"),
    ("/api/system/open", "POST"), ("/api/system/data-dir", "GET"),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _session_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(auth.SESSION_COOKIE + "="):
                return part[len(auth.SESSION_COOKIE) + 1:].strip()
        # 兼容 header 方式（便于脚本调用）
        return self.headers.get("X-Session-Token") or ""

    def _cookie_header(self, token: str, clear: bool = False) -> str:
        if clear:
            return (f"{auth.SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        max_age = auth.SESSION_DAYS * 86400
        return (f"{auth.SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; "
                f"HttpOnly; SameSite=Lax")

    def _json_cookie(self, status: int, payload, token: str = "", clear: bool = False):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        if token or clear:
            self.send_header("Set-Cookie", self._cookie_header(token, clear))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _run(self, method: str):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        body = self._read_body() if method != "GET" else {}
        try:
            result = dispatch(method, path, parse_qs(parsed.query), body,
                              session_token=self._session_token())
        except Exception as exc:  # noqa: BLE001
            return self._json(500, {"ok": False, "error": f"服务异常：{exc}"})
        if result is None:
            return self._json(404, {"ok": False, "error": "接口不存在"})
        # 文件下载：handler 直接返回 ("FILE", content, ctype, fname)
        if isinstance(result, tuple) and result and result[0] == "FILE":
            _, content, ctype, fname = result
            from urllib.parse import quote
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(fname)}")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        payload, code = result if isinstance(result, tuple) else (result, 200)
        # 登录 / 初始化：把 token 写进 Cookie，并从 body 里摘掉
        token = ""
        clear = False
        if isinstance(payload, dict) and payload.get("data") and isinstance(payload["data"], dict):
            if "token" in payload["data"]:
                token = payload["data"].pop("token") or ""
        if path.endswith("/api/auth/logout"):
            clear = True
        if token or clear:
            return self._json_cookie(code, payload, token=token, clear=clear)
        return self._json(code, payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            return self._run("GET")
        return self._static(path)

    def do_POST(self):
        return self._run("POST")

    def do_PUT(self):
        return self._run("PUT")

    def do_DELETE(self):
        return self._run("DELETE")

    def _static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/").replace("/", os.sep)
        full = resource_path(os.path.join("web", rel))
        if not os.path.isfile(full):
            full = resource_path(os.path.join("web", "index.html"))
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            content = f.read()
        self._send(200, content, MIME.get(ext, "application/octet-stream"))


def _seed_on_boot() -> None:
    """首次启动若项目库为空，自动导入内置出厂数据（失败不阻断启动）。"""
    try:
        r = seed.ensure_seeded()
        if r.get("seeded"):
            print(f"PROJLIB_SEEDED 已灌入内置出厂数据：新建 {r.get('created', 0)} 条"
                  f" / 更新 {r.get('updated', 0)} 条（seed v{r.get('seed_version')}）", flush=True)
        elif r.get("reason") == "no_seed_file":
            print("PROJLIB_SEED_SKIP 未找到内置数据文件，跳过自动导入", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"PROJLIB_SEED_ERROR 内置数据导入失败（已跳过）：{exc}", flush=True)


def start_server(port: int | None = None, host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, int]:
    db.init_db()
    auth.ensure_builtin_admin()      # 首次启动创建内置管理员（见 auth.BUILTIN_ADMIN_*）
    _seed_on_boot()                  # 首次启动（库为空）自动灌入内置出厂数据
    port = port or find_free_port()
    httpd = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


if __name__ == "__main__":
    # 服务端模式（容器 / 验证脚本）：监听 0.0.0.0，端口取 PROJLIB_PORT。
    _port = int(os.environ.get("PROJLIB_PORT") or 8775)
    _host = os.environ.get("PROJLIB_HOST") or "0.0.0.0"
    _httpd, _port = start_server(_port, host=_host)
    print(f"PROJLIB_SERVING http://{_host}:{_port}", flush=True)
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        _httpd.shutdown()
