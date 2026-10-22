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
import backup
import db
import export_excel
import importer

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

# 仅管理员可访问的接口（⚠️ 实际鉴权走下面的 ADMIN_KEY_SET；这份是按前缀的旧清单，
#   只作参考保留，新增管理员接口记得两边都补）
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


def fail(msg: str, code: int = 400, **extra):
    payload = {"ok": False, "error": msg}
    payload.update(extra)
    return payload, code


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


def _prefix_fail(codes):
    """编号缺字母前缀（如把 A539 填成 539）时的统一 400 响应。

    前端弹窗会先拦一道，这里是服务端兜底 —— 绕过页面直接 POST 也拦得住。
    返回 None 表示全部合格。响应里带 codes 字段，前端可直接列出问题编号。
    """
    bad = db.codes_without_prefix(codes)
    if not bad:
        return None
    shown = "、".join(bad[:8]) + ("…" if len(bad) > 8 else "")
    return fail(f"请添加项目编号前缀（{shown}）", 400, codes=bad)


# ------------------------------------------------------------ 消息中心（通知）
# 触发规则见 db.py 里「消息中心 / 站内通知」一节。日志式写库，失败只记日志，
# 绝不影响主流程（新增项目 / 改状态必须照常成功）。
def _safe_notify(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] 通知写入失败：{exc}", file=sys.stderr, flush=True)
        return 0


def _notify_project_added(ptype: str, code: str, name: str, created_by: int = 0) -> int:
    """新增项目 → 通知全体普通账户。

    库存项目只在消息中心留未读（不弹横幅）；真实 / 加急 / 返修会弹「流光横幅」。
    """
    label = db.TYPE_NAME.get(ptype, ptype)
    popup = ptype != "stock"
    return db.add_notification(
        db.NOTIF_PROJECT_IN, db.ROLE_USER,
        f"{code} 项目已入库，请查看",
        f"{label}｜{name}" if name else label,
        ptype=ptype, code=code, popup=popup, created_by=created_by)


def _notify_import_created(created_items: list, created_by: int = 0) -> int:
    """导入产生的新项目 → 按类型汇总成一条通知（避免一次几十条刷屏）。

    单个编号时文案就是「A539 项目已入库，请查看」；多个时变成
    「加急项目已入库 5 个，请查看」，编号清单放正文。
    """
    if not created_items:
        return 0
    by_type = {}
    for it in created_items:
        by_type.setdefault(it.get("ptype") or "", []).append(it)
    rows = []
    for ptype, items in by_type.items():
        label = db.TYPE_NAME.get(ptype, ptype)
        codes = [str(it.get("code") or "") for it in items if it.get("code")]
        if len(codes) == 1:
            title = f"{codes[0]} 项目已入库，请查看"
        else:
            title = f"{label}已入库 {len(items)} 个，请查看"
        body = "、".join(codes[:15]) + ("…" if len(codes) > 15 else "")
        rows.append({
            "category": db.NOTIF_PROJECT_IN, "target_role": db.ROLE_USER,
            "title": title, "body": body, "ptype": ptype,
            "code": codes[0] if len(codes) == 1 else "",
            "popup": ptype != "stock",      # 库存只进消息中心，不弹横幅
            "created_by": created_by,
        })
    return db.add_notifications_bulk(rows)


def _notify_after_import(result: dict, ctx) -> None:
    """导入完成后按「新增项目」广播入库通知。

    明细（created_items）从响应里摘掉，避免 /api/import/run 的返回体过大。
    """
    items = result.pop("created_items", None) or []
    if not items:
        return
    created_by = int(((ctx or {}).get("user") or {}).get("id") or 0)
    _safe_notify(_notify_import_created, items, created_by)


def _audit_content_changed(before: dict, after: dict) -> bool:
    """项目「需要重新审核的内容」是否有变化。

    只比业务内容（名称/平台状态/备注/报价/类型编号），不看 updated_at ——
    前端编辑弹窗「原样保存」也会写 updated_at，拿它判断会天天重开审核。
    """
    if not before or not after:
        return False
    if (str(before.get("ptype") or "") != str(after.get("ptype") or "")
            or str(before.get("code") or "").strip().upper()
            != str(after.get("code") or "").strip().upper()):
        return True
    for k in ("name", "cell", "mol", "path", "animal", "extra",
              "audit_sun", "audit_xu", "send_date", "note"):
        if str(before.get(k) or "").strip() != str(after.get(k) or "").strip():
            return True
    try:
        return round(float(before.get("price") or 0), 2) != round(float(after.get("price") or 0), 2)
    except (TypeError, ValueError):
        return False


