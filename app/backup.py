# -*- coding: utf-8 -*-
"""数据库定期备份：自动快照、按份数保留、恢复。

几条不能动摇的约定：

1. **用 SQLite 官方在线备份 API**（`Connection.backup`）生成快照，而不是
   `copy2`。程序是多线程 Web 服务，随时可能有人正在填报，直接拷文件可能拿到
   写了一半的库；backup API 会自己处理一致性和锁。
2. **只管理自己生成的文件**。用户选的备份目录里很可能还存着别的东西（毕竟
   「选择文件夹」是让人自己挑的），所以清理时严格按文件名模板匹配
   `projectlib-YYYYMMDD-HHMMSS[-tag].db`，一个不符合的文件都不碰。
3. **恢复之前先给当前库留一份**。恢复是唯一可能覆盖现网数据的操作，必须能反向
   回退，否则等于把保险柜砸了换新锁。
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import db

# 文件名模板：projectlib-20261022-093000-auto.db
NAME_RE = re.compile(r"^projectlib-(\d{8})-(\d{6})(?:-([a-z0-9-]+))?\.db$")

TAG_AUTO = "auto"
TAG_MANUAL = "manual"
TAG_SAFETY = "before-restore"

KIND_LABEL = {TAG_AUTO: "自动", TAG_MANUAL: "手动", TAG_SAFETY: "恢复前快照"}

# 默认值（settings 表里没有对应键时用它）
DEF_AUTO = True
DEF_INTERVAL = 24          # 小时
DEF_KEEP = 7               # 份数
KEEP_MIN, KEEP_MAX = 1, 200
INTERVAL_MIN, INTERVAL_MAX = 1, 720

# 失败后隔多久再试（秒），避免每 60 秒刷一次错误
FAIL_BACKOFF = 600

_LOCK = threading.RLock()
_RUNNING = False
_STARTED = False


# ------------------------------------------------------------------ 配置
def default_dir() -> str:
    return os.path.join(db.get_data_dir(), "backups")


def posix_path(p) -> str:
    """对外展示一律用正斜杠。

    Windows 下 os.path 产出的 `D:\\data\\backups` 既难看，又和容器里的
    `/data/backups` 两套画风；界面统一成 POSIX 风格，内部仍按系统路径处理
    （Windows 的 os.path 认正斜杠，不影响读写）。
    """
    return str(p or "").replace("\\", "/")


def resolve_dir_value(value) -> str:
    """把用户填的目录算成真正会用的绝对路径（空 → 默认目录）。"""
    return _clean_dir(value) or default_dir()


def _clean_dir(value: str) -> str:
    """把用户填的目录规范化；空串表示「用默认目录」。"""
    p = str(value or "").strip().strip('"').strip("'")
    if not p:
        return ""
    return os.path.abspath(os.path.expanduser(p))


def _as_bool(value, default: bool = DEF_AUTO) -> bool:
    if value is None:
        return default
    s = str(value).strip().lower()
    if s == "":
        return default
    return s not in ("0", "false", "off", "no")


def _as_int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def get_config() -> dict:
    s = db.get_settings()
    raw_dir = _clean_dir(s.get("backup_dir", ""))
    interval = _as_int(s.get("backup_interval_hours"), DEF_INTERVAL)
    interval = max(INTERVAL_MIN, min(INTERVAL_MAX, interval))
    keep = _as_int(s.get("backup_keep"), DEF_KEEP)
    keep = max(KEEP_MIN, min(KEEP_MAX, keep))
    return {
        "dir": posix_path(raw_dir),           # 用户填的原始值（"" = 默认）
        "dir_effective": posix_path(raw_dir or default_dir()),
        "dir_default": posix_path(default_dir()),
        "auto": _as_bool(s.get("backup_auto"), DEF_AUTO),
        "interval_hours": interval,
        "keep": keep,
    }


def save_config(payload: dict) -> dict:
    """保存备份配置，只收白名单键，顺手把数值夹到合法区间。"""
    out = {}
    if "dir" in payload:
        out["backup_dir"] = _clean_dir(payload.get("dir"))
    if "auto" in payload:
        out["backup_auto"] = "1" if _as_bool(payload.get("auto")) else "0"
    if "interval_hours" in payload:
        v = _as_int(payload.get("interval_hours"), DEF_INTERVAL)
        out["backup_interval_hours"] = str(max(INTERVAL_MIN, min(INTERVAL_MAX, v)))
    if "keep" in payload:
        v = _as_int(payload.get("keep"), DEF_KEEP)
        out["backup_keep"] = str(max(KEEP_MIN, min(KEEP_MAX, v)))
    if out:
        db.update_settings(out)
    return get_config()


# ------------------------------------------------------------------ 目录
def ensure_dir(path: str) -> tuple[bool, str]:
    """确保目录存在且可写。返回 (ok, 错误信息)。"""
    if not path:
        return False, "备份目录为空"
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        return False, f"无法创建目录 {path}：{exc.strerror or exc}"
    if not os.path.isdir(path):
        return False, f"{path} 不是目录"
    probe = os.path.join(path, ".projlib_write_test")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as exc:
        return False, f"目录不可写：{exc.strerror or exc}"
    return True, ""


def _scan(directory: str) -> list:
    """列出本程序生成的备份（新的在前）。其它文件一律忽略。"""
    items = []
    try:
        names = os.listdir(directory)
    except OSError:
        return items
    for name in names:
        m = NAME_RE.match(name)
        if not m:
            continue
        full = os.path.join(directory, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        items.append({
            "name": name,
            "path": posix_path(full),
            "size": st.st_size,
            "mtime": st.st_mtime,
            "time": f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} "
                    f"{m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:]}",
            "tag": m.group(3) or "",
            "kind": KIND_LABEL.get(m.group(3) or "", "备份"),
        })
    items.sort(key=lambda x: (x["mtime"], x["name"]), reverse=True)
    return items


def list_backups() -> list:
    return _scan(get_config()["dir_effective"])


def prune(keep: int, directory: str) -> list:
    """超出保留份数时删掉最旧的。只删模板内的文件。"""
    items = _scan(directory)
    removed = []
    for it in items[keep:]:
        try:
            # 用 name 拼真实路径，别依赖展示用的 posix 串
            os.remove(os.path.join(directory, it["name"]))
            removed.append(it["name"])
        except OSError:
            pass
    return removed


# ------------------------------------------------------------------ 快照
def _snapshot(dest: str) -> None:
    """用在线备份 API 把当前库写进 dest（dest 先写临时名再改名）。"""
    src_path = db.get_db_path()
    if not os.path.isfile(src_path):
        raise RuntimeError("数据库文件不存在")
    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _verify(path: str) -> None:
    """校验一个 .db 是否完整可读，坏了就抛错。"""
    # 只读打开：Windows 路径要换成正斜杠，否则 file: URI 解析不了盘符
    p = os.path.abspath(path).replace("\\", "/")
    uri = "file:" + p + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        conn = sqlite3.connect(path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        result = (row[0] if row else "") or ""
        if str(result).lower() != "ok":
            raise RuntimeError(f"备份文件校验未通过：{str(result)[:120]}")
        # 再确认关键表能读，避免「结构残缺但 integrity_check 通过」这种边角情况
        conn.execute("SELECT COUNT(*) FROM projects").fetchone()
    finally:
        conn.close()


def _parse(ts: str):
    try:
        return datetime.strptime(str(ts).strip(), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _record_success(name: str, kind: str) -> None:
    db.set_meta("backup_last_at", _now_str())
    db.set_meta("backup_last_name", name)
    db.set_meta("backup_last_kind", kind)
    db.set_meta("backup_last_error", "")


def _record_error(msg: str) -> None:
    db.set_meta("backup_last_error", str(msg)[:500])
    db.set_meta("backup_fail_at", _now_str())


def run_backup(kind: str = TAG_MANUAL) -> dict:
    """立刻做一份备份。返回 {"ok": bool, ...}（不抛异常，错误放在 error 里）。"""
    global _RUNNING
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "error": "已有一份备份正在生成，请稍候再试"}
    try:
        _RUNNING = True
        cfg = get_config()
        directory = cfg["dir_effective"]
        okd, err = ensure_dir(directory)
        if not okd:
            _record_error(err)
            return {"ok": False, "error": err}

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"projectlib-{stamp}-{kind}.db"
        seq = 2
        while os.path.exists(os.path.join(directory, name)):
            name = f"projectlib-{stamp}-{kind}-{seq}.db"
            seq += 1
        dest = os.path.join(directory, name)
        tmp = dest + ".part"
        try:
            _snapshot(tmp)
            _verify(tmp)
            os.replace(tmp, dest)
        except Exception as exc:  # noqa: BLE001
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            msg = f"备份失败：{exc}"
            _record_error(msg)
            return {"ok": False, "error": msg}

        removed = prune(cfg["keep"], directory)
        _record_success(name, kind)
        return {
            "ok": True,
            "name": name,
            "dir": posix_path(directory),
            "size": os.path.getsize(dest),
            "removed": removed,
        }
    finally:
        _RUNNING = False
        _LOCK.release()


# ------------------------------------------------------------------ 恢复
def restore(name: str) -> dict:
    """用某份备份覆盖当前库。**先给当前库存一份 before-restore 快照。**"""
    cfg = get_config()
    directory = cfg["dir_effective"]
    if not NAME_RE.match(str(name or "")):
        return {"ok": False, "error": "备份文件名不合法"}
    src = os.path.join(directory, name)
    if not os.path.isfile(src):
        return {"ok": False, "error": f"备份文件不存在：{name}"}

    with _LOCK:
        # 1) 先校验备份本身，坏文件绝不能拿来覆盖现网
        try:
            _verify(src)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

        # 2) 给当前库留退路
        safety = run_backup(TAG_SAFETY)
        if not safety.get("ok"):
            return {"ok": False,
                    "error": "恢复前快照失败，已中止：" + str(safety.get("error"))}

        db_path = db.get_db_path()
        tmp = db_path + ".restore.part"
        try:
            shutil.copy2(src, tmp)
            for suf in ("-wal", "-shm"):
                p = db_path + suf
                if os.path.exists(p):
                    os.remove(p)
            os.replace(tmp, db_path)
        except Exception as exc:  # noqa: BLE001
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return {"ok": False, "error": f"写入数据库失败：{exc}"}

        # 3) 覆盖后校验，并跑一次迁移（旧备份可能缺新表/新列）
        try:
            _verify(db_path)
            db.init_db()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False,
                    "error": f"恢复后校验失败：{exc}（当前库已先备份为 {safety['name']}）"}

        # 4) 备份状态存在 meta 表里，跟着被恢复了 —— 补记一下，
        #    否则「上次备份」会退回旧时间，看着像很久没备份。
        try:
            _record_success(safety["name"], TAG_SAFETY)
        except Exception:  # noqa: BLE001
            pass

        return {"ok": True, "restored": name, "safety_copy": safety["name"]}


# ------------------------------------------------------------------ 状态 / 调度
def get_status() -> dict:
    cfg = get_config()
    last_at = db.get_meta("backup_last_at", "")
    last = _parse(last_at)
    next_at = ""
    if cfg["auto"] and last:
        next_at = (last + timedelta(hours=cfg["interval_hours"])).strftime("%Y-%m-%d %H:%M:%S")
    items = list_backups()
    return {
        "last_at": last_at,
        "last_name": db.get_meta("backup_last_name", ""),
        "last_kind": db.get_meta("backup_last_kind", ""),
        "last_error": db.get_meta("backup_last_error", ""),
        "fail_at": db.get_meta("backup_fail_at", ""),
        "next_at": next_at,
        "running": _RUNNING,
        "count": len(items),
        "total_size": sum(i["size"] for i in items),
        "dir_exists": os.path.isdir(cfg["dir_effective"]),
        "dir_writable": os.access(cfg["dir_effective"], os.W_OK)
                        if os.path.isdir(cfg["dir_effective"]) else False,
    }


def _due(cfg: dict) -> bool:
    last = _parse(db.get_meta("backup_last_at", ""))
    if not last:
        return True                                    # 从没备份过 → 立刻补一份
    fail_at = _parse(db.get_meta("backup_fail_at", ""))
    if fail_at and (datetime.now() - fail_at).total_seconds() < FAIL_BACKOFF:
        return False
    return datetime.now() >= last + timedelta(hours=cfg["interval_hours"])


def _loop() -> None:
    time.sleep(10)          # 让服务先起来，别和启动抢资源
    while True:
        try:
            cfg = get_config()
            if cfg["auto"] and _due(cfg):
                run_backup(TAG_AUTO)
        except Exception:  # noqa: BLE001
            pass            # 调度线程绝不能因为一次异常就死掉
        time.sleep(60)


def start_scheduler() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    threading.Thread(target=_loop, name="projlib-backup", daemon=True).start()
