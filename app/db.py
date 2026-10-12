# -*- coding: utf-8 -*-
"""数据层：SQLite 连接、建表、项目库 CRUD、统计。"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime

APP_NAME = "ProjectLibrary"

# 项目类型（与源 xlsx 的 sheet 一一对应；real 为「真实项目」，导出的第一栏）
TYPES = [
    ("real", "真实项目"),
    ("urgent", "加急项目"),
    ("stock", "库存项目"),
    ("repair", "返修项目"),
]
TYPE_NAME = dict(TYPES)
TYPE_ORDER = {k: i for i, (k, _) in enumerate(TYPES)}

# 导出时的大栏（A 列）分组：加急 + 库存 合并为「压缩包」
EXPORT_GROUPS = [
    ("real", "真实", ["real"]),
    ("pack", "压缩包", ["urgent", "stock"]),
    ("repair", "返修", ["repair"]),
]
# 类型 -> 所属大栏
GROUP_OF_TYPE = {t: g for g, _label, members in EXPORT_GROUPS for t in members}

# 平台列（C-F），G 列在加急/库存里是「报价」，在返修里是「返修要求」
PLATFORMS = [
    ("cell", "细胞"),
    ("mol", "分子"),
    ("path", "病理"),
    ("animal", "动物"),
]
PLATFORM_KEYS = [k for k, _ in PLATFORMS]
PLATFORM_LABEL = dict(PLATFORMS)

# 注册时可选的岗位：前四个是实验平台（可填自己板块），
# 运营 / 设计 为只读岗位（全程只能查看，不能填任何板块）。
SIGNUP_OPTIONS = [
    ("cell", "细胞", False),
    ("mol", "分子", False),
    ("path", "病理", False),
    ("animal", "动物", False),
    ("ops", "运营", True),
    ("design", "设计", True),
]
SIGNUP_LABEL = {k: v for k, v, _ in SIGNUP_OPTIONS}
READONLY_PLATFORMS = {k for k, _v, ro in SIGNUP_OPTIONS if ro}
# 管理员岗位显示名
ADMIN_POST_NAME = "管理"

# 平台状态取值
# 语义约定（2026-10-10 与用户确认）：
#   "" (空白)  = 该项目**设计了**该平台的实验，但**尚未完成**
#   "/"        = 该项目**没有设计**该平台实验（不参与完成判定）
#   "✅"       = 该平台实验已完成
#   "❌"       = 该平台实验明确标记未完成
ST_NONE = "/"        # 未设计（不参与完成判定）
ST_EMPTY = ""        # 已设计、未完成（参与判定，计入未完成）
ST_DONE = "✅"       # 已完成
ST_UNDONE = "❌"     # 未完成

AUDITORS = [("audit_sun", "孙"), ("audit_xu", "徐")]

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ptype TEXT NOT NULL,                 -- urgent / stock / repair
  code TEXT NOT NULL,                  -- 项目编号（A 列）
  name TEXT,                           -- 项目名（B 列）
  src_row INTEGER DEFAULT 0,           -- 源 xlsx 行号（保序）
  cell TEXT DEFAULT '',                -- 细胞
  mol TEXT DEFAULT '',                 -- 分子
  path TEXT DEFAULT '',                -- 病理
  animal TEXT DEFAULT '',              -- 动物
  extra TEXT DEFAULT '',               -- G 列：加急/库存=报价；返修=返修要求
  price REAL DEFAULT 0,                -- 报价数值（从 extra 解析，返修恒为 0）
  audit_sun TEXT DEFAULT '',           -- 孙审核
  audit_xu TEXT DEFAULT '',            -- 徐审核
  send_date TEXT DEFAULT '',           -- 发送日期
  note TEXT DEFAULT '',                -- 程序内备注
  disc TEXT DEFAULT '',                -- 分组标识（同类型+编号下，用该列内容区分多行）
  seq INTEGER NOT NULL DEFAULT 0,      -- 同一 (类型,编号,disc) 组内的第几次出现（0 起）
  created_at TEXT,
  updated_at TEXT
);
-- 唯一键含 seq：允许「同类型 + 同编号 + 同内容」的多行并存
-- （例如同一项目先后返修多次、返修要求文字完全相同，两行都要留）
CREATE UNIQUE INDEX IF NOT EXISTS uq_projects ON projects(ptype, code, disc, seq);

CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);

-- 用户（首个注册的账号为管理员，之后一律普通用户）
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT DEFAULT '',
  password_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',       -- admin / user
  platform TEXT DEFAULT '',                -- 普通用户所属平台 cell/mol/path/animal
  active INTEGER DEFAULT 1,
  created_at TEXT,
  last_login TEXT
);

CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at TEXT,
  expires_at TEXT
);

-- 月末项目统计：一个月份 = 一份统计
CREATE TABLE IF NOT EXISTS months(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,                      -- 如「2026年9月」
  year INTEGER,
  month INTEGER,
  status TEXT DEFAULT 'open',              -- open 填报中 / locked 已锁定
  created_by INTEGER,
  created_at TEXT,
  exported_at TEXT,
  UNIQUE(name)
);

-- 月末填报明细：某月份下、某平台、某编号、某类型
CREATE TABLE IF NOT EXISTS month_entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  month_id INTEGER NOT NULL,
  platform TEXT NOT NULL,                  -- cell/mol/path/animal
  ptype TEXT NOT NULL,                     -- real/urgent/stock/repair
  code TEXT NOT NULL,
  user_id INTEGER,
  updated_at TEXT,
  UNIQUE(month_id, platform, ptype, code)
);

-- 导出记录
CREATE TABLE IF NOT EXISTS exports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  month_id INTEGER,
  user_id INTEGER,
  filename TEXT,
  path TEXT,
  created_at TEXT
);

-- 工作日志（**个人私有**：任何查询都必须带 user_id 过滤，杜绝越权读他人日志）
CREATE TABLE IF NOT EXISTS worklogs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,               -- 归属人（隔离依据）
  log_date TEXT NOT NULL,                 -- 日志所属日期 YYYY-MM-DD（默认当天）
  title TEXT DEFAULT '',                  -- 标题 / 摘要
  content TEXT DEFAULT '',                -- 正文
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_worklogs_user_date ON worklogs(user_id, log_date);
"""