def _notify_status_change(before: dict, after: dict, user: dict) -> None:
    """项目平台状态写入后的通知分发（项目库列表内联点勾 / 编辑弹窗共用）。

    ① 平台完成（非 ✅ → ✅）：通知全体管理员。
       **仅普通账户操作时播报** —— 管理员在项目库里替人勾选时不给自己刷屏。
    ② 结项 / 复核：只要写入后项目处于「已完成」状态，就保证待审核单是 pending。
       · 首次结项（未完成 → 完成）→ 建单 + 通知；
       · 已审核过（单子 done）之后项目内容又被改动 → **重新开单**，通知文案写「请重新审核」；
         这是「复核」的正路 —— 早期只认「未完成 → 完成」这一次状态迁移，
         于是复核阶段在编辑弹窗里改平台/改报价（完成状态没翻转）就静默丢掉，
         待审核统计里始终没有这个项目，只有最早那条通知。
       · 原样保存（内容没变）或已经 pending → 不动，避免重复刷单。
       **与操作者角色无关**：管理员替实验员把最后一个平台勾完，一样要进审核流程。
    """
    if after is None:
        return
    before = before or {}
    uid = int((user or {}).get("id") or 0)
    pid = int(after.get("id") or 0)
    code = after.get("code") or ""
    name = after.get("name") or ""
    ptype = after.get("ptype") or ""
    type_label = db.TYPE_NAME.get(ptype, ptype)

    # ① 各平台「未完成 → ✅」的增量播报（管理员自己操作时不发）
    if not auth.is_admin(user):
        for key, label in db.PLATFORMS:
            if (after.get(key) or "").strip() != db.ST_DONE:
                continue
            if (before.get(key) or "").strip() == db.ST_DONE:
                continue
            _safe_notify(db.add_notification,
                         db.NOTIF_PLATFORM_DONE, db.ROLE_ADMIN,
                         f"{code}项目{label}平台已完成",
                         f"{type_label}｜{name}" if name else type_label,
                         ptype=ptype, code=code, platform=key,
                         project_id=pid, created_by=uid)

    # ② 结项 / 复核：只认「写入后已完成」+「内容确实变过」
    st_before = db.project_status(before)
    st_after = db.project_status(after)
    if not st_after["completed"]:
        # 复核不通过 / 取消勾选 → 项目退出完成态，撤掉它的待审核单，
        # 否则审核员会看到一个还没做完的项目挂在「待审核」里
        if st_before["completed"]:
            _safe_notify(db.void_audit,
                         before.get("ptype") or ptype, before.get("code") or code)
        return
    if st_before["completed"] and not _audit_content_changed(before, after):
        return
    cur = db.get_audit_by(ptype, code)
    if cur and cur.get("status") == "pending":
        return                       # 已经在待审核里，别重复建单/刷通知
    reopened = bool(cur)             # 有历史单（已审核）→ 这一轮是复核

    done_labels = "、".join(db.PLATFORM_LABEL.get(k, k) for k in st_after["done"])
    body = f"{type_label}｜{name}｜完成平台：{done_labels}"
    title = f"{code}项目{'内容已变更，请重新审核' if reopened else '已结项，请审核'}"
    if reopened:
        body += "｜复核：项目内容有变更，需再次审核"
    _safe_notify(db.add_notification,
                 db.NOTIF_PROJECT_DONE, db.ROLE_ADMIN, title, body,
                 ptype=ptype, code=code, project_id=pid, created_by=uid)
    _safe_notify(db.upsert_audit, ptype, code, pid, name)
    if db.has_auditor():
        _safe_notify(db.add_notification,
                     db.NOTIF_AUDIT_REQUEST, db.ROLE_AUDITOR, title, body,
                     ptype=ptype, code=code, project_id=pid, popup=True,
                     created_by=uid)


def api_audits(query, _b, ctx=None):
    """结项待审核清单。仅「有审核权限的普通账户」与管理员可看。"""
    user = (ctx or {}).get("user") or {}
    if not (user.get("can_audit") or auth.is_admin(user)):
        return fail("没有审核权限", 403)
    status = (query.get("status", ["pending"])[0] or "pending").strip()
    if status not in ("pending", "done", "all"):
        return fail("status 不合法", 400)
    items = db.list_audits("" if status == "all" else status)
    return ok({"items": items, "pending": db.count_audits("pending"),
               "can_audit": bool(user.get("can_audit"))})


