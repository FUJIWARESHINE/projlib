# -*- coding: utf-8 -*-
"""用户 / 会话 / 权限。

规则：
- 系统**内置一个管理员账号**（用户名/初始密码见下方 `BUILTIN_ADMIN_*` 常量，首次启动自动创建）。
  ⚠️ 该账号信息**严禁出现在任何前端页面或免登录接口响应里**。
- 自助注册**一律只能注册普通用户**（必须选岗位）。
- 管理员可以在「用户管理」里：新增/编辑管理员、重置任意账号密码、停用/删除普通用户。
- 登录后签发随机 token，存 sessions 表，浏览器用 Cookie 携带。
- 角色：admin（管理员）/ user（普通用户）。普通用户绑定一个平台岗位
  （cell/mol/path/animal 可填报；ops/design 只读）。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from datetime import datetime, timedelta

import db

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_NAME = {ROLE_ADMIN: "管理员", ROLE_USER: "普通用户"}

# 内置管理员账号（首次启动自动创建；用户可在系统内改密码/改名）
BUILTIN_ADMIN_USER = "admin"
BUILTIN_ADMIN_PASS = "123456"
BUILTIN_ADMIN_NAME = "管理员"

SESSION_DAYS = 30
SESSION_COOKIE = "projlib_sid"
_LOGIN_LOCK = threading.Lock()


# ------------------------------------------------------------------ 口令
def hash_password(password: str, salt: str = "") -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), 120_000)
    return dk.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, password_hash or "")


# ------------------------------------------------------------------ 用户
def user_count() -> int:
    with db.tx() as conn:
        return conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def admin_exists() -> bool:
    with db.tx() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM users WHERE role=?",
                         (ROLE_ADMIN,)).fetchone()["n"]
    return n > 0


def ensure_builtin_admin() -> bool:
    """确保内置管理员存在（首次启动创建 admin/123456）。返回是否新建。"""
    with _LOGIN_LOCK:
        with db.tx() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE username=?",
                               (BUILTIN_ADMIN_USER,)).fetchone()
            if row:
                return False
            pw_hash, salt = hash_password(BUILTIN_ADMIN_PASS)
            conn.execute(
                "INSERT INTO users(username,display_name,password_hash,salt,role,"
                "platform,created_at,last_login,active) VALUES(?,?,?,?,?,'',?,'',1)",
                (BUILTIN_ADMIN_USER, BUILTIN_ADMIN_NAME, pw_hash, salt,
                 ROLE_ADMIN, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
    return True


def register(username: str, password: str, display_name: str = "",
             platform: str = "") -> dict:
    """自助注册——**只能注册普通用户**，必须选择岗位。"""
    username = (username or "").strip()
    display_name = (display_name or "").strip() or username
    if len(username) < 3:
        raise ValueError("用户名至少 3 个字符")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    if platform not in db.SIGNUP_LABEL:
        raise ValueError("请选择所属岗位（细胞 / 分子 / 病理 / 动物 / 运营 / 设计）")

    pw_hash, salt = hash_password(password)
    with db.tx() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username=?",
                              (username,)).fetchone()
        if exists:
            raise ValueError("该用户名已被注册")
        cur = conn.execute(
            "INSERT INTO users(username,display_name,password_hash,salt,role,"
            "platform,created_at,last_login,active) VALUES(?,?,?,?,?,?,?,'',1)",
            (username, display_name, pw_hash, salt, ROLE_USER, platform,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        uid = cur.lastrowid
    return get_user(uid)


def create_admin(username: str, password: str, display_name: str = "") -> dict:
    """管理员创建另一个管理员账号。"""
    username = (username or "").strip()
    display_name = (display_name or "").strip() or username
    if len(username) < 3:
        raise ValueError("用户名至少 3 个字符")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    pw_hash, salt = hash_password(password)
    with db.tx() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username=?",
                              (username,)).fetchone()
        if exists:
            raise ValueError("该用户名已存在")
        cur = conn.execute(
            "INSERT INTO users(username,display_name,password_hash,salt,role,"
            "platform,created_at,last_login,active) VALUES(?,?,?,?,?,'',?,'',1)",
            (username, display_name, pw_hash, salt, ROLE_ADMIN,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        uid = cur.lastrowid
    return get_user(uid)


def update_user(uid: int, display_name: str = None, platform: str = None,
                role: str = None, active: bool = None) -> dict:
    """管理员修改账号信息（显示名 / 岗位 / 角色 / 启用状态）。未传的字段不动。"""
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise ValueError("账号不存在")
        row = dict(row)
        new_name = row["display_name"] if display_name is None else (display_name.strip() or row["username"])
        new_role = row["role"] if role is None else role
        if new_role not in (ROLE_ADMIN, ROLE_USER):
            raise ValueError("角色不合法")
        # 岗位：管理员为空；普通用户必须合法岗位
        if new_role == ROLE_ADMIN:
            new_plat = ""
        else:
            new_plat = row["platform"] if platform is None else platform
            if new_plat not in db.SIGNUP_LABEL:
                raise ValueError("请选择所属岗位")
        new_active = row.get("active", 1) if active is None else (1 if active else 0)
        # 不允许停用最后一个启用中的管理员
        if new_role != ROLE_ADMIN or not new_active:
            admins = conn.execute(
                "SELECT COUNT(*) n FROM users WHERE role=? AND active=1 AND id<>?",
                (ROLE_ADMIN, uid)).fetchone()["n"]
            losing = (row["role"] == ROLE_ADMIN and row.get("active", 1))
            if losing and admins == 0:
                raise ValueError("至少保留一个启用中的管理员")
        conn.execute(
            "UPDATE users SET display_name=?, platform=?, role=?, active=? WHERE id=?",
            (new_name, new_plat, new_role, new_active, uid))
    return get_user(uid)


def get_user(uid: int):
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return _public(dict(row)) if row else None


def get_user_raw(uid: int):
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None


def _public(row: dict) -> dict:
    plat = row.get("platform") or ""
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row.get("display_name") or row["username"],
        "role": row["role"],
        "role_name": ROLE_NAME.get(row["role"], row["role"]),
        "platform": plat,
        "platform_name": db.SIGNUP_LABEL.get(plat, "全部平台"),
        "readonly": plat in db.READONLY_PLATFORMS,
        "active": bool(row.get("active", 1)),
        "created_at": row.get("created_at") or "",
        "last_login": row.get("last_login") or "",
    }


def list_users() -> list:
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, id"
        ).fetchall()
    return [_public(dict(r)) for r in rows]


def set_user_password(uid: int, password: str) -> None:
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    pw_hash, salt = hash_password(password)
    with db.tx() as conn:
        conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?",
                     (pw_hash, salt, uid))


def set_user_active(uid: int, active: bool) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE users SET active=? WHERE id=?",
                     (1 if active else 0, uid))


def set_user_platform(uid: int, platform: str) -> None:
    if platform and platform not in db.SIGNUP_LABEL:
        raise ValueError("岗位不合法")
    with db.tx() as conn:
        conn.execute("UPDATE users SET platform=? WHERE id=?", (platform, uid))


def delete_user(uid: int) -> None:
    u = get_user_raw(uid)
    if u and u["role"] == ROLE_ADMIN:
        raise ValueError("管理员账号不可删除")
    with db.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))


# ------------------------------------------------------------------ 会话
def create_session(uid: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with db.tx() as conn:
        conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) "
                     "VALUES(?,?,?,?)",
                     (token, uid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), expires))
        conn.execute("UPDATE users SET last_login=? WHERE id=?",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), uid))
    return token


def resolve_session(token: str):
    if not token:
        return None
    with db.tx() as conn:
        row = conn.execute(
            "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token=?", (token,)).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        if datetime.strptime(data["expires_at"], "%Y-%m-%d %H:%M:%S") < datetime.now():
            destroy_session(token)
            return None
    except Exception:  # noqa: BLE001
        pass
    if not data.get("active", 1):
        return None
    return _public(data)


def destroy_session(token: str) -> None:
    if not token:
        return
    with db.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def login(username: str, password: str) -> dict:
    username = (username or "").strip()
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        raise ValueError("用户名或密码错误")
    row = dict(row)
    if not verify_password(password, row["password_hash"], row["salt"]):
        raise ValueError("用户名或密码错误")
    if not row.get("active", 1):
        raise ValueError("账号已被停用，请联系管理员")
    return get_user(row["id"])


# ------------------------------------------------------------------ 权限判定
def is_admin(user) -> bool:
    return bool(user) and user.get("role") == ROLE_ADMIN


def can_edit_platform(user, platform: str) -> bool:
    """该用户能否填写/修改指定平台板块的数据。
    - 管理员：可操作全部平台
    - 运营 / 设计（只读岗位）：不能填任何板块
    - 实验员：只能填自己所属平台
    """
    if not user:
        return False
    if is_admin(user):
        return True
    up = user.get("platform") or ""
    if up in db.READONLY_PLATFORMS:
        return False
    return up == platform