# ------------------------------------------------------------------ 路径
def get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def portable_data_dir():
    """随身模式：exe 同目录有 data\\portable.flag 或已存在 data 目录时启用。"""
    app = get_app_dir()
    flag = os.path.join(app, "data", "portable.flag")
    data_dir = os.path.join(app, "data")
    if os.path.exists(flag):
        return data_dir
    # 冻结后默认随身（exe 同目录 data），非冻结默认本机用户目录
    if getattr(sys, "frozen", False):
        return data_dir
    return None


def get_data_dir() -> str:
    override = os.environ.get("PROJLIB_DATA_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    portable = portable_data_dir()
    if portable:
        os.makedirs(portable, exist_ok=True)
        return portable
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME, "data")
    os.makedirs(path, exist_ok=True)
    return path


def get_db_path() -> str:
    return os.path.join(get_data_dir(), "projectlib.db")


@contextmanager
def tx():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn) -> None:
    """老库升级：补 seq 列，并把唯一索引从 (ptype,code,disc) 换成带 seq 的四列。

    旧索引会阻止「同编号 + 同内容」的多行并存（同一项目多次返修会被吞掉），
    所以必须换掉；存量数据 seq 默认 0，组内本来就只有一行，迁移后依然唯一。
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if "seq" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN seq INTEGER NOT NULL DEFAULT 0")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_projects'"
    ).fetchone()
    if row is not None and "seq" not in (row["sql"] or ""):
        conn.execute("DROP INDEX IF EXISTS uq_projects")
        conn.execute("CREATE UNIQUE INDEX uq_projects ON projects(ptype, code, disc, seq)")


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


# ------------------------------------------------------------------ 项目
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 排序表达式：先按类型分组（真实→加急→库存→返修），再按所选维度
#   code → 按编号（A99 排在 A100 前面，用长度再字典序，避免字符串比较的坑）
#   src  → 按源表录入顺序（保序，导入后与 Excel 行序一致）
_CODE_ORDER = ("UPPER(SUBSTR(code,1,1)) {d}, LENGTH(code) {d}, UPPER(code) {d}")


def _order_sql(sort: str, direction: str, group: str = "type") -> str:
    """排序 SQL。

    group="type" → 先按类型分组（真实→加急→库存→返修），组内再按所选维度；
    group="all"  → 不分组，全局按所选维度排（编号就是唯一坐标，
                   A393 直接排到 393 该在的位置，不管它属于哪个类型）。
    """
    head = ""
    if str(group).lower() != "all":
        head = ("CASE ptype WHEN 'real' THEN 0 WHEN 'urgent' THEN 1 "
                "WHEN 'stock' THEN 2 ELSE 3 END, ")
    if str(sort).lower() == "src":
        return f"ORDER BY {head}src_row {direction}, id {direction}"
    code_part = _CODE_ORDER.format(d=direction)
    return f"ORDER BY {head}{code_part}, id {direction}"


def list_projects(ptype: str = "", search: str = "", platform: str = "",
                  status: str = "", page: int = 1, page_size: int = 50,
                  order: str = "asc", sort: str = "code",
                  group: str = "type") -> dict:
    """按条件分页查询项目库。

    status 语义（配合 platform，二者需同时给出）：
      "✅"        → 该平台已完成
      "❌"        → 该平台未完成（❌）
      "/"         → 该平台未设计
      "undone"    → **未完成（含空白）**：值 ≠ "/" 且 ≠ "✅"
                     —— 用户视角的「未完成」，与 dashboard 的 undone 口径一致
      "" / "done" → 不按状态过滤（"" 时）
    """
    where, args = [], []
    if ptype:
        where.append("ptype=?")
        args.append(ptype)
    if search:
        where.append("(code LIKE ? OR name LIKE ? OR extra LIKE ?)")
        like = f"%{search}%"
        args += [like, like, like]
    if platform and status:
        col_map = {"cell": "cell", "mol": "mol", "path": "path", "animal": "animal"}
        c = col_map.get(platform)
        if c:
            if status == "undone":
                # 未完成（含空白）：设计了该平台（≠ "/"）但没打 ✅
                where.append(f"COALESCE({c},'') <> ?")
                args.append(ST_NONE)
                where.append(f"COALESCE({c},'') <> ?")
                args.append(ST_DONE)
            else:
                where.append(f"COALESCE({c},'')=?")
                args.append(status)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    # 排序：默认按类型分组 + 编号（A99 < A100，符合项目库直觉）；
    # sort="src" 按源表录入顺序；group="all" 不分组、全局混排。
    dir_sql = "DESC" if str(order).lower() == "desc" else "ASC"
    order_sql = _order_sql(sort, dir_sql, group)
    with tx() as conn:
        total = conn.execute(f"SELECT COUNT(*) n FROM projects {sql_where}", args).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM projects {sql_where} {order_sql} LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size],
        ).fetchall()
    return {"total": total, "page": page, "page_size": page_size,
            "rows": [dict(r) for r in rows]}


def all_projects(ptype: str = "") -> list:
    where, args = ("WHERE ptype=?" if ptype else ""), ([ptype] if ptype else [])
    order = ("ORDER BY CASE ptype WHEN 'real' THEN 0 WHEN 'urgent' THEN 1 "
             "WHEN 'stock' THEN 2 ELSE 3 END, src_row, id")
    with tx() as conn:
        rows = conn.execute(
            f"SELECT * FROM projects {where} {order}", args
        ).fetchall()
    return [dict(r) for r in rows]


def get_project(pid: int):
    with tx() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def get_project_by_code(ptype: str, code: str, extra: str = ""):
    with tx() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE ptype=? AND code=? AND disc=?",
            (ptype, code, str(extra or "").strip() if ptype != "repair" else (str(extra or "").strip() or "")),
        ).fetchone()
    return dict(row) if row else None


def find_by_codes(ptype: str, codes: list) -> list:
    """按类型 + 编号批量取，保持传入编号的顺序；同一编号多条按 src_row 排序。

    编号匹配**忽略大小写**（库里存的都是大写，用户可能填小写），
    返回的 code 统一为库中的原始大写形式。
    """
    if not codes:
        return []
    with tx() as conn:
        found = {}
        for chunk_start in range(0, len(codes), 500):
            chunk = [str(c) for c in codes[chunk_start:chunk_start + 500]]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"""SELECT * FROM projects
                    WHERE ptype=? AND UPPER(code) IN ({marks})
                    ORDER BY src_row, id""",
                [ptype] + [c.upper() for c in chunk],
            ).fetchall()
            for r in rows:
                # 以库中 code 的大写形式作为键，方便下面按用户输入匹配
                found.setdefault(str(r["code"]).upper(), []).append(dict(r))
    result = []
    for code in codes:
        for row in found.get(str(code).strip().upper(), []):
            result.append(row)
    return result


def _disc_of(payload: dict) -> str:
    """分组标识：同类型 + 同编号下，用「返修要求 / 报价说明」把多行分成若干组。

    注意：**不再把「内容相同」当成同一条**。同一项目返修多次、要求文字一样
    （例如都是「补实验」）时是两条真实记录，靠 seq 区分并存。
    """
    return str(payload.get("extra") or "").strip()


def _default_seq(conn, ptype: str, code: str, disc: str,
                 payload: dict) -> int:
    """手工新增单条时决定 seq：显式给了就用给的，否则接在该组已有行后面。"""
    if payload.get("seq") not in (None, ""):
        try:
            return int(payload["seq"])
        except (TypeError, ValueError):
            pass
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), -1) AS m FROM projects WHERE ptype=? AND code=? AND disc=?",
        (ptype, code, disc),
    ).fetchone()
    return int(row["m"]) + 1


def upsert_projects_bulk(rows: list) -> tuple:
    """批量 upsert（单连接单事务）。

    判存键 = (ptype, code, disc, seq)。导入时 seq 由 importer 按源表里
    「同编号+同内容」的第几次出现算出，因此：
      - 源表里两行完全相同的内容 → seq 不同 → 两行都保留；
      - 同一份文件重复导入 → seq 相同 → 覆盖更新，不会翻倍。
    返回 (created, updated)。
    """
    if not rows:
        return 0, 0

    # 先算出每行的 fields（复用单条逻辑的构造方式，保证字段一致）
    prepared = []
    for payload in rows:
        ptype = (payload.get("ptype") or "").strip()
        code = str(payload.get("code") or "").strip()
        if not ptype or not code:
            continue
        disc = _disc_of(payload)
        try:
            seq = int(payload.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        prepared.append((ptype, code, disc, seq, {
            "name": str(payload.get("name") or "").strip(),
            "src_row": int(payload.get("src_row") or 0),
            "cell": str(payload.get("cell") or ""), "mol": str(payload.get("mol") or ""),
            "path": str(payload.get("path") or ""), "animal": str(payload.get("animal") or ""),
            "extra": str(payload.get("extra") or ""),
            "price": float(payload.get("price") or 0),
            "audit_sun": str(payload.get("audit_sun") or ""),
            "audit_xu": str(payload.get("audit_xu") or ""),
            "send_date": str(payload.get("send_date") or ""),
            "note": str(payload.get("note") or ""),
        }))
    if not prepared:
        return 0, 0

    created = updated = 0
    now = _now()
    cols = ("name", "src_row", "cell", "mol", "path", "animal",
            "extra", "price", "audit_sun", "audit_xu", "send_date", "note")
    with tx() as conn:
        for ptype, code, disc, seq, fields in prepared:
            row = conn.execute(
                "SELECT id FROM projects WHERE ptype=? AND code=? AND disc=? AND seq=?",
                (ptype, code, disc, seq),
            ).fetchone()
            if row:
                sets = ", ".join(f"{k}=?" for k in cols)
                conn.execute(
                    f"UPDATE projects SET {sets}, updated_at=? WHERE id=?",
                    [fields[k] for k in cols] + [now, row["id"]],
                )
                updated += 1
            else:
                collist = ", ".join(("ptype", "code", "disc", "seq") + cols + ("updated_at",))
                marks = ", ".join("?" * (4 + len(cols) + 1))
                conn.execute(
                    f"INSERT INTO projects ({collist}) VALUES ({marks})",
                    [ptype, code, disc, seq] + [fields[k] for k in cols] + [now],
                )
                created += 1
    return created, updated


def upsert_project(payload: dict) -> int:
    """手工新增/更新单条。

    - 带 seq（或库里已有同组行）→ 按 (ptype, code, disc, seq) 定位；
    - 未带 seq 且该组已存在 → seq 取组内最大值 +1，**新增一次出现**，
      而不是覆盖旧行（同一项目多次返修/多次报价都必须留下）。
      想改已有那条，请走 PUT /api/projects/<id> 的编辑弹窗。
    """
    ptype = (payload.get("ptype") or "").strip()
    code = str(payload.get("code") or "").strip()
    if not ptype or not code:
        raise ValueError("项目类型与项目编号不能为空")
    disc = _disc_of(payload)
    fields = {
        "ptype": ptype, "code": code, "disc": disc,
        "name": str(payload.get("name") or "").strip(),
        "src_row": int(payload.get("src_row") or 0),
        "cell": str(payload.get("cell") or ""), "mol": str(payload.get("mol") or ""),
        "path": str(payload.get("path") or ""), "animal": str(payload.get("animal") or ""),
        "extra": str(payload.get("extra") or ""),
        "price": float(payload.get("price") or 0),
        "audit_sun": str(payload.get("audit_sun") or ""),
        "audit_xu": str(payload.get("audit_xu") or ""),
        "send_date": str(payload.get("send_date") or ""),
        "note": str(payload.get("note") or ""),
    }
    with tx() as conn:
        # 手工新增且没带源表行号：排到该类型末尾（src_row=0 会冲到列表最前面，
        # 让人误以为「没入库」或「排序乱了」）
        if not fields["src_row"]:
            mx = conn.execute(
                "SELECT COALESCE(MAX(src_row), 0) AS m FROM projects WHERE ptype=?",
                (ptype,),
            ).fetchone()["m"]
            fields["src_row"] = int(mx) + 1
        seq = _default_seq(conn, ptype, code, disc, payload)
        row = conn.execute(
            "SELECT id FROM projects WHERE ptype=? AND code=? AND disc=? AND seq=?",
            (ptype, code, disc, seq),
        ).fetchone()
        if row:
            sets = ", ".join(f"{k}=?" for k in fields if k not in ("ptype", "code", "disc"))
            args = [fields[k] for k in fields if k not in ("ptype", "code", "disc")]
            conn.execute(
                f"UPDATE projects SET {sets}, updated_at=? WHERE id=?",
                args + [_now(), row["id"]],
            )
            return row["id"]
        keys = list(fields.keys()) + ["seq"]
        conn.execute(
            f"INSERT INTO projects({','.join(keys)},created_at,updated_at) "
            f"VALUES({','.join('?' * len(keys))},?,?)",
            [fields[k] for k in fields] + [seq, _now(), _now()],
        )
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def update_project(pid: int, payload: dict) -> None:
    """更新项目。

    **允许修改「归属类型」(ptype) 与「编号」(code)**：项目从库存转加急这类
    场景不需要删掉重建。换组时自动重算 seq，避开唯一键 (ptype,code,disc,seq)
    冲突；月末填报里引用了该编号的记录也一并跟着换类型/编号（冲突则跳过）。
    """
    allowed = ["name", "cell", "mol", "path", "animal", "extra", "price",
               "audit_sun", "audit_xu", "send_date", "note"]
    with tx() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            return
        fields = {k: payload[k] for k in allowed if k in payload}

        new_ptype = str(payload.get("ptype") or row["ptype"]).strip() or row["ptype"]
        new_code = str(payload.get("code") or row["code"]).strip() or row["code"]
        # extra 就是 disc（分组标识），改了它等于换了分组
        new_disc = str(fields.get("extra", row["disc"]) or "").strip()

        seq = row["seq"]
        if (new_ptype, new_code, new_disc) != (row["ptype"], row["code"], row["disc"]):
            mx = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) AS m FROM projects "
                "WHERE ptype=? AND code=? AND disc=?",
                (new_ptype, new_code, new_disc),
            ).fetchone()["m"]
            seq = int(mx) + 1

        sets = ", ".join(f"{k}=?" for k in fields)
        args = list(fields.values())
        sets = (sets + ", " if sets else "") + "ptype=?, code=?, disc=?, seq=?"
        args += [new_ptype, new_code, new_disc, seq]
        conn.execute(
            f"UPDATE projects SET {sets}, updated_at=? WHERE id=?",
            args + [_now(), pid],
        )

        # 月末填报按 (月, 平台, 类型, 编号) 关联；类型/编号变了要跟着走，
        # 否则历史填报会变成「查不到对应项目」的孤儿记录。
        if (new_ptype, new_code) != (row["ptype"], row["code"]):
            conn.execute(
                "UPDATE OR IGNORE month_entries SET ptype=?, code=? "
                "WHERE ptype=? AND code=?",
                (new_ptype, new_code, row["ptype"], row["code"]),
            )


def delete_projects(ids: list) -> int:
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return 0
    with tx() as conn:
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM projects WHERE id IN ({marks})", ids)
    return len(ids)


def clear_type(ptype: str = "") -> int:
    with tx() as conn:
        if ptype:
            n = conn.execute("SELECT COUNT(*) n FROM projects WHERE ptype=?", (ptype,)).fetchone()["n"]
            conn.execute("DELETE FROM projects WHERE ptype=?", (ptype,))
        else:
            n = conn.execute("SELECT COUNT(*) n FROM projects").fetchone()["n"]
            conn.execute("DELETE FROM projects")
    return n


# ------------------------------------------------------------------ 统计
def _pct(done: int, total: int) -> float:
    return round(done * 100.0 / total, 1) if total else 0.0


def platform_val(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def platform_designed(row: dict, key: str) -> bool:
    """该项目是否设计了该平台的实验：只要不是「/」就算设计了（空白=设计了但未完成）。"""
    return platform_val(row, key) != ST_NONE


def platform_finished(row: dict, key: str) -> bool:
    """该平台实验是否已完成：必须明确为 ✅。"""
    return platform_val(row, key) == ST_DONE


def project_status(row: dict) -> dict:
    """项目整体状态。
    参与平台 = 所有「设计了」的平台（值 ≠ "/"，含空白）；
    全部参与平台 ✅ → 已完成；否则未完成。
    返回 {designed, done, total_designed, completed}
    """
    designed = [k for k, _ in PLATFORMS if platform_designed(row, k)]
    done = [k for k in designed if platform_finished(row, k)]
    return {
        "designed": designed,
        "done": done,
        "total_designed": len(designed),
        "completed": len(designed) > 0 and len(designed) == len(done),
    }


def dashboard() -> dict:
    with tx() as conn:
        rows = conn.execute("SELECT * FROM projects").fetchall()
    rows = [dict(r) for r in rows]
    by_type = {}
    for key, label in TYPES:
        sub = [r for r in rows if r["ptype"] == key]
        by_type[key] = {
            "key": key, "label": label, "count": len(sub),
            "price": round(sum(r["price"] or 0 for r in sub), 2),
            "done": sum(1 for r in sub if project_status(r)["completed"]),
        }
    # 各平台完成情况：
    #   total   = 设计了该平台的项目数（值 ≠ "/"，含空白）
    #   done    = 该平台 ✅
    #   undone  = 该平台 ❌ 或 空白（设计了但没完成）
    #   none    = 该平台 "/"（未设计）—— 仅作展示，不计入完成率
    platforms = []
    for key, label in PLATFORMS:
        designed = [r for r in rows if platform_designed(r, key)]
        done = sum(1 for r in designed if platform_finished(r, key))
        undone = len(designed) - done
        none = sum(1 for r in rows if platform_val(r, key) == ST_NONE)
        platforms.append({
            "key": key, "label": label, "total": len(designed),
            "done": done, "undone": undone, "none": none,
            "pct": _pct(done, len(designed)),
        })
    total_price = round(sum(r["price"] or 0 for r in rows), 2)
    return {
        "total": len(rows),
        "total_price": total_price,
        "by_type": [by_type[k] for k, _ in TYPES],
        "platforms": platforms,
    }


def _platform_done(row: dict) -> int:
    """该记录已完成的平台数（供兼容调用）。"""
    return sum(1 for key, _ in PLATFORMS if platform_finished(row, key))


# ------------------------------------------------------------------ 设置
DEFAULT_SETTINGS = {
    "export_dir": "",
    "company_name": "",
}


def get_settings() -> dict:
    with tx() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    data = dict(DEFAULT_SETTINGS)
    data.update({r["key"]: r["value"] for r in rows})
    return data


def update_settings(payload: dict) -> dict:
    with tx() as conn:
        for k, v in payload.items():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, "" if v is None else str(v)),
            )
    return get_settings()


def set_meta(key: str, value: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_meta(key: str, default: str = "") -> str:
    with tx() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def data_info() -> dict:
    info = {"data_dir": get_data_dir(), "db_path": get_db_path()}
    try:
        info["db_size"] = os.path.getsize(get_db_path())
    except OSError:
        info["db_size"] = 0
    with tx() as conn:
        info["total"] = conn.execute("SELECT COUNT(*) n FROM projects").fetchone()["n"]
    info["source_file"] = get_meta("source_file", "")
    info["last_import"] = get_meta("last_import", "")
    return info


# ================================================================== 月末项目统计
def _month_public(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "year": row.get("year") or 0,
        "month": row.get("month") or 0,
        "status": row.get("status") or "open",
        "status_name": {"open": "填报中", "locked": "已锁定"}.get(row.get("status"), "填报中"),
        "created_at": row.get("created_at") or "",
        "exported_at": row.get("exported_at") or "",
    }


def list_months() -> list:
    with tx() as conn:
        rows = conn.execute(
            "SELECT * FROM months ORDER BY year DESC, month DESC, id DESC"
        ).fetchall()
    return [_month_public(dict(r)) for r in rows]


def get_month(mid: int):
    with tx() as conn:
        row = conn.execute("SELECT * FROM months WHERE id=?", (mid,)).fetchone()
    return _month_public(dict(row)) if row else None


def create_month(name: str, year: int = 0, month: int = 0, created_by: int = 0) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("月份名称不能为空")
    with tx() as conn:
        if conn.execute("SELECT 1 FROM months WHERE name=?", (name,)).fetchone():
            raise ValueError(f"「{name}」已存在")
        cur = conn.execute(
            "INSERT INTO months(name,year,month,status,created_by,created_at,exported_at) "
            "VALUES(?,?,?,?,?,?,'')",
            (name, int(year or 0), int(month or 0), "open", created_by,
             _now()),
        )
        return _month_public(dict(conn.execute(
            "SELECT * FROM months WHERE id=?", (cur.lastrowid,)).fetchone()))


def delete_month(mid: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM month_entries WHERE month_id=?", (mid,))
        conn.execute("DELETE FROM months WHERE id=?", (mid,))


def update_month(mid: int, name=None, year=None, month=None) -> dict:
    """改月份名称 / 年份 / 月份（月份建错了不必删库重建）。

    - 只更新显式传入的字段（None = 保持原值），所以「只改名」或「只改年月」都能用。
    - 名称仍受 UNIQUE(name) 约束；已填写的填报明细（month_entries）完全不动。
    """
    with tx() as conn:
        row = conn.execute("SELECT * FROM months WHERE id=?", (mid,)).fetchone()
        if not row:
            raise ValueError("该月份不存在")
        sets, args = [], []
        if name is not None:
            nm = str(name).strip()
            if not nm:
                raise ValueError("月份名称不能为空")
            if conn.execute("SELECT id FROM months WHERE name=? AND id<>?",
                            (nm, mid)).fetchone():
                raise ValueError(f"「{nm}」已存在")
            sets.append("name=?")
            args.append(nm)
        if year is not None and str(year).strip() != "":
            try:
                yv = int(year)
            except (TypeError, ValueError):
                raise ValueError("年份不合法")
            if yv and not (1900 <= yv <= 2200):
                raise ValueError("年份需在 1900-2200 之间")
            sets.append("year=?")
            args.append(yv)
        if month is not None and str(month).strip() != "":
            try:
                mv = int(month)
            except (TypeError, ValueError):
                raise ValueError("月份不合法")
            if mv and not (1 <= mv <= 12):
                raise ValueError("月份需在 1-12 之间")
            sets.append("month=?")
            args.append(mv)
        if not sets:
            return _month_public(dict(row))
        args.append(mid)
        conn.execute(f"UPDATE months SET {', '.join(sets)} WHERE id=?", args)
        return _month_public(dict(conn.execute(
            "SELECT * FROM months WHERE id=?", (mid,)).fetchone()))


def set_month_status(mid: int, status: str) -> None:
    if status not in ("open", "locked"):
        raise ValueError("状态不合法")
    with tx() as conn:
        conn.execute("UPDATE months SET status=? WHERE id=?", (status, mid))


def touch_month_exported(mid: int) -> None:
    with tx() as conn:
        conn.execute("UPDATE months SET exported_at=? WHERE id=?", (_now(), mid))


def list_entries(mid: int, platform: str = "", ptype: str = "") -> list:
    where, args = ["month_id=?"], [mid]
    if platform:
        where.append("platform=?")
        args.append(platform)
    if ptype:
        where.append("ptype=?")
        args.append(ptype)
    with tx() as conn:
        rows = conn.execute(
            f"SELECT * FROM month_entries WHERE {' AND '.join(where)} "
            "ORDER BY platform, id", args
        ).fetchall()
    return [dict(r) for r in rows]


def add_entry(mid: int, platform: str, ptype: str, code: str, user_id: int = 0) -> int:
    platform = (platform or "").strip()
    ptype = (ptype or "").strip()
    code = str(code or "").strip()
    if platform not in PLATFORM_KEYS:
        raise ValueError("平台不合法")
    if ptype not in TYPE_NAME:
        raise ValueError("项目类型不合法")
    if not code:
        raise ValueError("项目编号不能为空")
    with tx() as conn:
        row = conn.execute(
            "SELECT id FROM month_entries WHERE month_id=? AND platform=? AND ptype=? AND code=?",
            (mid, platform, ptype, code)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO month_entries(month_id,platform,ptype,code,user_id,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (mid, platform, ptype, code, user_id, _now()))
        return cur.lastrowid


def add_entries(mid: int, platform: str, ptype: str, codes: list, user_id: int = 0) -> dict:
    """批量添加 / 更新（同编号同平台同类型视为同一条）。

    编号统一转大写（项目库编号均为大写），小写输入也能与已有记录正确去重。
    """
    codes = [(str(c).strip().upper()) for c in (codes or []) if str(c).strip()]
    added = 0
    skipped = []
    seen = set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        with tx() as conn:
            dup = conn.execute(
                "SELECT 1 FROM month_entries WHERE month_id=? AND platform=? AND ptype=? "
                "AND UPPER(code)=?",
                (mid, platform, ptype, code)).fetchone()
        if dup:
            continue
        try:
            add_entry(mid, platform, ptype, code, user_id)
            added += 1
        except Exception:  # noqa: BLE001
            skipped.append(code)
    return {"added": added, "skipped": skipped}


def sync_entries(mid: int, platform: str, groups: list, user_id: int = 0) -> dict:
    """把某月份、某平台的填报**整体同步**成提交的内容（月末「编辑编号」弹窗用）。

    与逐条增删的差别：这是「所见即所得」的整体替换 ——
      · 提交里没有的旧编号 → 删除；
      · 编号还在但换了类型 → **原地改 ptype**（记录 id 不变，填报人不变）；
      · 新出现的编号 → 新增；
      · 什么都没变的 → 原样保留。

    groups = [(ptype, [code, ...]), ...]，本次提交的全部编号（四类一起给）。
    前端弹窗会把该平台四类现有编号**全部回填**，所以某个类型框留空 = 该类型下没有编号。
    ※ 同一编号出现在两个类型下会各记一条（唯一键含 ptype），这是允许的。
    """
    platform = (platform or "").strip()
    if platform not in PLATFORM_KEYS:
        raise ValueError("平台不合法")

    targets, seen, by_type = [], set(), {}
    for ptype, codes in groups or []:
        ptype = (ptype or "").strip()
        if ptype not in TYPE_NAME:
            raise ValueError("项目类型不合法")
        for c in codes or []:
            code = str(c).strip().upper()
            if not code or (ptype, code) in seen:
                continue
            seen.add((ptype, code))
            targets.append((ptype, code))
            by_type[ptype] = by_type.get(ptype, 0) + 1

    added, moved, removed, kept = [], [], [], 0
    with tx() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM month_entries WHERE month_id=? AND platform=? ORDER BY id",
            (mid, platform))]
        pool = {}
        for r in rows:
            pool.setdefault(str(r["code"]).upper(), []).append(r)

        used = set()
        for ptype, code in targets:
            cands = pool.get(code) or []
            pick = None
            for r in cands:            # 优先同类型那一条：单纯改名不算「改归类」
                if r["id"] not in used and r["ptype"] == ptype:
                    pick = r
                    break
            if pick is None:           # 退一步：同编号但类型变了 → 原地改 ptype
                for r in cands:
                    if r["id"] not in used:
                        pick = r
                        break
            if pick is None:           # 全新的编号
                conn.execute(
                    "INSERT INTO month_entries(month_id,platform,ptype,code,user_id,updated_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (mid, platform, ptype, code, user_id, _now()))
                added.append(code)
            else:
                used.add(pick["id"])
                if pick["ptype"] != ptype:
                    conn.execute(
                        "UPDATE month_entries SET ptype=?, updated_at=? WHERE id=?",
                        (ptype, _now(), pick["id"]))
                    moved.append({"code": code, "from": pick["ptype"], "to": ptype})
                else:
                    kept += 1

        for r in rows:
            if r["id"] not in used:
                conn.execute("DELETE FROM month_entries WHERE id=?", (r["id"],))
                removed.append({"code": r["code"], "ptype": r["ptype"]})

    return {"added": len(added), "added_codes": added,
            "moved": len(moved), "moved_items": moved,
            "removed": len(removed), "removed_items": removed,
            "kept": kept, "total": len(targets), "by_type": by_type}


def delete_entry(eid: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM month_entries WHERE id=?", (eid,))


def update_entry(eid: int, platform: str = "", ptype: str = "", code: str = "") -> dict:
    """改一条填报记录的**编号 / 类型 / 平台**（月末统计里拖动归类、弹窗再编辑都用它）。

    - 三个参数留空表示保持原值，所以「只换类型」「只换平台」「只改编号」都能用。
    - 与原记录完全一致时原样返回（unchanged）。
    - 目标位置**已存在**同编号记录时不覆盖、不重复插入，返回 conflict=True，
      由前端提示用户，绝不静默丢数据。
    """
    with tx() as conn:
        row = conn.execute("SELECT * FROM month_entries WHERE id=?", (eid,)).fetchone()
        if not row:
            raise ValueError("记录不存在")
        row = dict(row)
        newp = (platform or row["platform"] or "").strip()
        newt = (ptype or row["ptype"] or "").strip()
        # 编号统一大写（项目库编号均为大写，小写输入也能正确判重）
        newc = str(code or row["code"] or "").strip().upper()
        if not newc:
            raise ValueError("项目编号不能为空")
        if newp not in PLATFORM_KEYS:
            raise ValueError("平台不合法")
        if newt not in TYPE_NAME:
            raise ValueError("项目类型不合法")
        if (newp == row["platform"] and newt == row["ptype"]
                and newc == str(row["code"]).upper()):
            return {"moved": False, "unchanged": True, "entry": row}
        dup = conn.execute(
            "SELECT id FROM month_entries WHERE month_id=? AND platform=? AND ptype=? "
            "AND UPPER(code)=? AND id<>?",
            (row["month_id"], newp, newt, newc, eid)).fetchone()
        if dup:
            return {"moved": False, "conflict": True, "conflict_id": dup["id"],
                    "entry": row, "target": {"platform": newp, "ptype": newt, "code": newc}}
        conn.execute(
            "UPDATE month_entries SET platform=?, ptype=?, code=?, updated_at=? WHERE id=?",
            (newp, newt, newc, _now(), eid))
        new_row = dict(conn.execute(
            "SELECT * FROM month_entries WHERE id=?", (eid,)).fetchone())
        return {
            "moved": True, "entry": new_row,
            "from": {"platform": row["platform"], "ptype": row["ptype"], "code": row["code"]},
        }


def clear_entries(mid: int, platform: str = "", ptype: str = "") -> int:
    where, args = ["month_id=?"], [mid]
    if platform:
        where.append("platform=?")
        args.append(platform)
    if ptype:
        where.append("ptype=?")
        args.append(ptype)
    with tx() as conn:
        rows = conn.execute(
            f"SELECT id FROM month_entries WHERE {' AND '.join(where)}", args).fetchall()
        conn.execute(f"DELETE FROM month_entries WHERE {' AND '.join(where)}", args)
    return len(rows)


def month_detail(mid: int) -> dict:
    """某月份按平台分组 + 关联项目库信息。"""
    month = get_month(mid)
    if not month:
        return {}
    entries = list_entries(mid)
    # 项目库索引：(ptype, 大写编号) -> 记录（同编号多条取第一条）
    all_rows = all_projects()
    index = {}
    for r in all_rows:
        index.setdefault((r["ptype"], str(r["code"]).upper()), r)
    platforms = []
    total = 0
    for pkey, plabel in PLATFORMS:
        sub = [e for e in entries if e["platform"] == pkey]
        items = []
        for e in sub:
            proj = index.get((e["ptype"], str(e["code"]).upper()))
            items.append({
                "id": e["id"],
                "ptype": e["ptype"],
                "type_label": TYPE_NAME.get(e["ptype"], e["ptype"]),
                "code": e["code"],
                "name": (proj or {}).get("name") or "",
                "price": (proj or {}).get("price") or 0,
                "matched": bool(proj),
                "updated_at": e.get("updated_at") or "",
            })
        total += len(items)
        platforms.append({
            "key": pkey, "label": plabel, "count": len(items), "items": items,
        })
    return {"month": month, "platforms": platforms, "total": total}


def save_export_record(mid: int, user_id: int, filename: str, path: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO exports(month_id,user_id,filename,path,created_at) "
            "VALUES(?,?,?,?,?)", (mid, user_id, filename, path, _now()))


def list_exports(mid: int = 0) -> list:
    with tx() as conn:
        if mid:
            rows = conn.execute(
                "SELECT * FROM exports WHERE month_id=? ORDER BY id DESC", (mid,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM exports ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]



# ================================================================== 工作日志
# 个人私有：所有读 / 改 / 删都强制带 user_id 过滤，调用方（server）必须传自己的 id。
def _log_public(row: dict) -> dict:
    return {
        "id": row["id"],
        "date": row.get("log_date") or "",
        "title": row.get("title") or "",
        "content": row.get("content") or "",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    }


def add_worklog(user_id: int, log_date: str, title: str, content: str) -> int:
    """新增一条日志，返回 id。log_date 为空时回落当天。"""
    ts = _now()
    d = (log_date or "").strip() or datetime.now().strftime("%Y-%m-%d")
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO worklogs(user_id,log_date,title,content,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, d, (title or "").strip(), (content or "").strip(), ts, ts))
        return cur.lastrowid


def get_worklog(lid: int, user_id: int):
    """取自己的某条日志（对外字段已归一化）。带 user_id → 取不到别人的。"""
    with tx() as conn:
        row = conn.execute(
            "SELECT * FROM worklogs WHERE id=? AND user_id=?", (lid, user_id)).fetchone()
    return _log_public(dict(row)) if row else None


def list_worklogs(user_id: int, month: str = "", keyword: str = "") -> list:
    """某用户的日志列表。month = 'YYYY-MM' 时按月筛选。**永远限定 user_id**。"""
    where, args = ["user_id=?"], [user_id]
    m = (month or "").strip()
    if m:
        where.append("substr(log_date,1,7)=?")
        args.append(m)
    kw = (keyword or "").strip()
    if kw:
        where.append("(title LIKE ? OR content LIKE ?)")
        args.extend([f"%{kw}%", f"%{kw}%"])
    sql = ("SELECT * FROM worklogs WHERE " + " AND ".join(where) +
           " ORDER BY log_date DESC, id DESC")
    with tx() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_log_public(dict(r)) for r in rows]


def update_worklog(lid: int, user_id: int, payload: dict) -> bool:
    """更新自己的日志。带 user_id 过滤 → 改不到别人的。"""
    sets, args = [], []
    if "date" in payload:
        v = (payload.get("date") or "").strip()
        if v:
            sets.append("log_date=?")
            args.append(v)
    if "title" in payload:
        sets.append("title=?")
        args.append((payload.get("title") or "").strip())
    if "content" in payload:
        sets.append("content=?")
        args.append((payload.get("content") or "").strip())
    if not sets:
        return False
    sets.append("updated_at=?")
    args.append(_now())
    args.extend([lid, user_id])
    with tx() as conn:
        cur = conn.execute(
            f"UPDATE worklogs SET {', '.join(sets)} WHERE id=? AND user_id=?", args)
        return cur.rowcount > 0


def delete_worklog(lid: int, user_id: int) -> bool:
    with tx() as conn:
        cur = conn.execute("DELETE FROM worklogs WHERE id=? AND user_id=?", (lid, user_id))
        return cur.rowcount > 0


def worklog_summary(user_id: int, year: int, month: int) -> dict:
    """统计某用户某月的日志：总条数 + 按日分布 + 有日志的天数。"""
    prefix = f"{int(year):04d}-{int(month):02d}"
    with tx() as conn:
        rows = conn.execute(
            "SELECT log_date AS d, COUNT(*) AS n FROM worklogs "
            "WHERE user_id=? AND substr(log_date,1,7)=? "
            "GROUP BY log_date ORDER BY log_date",
            (user_id, prefix)).fetchall()
    days = [{"date": r["d"], "count": r["n"]} for r in rows]
    return {
        "year": int(year), "month": int(month),
        "label": f"{int(year)}年{int(month)}月",
        "total": sum(d["count"] for d in days),
        "active_days": len(days),
        "days": days,
    }


def worklog_months(user_id: int) -> list:
    """该用户有日志的月份列表（倒序），供前端下拉筛选。"""
    with tx() as conn:
        rows = conn.execute(
            "SELECT substr(log_date,1,7) AS m, COUNT(*) AS n FROM worklogs "
            "WHERE user_id=? GROUP BY m ORDER BY m DESC", (user_id,)).fetchall()
    return [{"month": r["m"], "count": r["n"]} for r in rows]