def api_audit_done(_q, _b, aid, ctx=None):
    """审核员复核完成 → 通知全体管理员「某某某已审核完成，请复审」。"""
    user = (ctx or {}).get("user") or {}
    if not (user.get("can_audit") or auth.is_admin(user)):
        return fail("没有审核权限", 403)
    if not db.get_audit(aid):
        return fail("该审核记录不存在", 404)
    done = db.complete_audit(aid, user)
    if not done:
        return fail("该项目已经审核过了", 409, audit=db.get_audit(aid))
    # 通知文案里的人名一律取账号**姓名**（db.person_label；账号没填姓名则写成「账号 xxx」）
    who = db.person_label(user)
    # 事已办完 —— 把「我」那条「请审核」提醒收掉，别再挂在未读里、也别再弹横幅
    _safe_notify(db.mark_notif_read_for_project,
                 int(user.get("id") or 0), db.NOTIF_AUDIT_REQUEST,
                 done["ptype"], done["code"])
    _safe_notify(db.add_notification,
                 db.NOTIF_AUDIT_DONE, db.ROLE_ADMIN,
                 f"{done['code']}项目 {who}已审核完成，请复审",
                 f"{done['type_label']}｜{done['name']}",
                 ptype=done["ptype"], code=done["code"],
                 project_id=done["project_id"], created_by=int(user.get("id") or 0))
    return ok({"audit": done, "auditor": who})


def api_notifications(query, _b, ctx=None):
    """当前用户的消息中心数据：列表 + 未读数 + 待弹横幅。

    ?unread=1 只取未读；?limit=N 限制条数。popups 恒为「未读且 popup=1」。
    受众按角色取：管理员只看管理员消息；普通账户看全员消息；
    有审核权限的普通账户再多看一份「结项待审核」。
    ⚠️ 全服通告还要再按**平台**收窄：只有「不限平台」或明确包含自己岗位的通告才可见，
    所以 roles 之外必须把当前用户的 platform 一起传下去。
    """
    user = (ctx or {}).get("user") or {}
    uid = int(user.get("id") or 0)
    plat = str(user.get("platform") or "")
    roles = db.roles_for(user)
    try:
        limit = int(query.get("limit", ["200"])[0] or 200)
    except (TypeError, ValueError):
        limit = 200
    only_unread = query.get("unread", [""])[0] in ("1", "true", "yes")
    items = db.list_notifications(uid, roles, limit=max(1, min(limit, 500)),
                                  unread_only=only_unread, platform=plat)
    can_audit = bool(user.get("can_audit"))
    return ok({
        "items": items,
        # ★ 角标：审核员「待审核」的提醒恒算未读（只有点「审核完成」才消）
        "unread": db.notif_unread_count(uid, roles, plat, can_audit=can_audit),
        "popups": db.notif_pending_popups(uid, roles, platform=plat),
        "roles": roles,
        "can_audit": bool(user.get("can_audit")),
        # 有审核权限时带上「待我审核」清单，消息中心直接列出来
        "audits": db.list_audits("pending", limit=50) if user.get("can_audit") else [],
    })


def api_notifications_read(_q, body, ctx=None):
    """标记已读：{ids:[...]} 指定条目标记；{all:true} 全部标记。"""
    user = (ctx or {}).get("user") or {}
    uid = int(user.get("id") or 0)
    plat = str(user.get("platform") or "")
    roles = db.roles_for(user)
    if body.get("all"):
        db.mark_notifications_read(uid, None, roles, plat)
    else:
        ids = body.get("ids") or []
        if not isinstance(ids, list):
            return fail("ids 参数不合法", 400)
        db.mark_notifications_read(uid, ids, roles, plat)
    return ok({"unread": db.notif_unread_count(
        uid, roles, plat, can_audit=bool(user.get("can_audit")))})


def api_notifications_clear(_q, _b, ctx=None):
    """清空「我」的消息中心。

    通知是按受众标签广播的，「清空」只对当前用户隐藏（写 hidden=1 的已读记录），
    别的账号照常能看到自己的消息；「待我审核」工作台不受影响。
    ★ 待审核项目的「请审核」提醒清不掉 —— 项目没审核完成，红点与那条消息都必须留着。
    """
    user = (ctx or {}).get("user") or {}
    uid = int(user.get("id") or 0)
    plat = str(user.get("platform") or "")
    roles = db.roles_for(user)
    n = db.hide_notifications(uid, roles, plat)
    return ok({"cleared": n, "unread": db.notif_unread_count(
        uid, roles, plat, can_audit=bool(user.get("can_audit")))})


