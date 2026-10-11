# -*- coding: utf-8 -*-
"""出厂内置数据（seed）：随镜像附带的默认项目库。

设计要点：
- 内置一份 xlsx（app/seed/projectlib_seed.xlsx），不预置 sqlite，
  这样数据可追溯、可替换，导入逻辑与管理员手工导入完全一致。
- 首次启动（或数据卷被清空后重启）若 projects 表为空，自动灌入内置数据。
- 管理员可在「设置 → 数据维护」里点「恢复出厂数据」回到这个初始态。
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime

import db
import importer

SEED_META_KEY = "seed_version"

# 内置数据版本号：每次替换 seed xlsx 后手动 +1，
# 便于日后做「升级时增量补种」（当前仅用于标记/展示）。
SEED_VERSION = "1"

SEED_FILENAME = "projectlib_seed.xlsx"


def seed_path() -> str:
    """内置 xlsx 的位置（兼容 PyInstaller 冻结环境）。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "app", "seed", SEED_FILENAME)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed", SEED_FILENAME)


def has_seed() -> bool:
    return os.path.isfile(seed_path())


def project_count() -> int:
    with db.tx() as conn:
        return conn.execute("SELECT COUNT(*) n FROM projects").fetchone()["n"]


def load_seed(*, mode: str = "rebuild", keep_copy: bool = True) -> dict:
    """把内置数据导入数据库。

    mode="rebuild"：先清空 projects 再全量写入（恢复出厂用）
    mode="upsert" ：仅补写/更新（首次启动用，库为空时等价）
    keep_copy=True：在数据目录留一份 xlsx 副本，方便用户核对「出厂数据长什么样」
    """
    path = seed_path()
    if not os.path.isfile(path):
        return {"ok": False, "error": f"内置数据文件缺失：{path}"}

    result = importer.import_workbook(path, mode=mode,
                                      source_label=f"内置出厂数据（seed v{SEED_VERSION}）")
    if not result.get("ok"):
        return result

    db.set_meta(SEED_META_KEY, SEED_VERSION)

    if keep_copy:
        try:
            keep_dir = os.path.join(db.get_data_dir(), "内置源文件")
            os.makedirs(keep_dir, exist_ok=True)
            shutil.copy2(path, os.path.join(keep_dir, SEED_FILENAME))
            result["kept"] = os.path.join(keep_dir, SEED_FILENAME)
        except Exception:  # noqa: BLE001
            pass

    result["seed_version"] = SEED_VERSION
    result["seeded_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result


def ensure_seeded() -> dict:
    """启动时调用：库为空且有内置数据 → 自动灌入。

    返回 {"seeded": bool, ...}；已有数据时不做任何事（不覆盖用户数据）。
    """
    if not has_seed():
        return {"seeded": False, "reason": "no_seed_file"}
    if project_count() > 0:
        return {"seeded": False, "reason": "already_has_data", "total": project_count()}
    r = load_seed(mode="rebuild", keep_copy=False)
    r["seeded"] = bool(r.get("ok"))
    return r


def reset_to_seed() -> dict:
    """清空项目库并恢复成内置出厂数据（仅动 projects 表）。

    不触碰：用户账号、系统设置、月末统计填报、导出历史。
    """
    if not has_seed():
        return {"ok": False, "error": f"内置数据文件缺失：{seed_path()}"}
    r = load_seed(mode="rebuild", keep_copy=True)
    if not r.get("ok"):
        return r
    r["reset"] = True
    return r