# ------------------------------------------------------------ 全服通告（管理员）
# 例：月末了，管理员发布「请及时填写月末统计」，选定细胞 / 分子两个平台，
# 两个平台的实验员登录后就会收到弹窗横幅，消息中心也留一条未读。
# 平台留空 = 全体成员（含运营 / 设计）；已发布通告可在此页撤回。
BROADCAST_MAX_TITLE = 60
BROADCAST_MAX_BODY = 600


def api_broadcasts(_q, _b, ctx=None):
    """管理员：已发布通告列表 + 各平台人数（发布页显示「细胞 3 人」，便于估算受众）。"""
    user = (ctx or {}).get("user") or {}
    if not auth.is_admin(user):
        return fail("需要管理员权限", 403)
    counts = db.platform_member_counts()
    platforms = [{"key": k, "label": db.SIGNUP_LABEL.get(k, k),
                  "readonly": k in db.READONLY_PLATFORMS,
                  "count": int(counts.get(k) or 0)}
                 for k in db.BROADCAST_PLATFORM_KEYS]
    return ok({"items": db.list_broadcasts(200), "platforms": platforms,
               "total": int(counts.get("__total__") or 0)})


def api_broadcast_create(_q, body, ctx=None):
    """发布全服通告：{title, body, platforms:[平台键...], popup:true}。

    platforms 为空数组 = 全体成员；popup=false 则只进消息中心、不弹横幅。
    """
    user = (ctx or {}).get("user") or {}
    if not auth.is_admin(user):
        return fail("需要管理员权限", 403)
    title = str(body.get("title") or "").strip()
    if not title:
        return fail("请填写通告标题", 400)
    if len(title) > BROADCAST_MAX_TITLE:
        return fail(f"标题最多 {BROADCAST_MAX_TITLE} 个字符", 400)
    text = str(body.get("body") or "").strip()
    if len(text) > BROADCAST_MAX_BODY:
        return fail(f"正文最多 {BROADCAST_MAX_BODY} 个字符", 400)
    platforms = body.get("platforms")
    if platforms is None:
        platforms = []
    if not isinstance(platforms, list):
        return fail("platforms 参数不合法", 400)
    bad = [str(p) for p in platforms if str(p) not in db.BROADCAST_PLATFORM_KEYS]
    if bad:
        return fail("平台参数不合法：" + "、".join(bad[:4]), 400)
    item = db.add_broadcast(title, text, [str(p) for p in platforms],
                            bool(body.get("popup", True)),
                            created_by=int(user.get("id") or 0))
    return ok(item)


def api_broadcast_reads(_q, _b, bid, ctx=None):
    """管理员：某条通告的已读明细（谁看过 / 谁还没看）。"""
    user = (ctx or {}).get("user") or {}
    if not auth.is_admin(user):
        return fail("需要管理员权限", 403)
    item = db.broadcast_receipts(bid)
    if not item:
        return fail("通告不存在或已被撤回", 404)
    return ok(item)


def api_broadcast_delete(_q, _b, bid, ctx=None):
    """撤回通告：从所有接收者的消息中心消失（连同已读记录一起删掉）。"""
    user = (ctx or {}).get("user") or {}
    if not auth.is_admin(user):
        return fail("需要管理员权限", 403)
    if not db.delete_broadcast(bid):
        return fail("通告不存在或已被撤回", 404)
    return ok({"id": int(bid)})


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
        sort=query.get("sort", ["code"])[0] or "code",
        group=query.get("group", ["type"])[0] or "type",
    ))


def api_get_project(_q, _b, pid):
    row = db.get_project(pid)
    if not row:
        return fail("项目不存在", 404)
    return ok(row)


_VALID_TYPES = {k for k, _ in db.TYPES}


def api_create_project(_q, body, ctx=None):
    ptype = str(body.get("ptype") or "").strip()
    if ptype not in _VALID_TYPES:
        return fail("项目类型不合法", 400)
    try:
        pid = db.upsert_project(body)
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))
    # 新增项目 → 通知全体普通账户（真实/加急/返修弹横幅，库存只进消息中心）
    row = db.get_project(pid)
    if row:
        _safe_notify(_notify_project_added, row["ptype"], row["code"],
                     row.get("name") or "",
                     created_by=int(((ctx or {}).get("user") or {}).get("id") or 0))
        # 手工新增时就把所有平台填成 ✅ 的，一样要进结项审核
        _notify_status_change({}, row, (ctx or {}).get("user") or {})
    return ok({"id": pid})


def api_update_project(_q, body, pid, ctx=None):
    # 归属类型可以改（项目从库存转加急等），但要挡住非法值
    ptype = str(body.get("ptype") or "").strip()
    if ptype and ptype not in _VALID_TYPES:
        return fail("项目类型不合法", 400)
    before = db.get_project(pid)
    if not before:
        return fail("项目不存在", 404)
    db.update_project(pid, body)
    # 编辑弹窗里也能改平台状态（cell/mol/path/animal），
    # 这条路径以前完全不通知 —— 管理员整套平台改成 ✅ 后审核员收不到待审核。
    after = db.get_project(pid)
    _notify_status_change(before, after, (ctx or {}).get("user") or {})
    # 换了类型 / 编号（库存转加急、编号写错重改）：旧组若一条项目都不剩，
    # 它的待审核单跟着作废，免得「待审核」里挂着一条已经改走的旧编号。
    if after and (str(before.get("ptype") or "") != str(after.get("ptype") or "")
                  or str(before.get("code") or "").strip().upper()
                  != str(after.get("code") or "").strip().upper()):
        if not db.find_by_codes(before.get("ptype") or "", [before.get("code") or ""]):
            _safe_notify(db.void_audit, before.get("ptype") or "", before.get("code") or "")
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
    before = db.get_project(pid)
    if not before:
        return fail("项目不存在", 404)
    db.update_project(pid, {plat: value})
    # 平台状态变更 → 通知管理员 / 结项进审核（普通账户与管理员都走同一条逻辑）
    _notify_status_change(before, db.get_project(pid), user)
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


def api_import_run(_q, body, ctx=None):
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
    _notify_after_import(result, ctx)
    return ok(result)


def api_import_path(_q, body, ctx=None):
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
    _notify_after_import(result, ctx)
    return ok(result)


def _save_upload(body):
    content = body.get("content") or ""
    filename = body.get("filename") or "import.xlsx"
    if not content:
        return fail("未收到文件内容")
    # openpyxl 按扩展名判断格式，源文件名没有 .xlsx 时要补上，
    # 否则会报 "does not support  file format" 这种看不懂的错误
    fname = os.path.basename(filename)
    if not fname.lower().endswith((".xlsx", ".xlsm")):
        fname += ".xlsx"
    tmp_dir = tempfile.mkdtemp(prefix="projlib_")
    tmp_path = os.path.join(tmp_dir, fname)
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
            if body.get("can_audit"):
                auth.update_user(user["id"], can_audit=True)
                user = auth.get_user(user["id"])
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
        if "can_audit" in body:
            fields["can_audit"] = bool(body["can_audit"])
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


def api_update_month(_q, body, mid):
    """改月份名称 / 年份 / 月份。仅管理员（在 ADMIN_KEY_SET 里）。

    月份建错（把 9 月建成 10 月）时直接改回来，已填报的编号一条都不会丢。
    字段不传 = 不改。
    """
    y = body.get("year")
    mo = body.get("month")
    try:
        m = db.update_month(
            mid,
            name=body.get("name"),
            year=None if y in (None, "") else y,
            month=None if mo in (None, "") else mo,
        )
        return ok({"month": m})
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


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

    _bad = _prefix_fail([c for _pt, cs in norm for c in cs])
    if _bad:
        return _bad          # 编号必须带字母前缀（如 A539），否则拒绝保存

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


def api_month_sync_entries(_q, body, mid, ctx):
    """整平台一次编辑填报编号（前端卡片上的「编辑编号」弹窗）。

    收 {"platform", "groups":[{"ptype","codes"|"codes文本"}, ...]}，
    语义是**整体替换**该平台当月的填报：提交里没有的旧编号会被删掉，
    所以前端必须把四类现有编号全部回填（留空 = 该类型下没有编号）。

    权限与添加编号一致：对该平台有填写权限即可（管理员 = 全部平台）。
    特意**不放进 ADMIN_KEY_SET**，否则实验员就改不了自己平台的编号了。
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

    groups = body.get("groups")
    if not isinstance(groups, list) or not groups:
        return fail("缺少 groups 参数", 400)
    norm = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        ptype = (g.get("ptype") or "").strip()
        codes = g.get("codes")
        if isinstance(codes, str):
            codes = _split_codes(codes)
        codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
        if ptype:
            norm.append((ptype, codes))
    if not norm:
        return fail("请至少提交一个类型", 400)

    _bad = _prefix_fail([c for _pt, cs in norm for c in cs])
    if _bad:
        return _bad          # 编号必须带字母前缀（如 A539），否则拒绝保存

    try:
        r = db.sync_entries(mid, platform, norm, user_id=user.get("id") or 0)
        r["platform_label"] = db.PLATFORM_LABEL.get(platform, platform)
        return ok(r)
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


def api_month_move_entry(_q, body, mid, eid, ctx):
    """改一条填报记录的编号 / 类型 / 平台（月末统计里拖动编号、弹窗再编辑都用它）。

    只收 {platform?, ptype?, code?}，都可省（省 = 保持原值）。
    权限：对**原平台**和**目标平台**都要有填写权限（管理员全能，实验员只动得了自己的平台）。
    """
    user = (ctx or {}).get("user") or {}
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM month_entries WHERE id=?", (eid,)).fetchone()
    if not row:
        return fail("记录不存在", 404)
    row = dict(row)
    if int(row["month_id"]) != int(mid):
        return fail("记录不属于该月份", 400)
    if not auth.can_edit_platform(user, row["platform"]):
        return fail("你没有该平台板块的操作权限", 403)
    month = db.get_month(mid)
    if not month:
        return fail("该月份不存在", 404)
    if month["status"] == "locked":
        return fail("该月份已锁定，无法修改")

    ptype = (body.get("ptype") or "").strip() or row["ptype"]
    platform = (body.get("platform") or "").strip() or row["platform"]
    raw_code = body.get("code")
    code = str(raw_code).strip() if raw_code is not None else ""
    if raw_code is not None and not code:
        return fail("项目编号不能为空", 400)   # 明确传了空编号 = 非法（不传才是「不改」）
    if raw_code is not None:
        _bad = _prefix_fail([code])
        if _bad:
            return _bad      # 单条改编号也要带字母前缀（拖动只改归类、不带 code，不受影响）
    if platform not in db.PLATFORM_KEYS:
        return fail("平台不合法", 400)
    if ptype not in db.TYPE_NAME:
        return fail("项目类型不合法", 400)
    if not auth.can_edit_platform(user, platform):
        return fail("你没有目标平台板块的操作权限", 403)
    try:
        r = db.update_entry(eid, platform=platform, ptype=ptype, code=code)
        if r.get("conflict"):
            tgt = r.get("target") or {}
            return fail(
                f"{db.TYPE_NAME.get(tgt.get('ptype') or ptype, ptype)} 下已经有 "
                f"{tgt.get('code') or code} 了，未修改", 409)
        r["type_label"] = db.TYPE_NAME.get(ptype, ptype)
        r["platform_label"] = db.PLATFORM_LABEL.get(platform, platform)
        return ok(r)
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


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
    return ok(info)


def api_backup(_q, _b):
    """老的「备份数据库」入口（数据维护卡片用），现在走统一备份流程：
    存到配置好的备份目录、按保留份数清理。"""
    res = backup.run_backup(backup.TAG_MANUAL)
    if not res.get("ok"):
        return fail(res.get("error") or "备份失败")
    return ok({
        "path": os.path.join(res["dir"], res["name"]).replace("\\", "/"),
        "name": res["name"],
        "size": res.get("size", 0),
        "removed": res.get("removed", []),
    })


# ---------------------------------------------------------------- 备份管理
def api_backup_config(_q, _b):
    return ok({
        "config": backup.get_config(),
        "status": backup.get_status(),
        "items": backup.list_backups(),
    })


def api_backup_config_save(_q, body):
    # 先验证目录再落库：否则用户收到「目录不可写」的错误，配置却已经存进去了，
    # 之后每次自动备份都会静默失败 —— 这种「报错但生效一半」最坑人。
    if "dir" in body:
        okd, err = backup.ensure_dir(backup.resolve_dir_value(body.get("dir")))
        if not okd:
            return fail(err)
    cfg = backup.save_config(body)
    okd, err = backup.ensure_dir(cfg["dir_effective"])
    if not okd:
        return fail(err)
    return ok({
        "config": cfg,
        "status": backup.get_status(),
        "items": backup.list_backups(),
    })


def api_backup_run(_q, _b):
    res = backup.run_backup(backup.TAG_MANUAL)
    if not res.get("ok"):
        return fail(res.get("error") or "备份失败")
    return ok({
        "name": res["name"], "dir": res["dir"],
        "size": res.get("size", 0), "removed": res.get("removed", []),
        "config": backup.get_config(),
        "status": backup.get_status(),
        "items": backup.list_backups(),
    })


def api_backup_restore(_q, body):
    res = backup.restore(body.get("name") or "")
    if not res.get("ok"):
        return fail(res.get("error") or "恢复失败")
    return ok({
        "restored": res["restored"],
        "safety_copy": res["safety_copy"],
        "config": backup.get_config(),
        "status": backup.get_status(),
        "items": backup.list_backups(),
    })


def api_backup_delete(query, _b):
    name = query.get("name", [""])[0]
    if not backup.NAME_RE.match(name or ""):
        return fail("备份文件名不合法")
    path = os.path.join(backup.get_config()["dir_effective"], name)
    if not os.path.isfile(path):
        return fail("备份文件不存在", 404)
    try:
        os.remove(path)
    except OSError as exc:
        return fail(f"删除失败：{exc}")
    return ok({
        "deleted": name,
        "config": backup.get_config(),
        "status": backup.get_status(),
        "items": backup.list_backups(),
    })


def api_backup_download(query, _b):
    name = query.get("name", [""])[0]
    if not backup.NAME_RE.match(name or ""):
        return fail("备份文件名不合法")
    path = os.path.join(backup.get_config()["dir_effective"], name)
    if not os.path.isfile(path):
        return fail("备份文件不存在", 404)
    with open(path, "rb") as f:
        content = f.read()
    return ("FILE", content, "application/x-sqlite3", name)


def api_backup_browse(query, _b):
    """服务端目录浏览（容器部署时没法弹系统文件夹选择框）。只返回目录，不读文件。"""
    raw = (query.get("path", [""])[0] or "").strip()
    cfg = backup.get_config()
    path = os.path.abspath(os.path.expanduser(raw or cfg["dir_effective"]))
    if not os.path.isdir(path):
        return fail("目录不存在：" + path)
    dirs = []
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                dirs.append({"name": e.name, "path": e.path})
    except PermissionError:
        return fail("没有权限读取该目录")
    except OSError as exc:
        return fail(f"读取目录失败：{exc}")
    dirs.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(path)
    if parent == path:
        parent = ""
    # 展示层统一正斜杠（容器是 /data/backups，Windows 也显示成 D:/x/y）
    for d in dirs:
        d["path"] = backup.posix_path(d["path"])
    return ok({
        "path": backup.posix_path(path),
        "parent": backup.posix_path(parent),
        "dirs": dirs[:800],
        "writable": os.access(path, os.W_OK),
        # 桌面（Windows exe）模式下前端会多给一个「系统选择框」按钮
        "native": IS_WINDOWS,
        "shortcuts": [
            {"name": "默认备份目录", "path": backup.posix_path(cfg["dir_default"])},
            {"name": "数据目录", "path": backup.posix_path(db.get_data_dir())},
        ],
    })


def api_backup_pick_dir(_q, _b):
    """桌面（Windows exe）模式：弹系统「选择文件夹」。容器里走 browse。"""
    if not IS_WINDOWS:
        return fail("当前为服务端部署，请用「浏览」选择容器内目录")
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="选择数据库备份文件夹", mustexist=False)
        root.destroy()
        return ok({"path": path or ""})
    except Exception as exc:  # noqa: BLE001
        return fail(f"选择文件夹失败：{exc}")


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
    ("PUT", re.compile(r"^/api/months/(\d+)$"), api_update_month),
    ("DELETE", re.compile(r"^/api/months/(\d+)$"), api_delete_month),
    ("POST", re.compile(r"^/api/months/(\d+)/entries$"), api_month_add_entries),
    ("PUT", re.compile(r"^/api/months/(\d+)/entries$"), api_month_sync_entries),
    ("PUT", re.compile(r"^/api/months/(\d+)/entries/(\d+)$"), api_month_move_entry),
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
    # ---- 消息中心（所有登录用户可用；按角色广播 + 各人各自的已读状态）
    ("GET", re.compile(r"^/api/notifications$"), api_notifications),
    ("POST", re.compile(r"^/api/notifications/read$"), api_notifications_read),
    ("POST", re.compile(r"^/api/notifications/clear$"), api_notifications_clear),
    # ---- 结项审核（有审核权限的普通账户；管理员也能看）
    ("GET", re.compile(r"^/api/audits$"), api_audits),
    ("POST", re.compile(r"^/api/audits/(\d+)/done$"), api_audit_done),
    # ---- 全服通告（仅管理员：发布 / 查看已发布 / 撤回）
    ("GET", re.compile(r"^/api/broadcasts$"), api_broadcasts),
    ("POST", re.compile(r"^/api/broadcasts$"), api_broadcast_create),
    ("GET", re.compile(r"^/api/broadcasts/(\d+)/reads$"), api_broadcast_reads),
    ("DELETE", re.compile(r"^/api/broadcasts/(\d+)$"), api_broadcast_delete),
    ("POST", re.compile(r"^/api/system/open$"), api_open),
    ("GET", re.compile(r"^/api/system/data-dir$"), api_open_data_dir),
    ("GET", re.compile(r"^/api/system/download$"), api_download),
    ("GET", re.compile(r"^/api/download$"), api_download),
    ("GET", re.compile(r"^/api/system/info$"), api_system_info),
    ("POST", re.compile(r"^/api/system/backup$"), api_backup),
    # ---- 数据库备份（配置 / 手动 / 恢复 / 删除 / 下载 / 目录浏览）
    ("GET", re.compile(r"^/api/backup$"), api_backup_config),
    ("PUT", re.compile(r"^/api/backup$"), api_backup_config_save),
    ("POST", re.compile(r"^/api/backup/run$"), api_backup_run),
    ("POST", re.compile(r"^/api/backup/restore$"), api_backup_restore),
    ("POST", re.compile(r"^/api/backup/pick-dir$"), api_backup_pick_dir),
    ("DELETE", re.compile(r"^/api/backup/file$"), api_backup_delete),
    ("GET", re.compile(r"^/api/backup/download$"), api_backup_download),
    ("GET", re.compile(r"^/api/backup/browse$"), api_backup_browse),
    ("POST", re.compile(r"^/api/system/package$"), api_package),
    ("GET", re.compile(r"^/api/health$"), api_health),
]


# 处理函数需要拿到「当前登录用户」的接口（作为关键字参数 ctx 传入）
CTX_AWARE = {api_auth_me, api_create_month, api_month_detail,
             api_month_add_entries, api_month_sync_entries, api_month_move_entry,
             api_month_delete_entry,
             api_month_export,
             api_set_platform_status,
             api_create_project, api_update_project, api_import_run, api_import_path,
             api_notifications, api_notifications_read, api_notifications_clear,
             api_audits, api_audit_done,
             api_broadcasts, api_broadcast_create, api_broadcast_reads,
             api_broadcast_delete,
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
    ("/api/months", "POST"),            # 创建月份仅管理员
    ("/api/months/*", "PUT"),           # 改月份名称 / 年月（建错时修正）
    ("/api/months/*", "DELETE"),
    ("/api/months/*/lock", "POST"),
    ("/api/months/*/export", "POST"),
    # 设置 / 系统维护：与侧边栏一致，仅管理员可见可用
    ("/api/settings", "GET"), ("/api/settings", "PUT"),
    ("/api/system/info", "GET"),
    ("/api/system/open", "POST"), ("/api/system/data-dir", "GET"),
    # 数据库备份：配置、手动备份、恢复、删除、下载、目录浏览，全部只给管理员
    ("/api/backup", "GET"), ("/api/backup", "PUT"),
    ("/api/backup/run", "POST"), ("/api/backup/restore", "POST"),
    ("/api/backup/pick-dir", "POST"),
    ("/api/backup/file", "DELETE"), ("/api/backup/download", "GET"),
    ("/api/backup/browse", "GET"),
    # 全服通告：发布 / 查看已发布 / 撤回，都只给管理员
    ("/api/broadcasts", "GET"), ("/api/broadcasts", "POST"),
    ("/api/broadcasts/*/reads", "GET"),
    ("/api/broadcasts/*", "DELETE"),
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


def start_server(port: int | None = None, host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, int]:
    db.init_db()
    auth.ensure_builtin_admin()      # 首次启动创建内置管理员（见 auth.BUILTIN_ADMIN_*）
    backup.start_scheduler()         # 定期备份调度线程（到期才真正干活）
    # 注意：镜像不再内置任何项目库数据，新实例项目库为空，由管理员自行导入。
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
