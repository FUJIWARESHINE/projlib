# -*- coding: utf-8 -*-
"""数据层：SQLite 连接、建表、项目库 CRUD、统计。"""
from __future__ import annotations

import os
import re
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

# 项目编号前缀（二十八期）：项目库里的编号一律是「字母前缀 + 数字」，如 A539 / A1000。
# 实验员填报时若只写了数字（539）就会被拦下来，提醒补全前缀。
# 规则只要求**以字母开头**（A、B、C… 都行，不写死某个字母），
# 因为后续可能新增 B/C 系列编号，写死 A 反而会误伤。
CODE_PREFIX_RE = re.compile(r"^[A-Za-z]")


def has_code_prefix(code) -> bool:
    """编号是否带字母前缀（A539 → True，539 → False，空 → False）。"""
    return bool(CODE_PREFIX_RE.match(str(code or "").strip()))


def codes_without_prefix(codes) -> list:
    """挑出没有字母前缀的编号（去重、转大写、保持原顺序），供前后端提示用。"""
    seen, out = set(), []
    for c in codes or []:
        s = str(c or "").strip()
        if not s or has_code_prefix(s):
            continue
        key = s.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out

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

-- 站内通知（消息中心）：按「角色」广播，已读状态按用户存。
--   target_role='user'   → 发给所有普通账户（管理员新增项目入库时）
--   target_role='admin'  → 发给所有管理员（实验员勾选平台完成 / 项目结项时）
--   target_platform      → 按平台再收窄一层受众（**仅全服通告用**，见下）
--   popup=1 → 目标用户登录后弹「闪烁流光横幅」；popup=0 → 只在消息中心显示未读。
-- 受众 = target_role 命中的角色 **且** 平台命中 target_platform：
--   ''            → 不限制平台（对所有普通账户可见，历史通知都是这个值）
--   'cell,mol'    → 只有 cell / mol 平台的用户可见（逗号分隔，多选）
--   ⚠️ 管理员 platform 为空，所以按平台定向的通告管理员自己看不到 —— 这是刻意的：
--      通告是发给实验员干活的，管理员通过「全服通告」页面查看已发记录即可。
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,                 -- project_in 项目入库 / platform_done 平台完成 / project_done 项目结项
  target_role TEXT NOT NULL DEFAULT 'user',
  target_platform TEXT DEFAULT '',        -- 受众平台（逗号分隔，'' = 不限平台）；全服通告用
  title TEXT NOT NULL DEFAULT '',         -- 主文案（消息中心与横幅都用它）
  body TEXT DEFAULT '',                   -- 详情（编号清单等）
  ptype TEXT DEFAULT '',                  -- 关联项目类型
  code TEXT DEFAULT '',                   -- 关联项目编号
  platform TEXT DEFAULT '',               -- 关联平台（平台完成用）
  project_id INTEGER DEFAULT 0,           -- 关联项目 id
  popup INTEGER NOT NULL DEFAULT 0,       -- 1 = 登录后弹横幅
  created_by INTEGER DEFAULT 0,           -- 触发人（管理员/实验员 id）
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_notif_role ON notifications(target_role, id);

CREATE TABLE IF NOT EXISTS notification_reads(
  notification_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  read_at TEXT,
  hidden INTEGER NOT NULL DEFAULT 0,      -- 1 = 该用户已把这条从自己的消息中心清掉
  PRIMARY KEY(notification_id, user_id)
);

-- 结项待审核：项目所有被设计平台都完成后生成一条，由「有审核权限的普通账户」复核。
--   审核完成后 status='done'，并通知全体管理员「某某某已审核完成，请复审」。
--   同一项目再次结项（先取消再勾回来）会把这条重置成 pending。
CREATE TABLE IF NOT EXISTS project_audits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ptype TEXT NOT NULL,
  code TEXT NOT NULL,
  project_id INTEGER DEFAULT 0,
  name TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',   -- pending 待审核 / done 已审核
  auditor_id INTEGER DEFAULT 0,
  auditor_name TEXT DEFAULT '',
  created_at TEXT,
  reviewed_at TEXT,
  UNIQUE(ptype, code)
);
CREATE INDEX IF NOT EXISTS ix_audits_status ON project_audits(status, id);
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
    """老库升级。

    ① 补 projects.seq 列，并把唯一索引从 (ptype,code,disc) 换成带 seq 的四列。
       旧索引会阻止「同编号 + 同内容」的多行并存（同一项目多次返修会被吞掉），
       所以必须换掉；存量数据 seq 默认 0，组内本来就只有一行，迁移后依然唯一。
    ② 补 users.can_audit 列（审核权限，2026-10-14 新增）。
    ③ 补 notification_reads.hidden 列（「清空消息中心」按人隐藏，2026-10-14 新增）。
    ④ 补 notifications.target_platform 列（全服通告按平台定向，2026-10-14 新增）。
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
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if ucols and "can_audit" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN can_audit INTEGER DEFAULT 0")
    ncols = {r["name"] for r in conn.execute("PRAGMA table_info(notification_reads)")}
    if ncols and "hidden" not in ncols:
        conn.execute("ALTER TABLE notification_reads ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(notifications)")}
    if tcols and "target_platform" not in tcols:
        conn.execute("ALTER TABLE notifications ADD COLUMN target_platform TEXT DEFAULT ''")


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
    返回 (created, updated, created_items)：
      created_items = [{"ptype":..., "code":...}, ...] 本次**新增**的行，
      供上层（导入接口）生成「xx项目已入库」通知。
    """
    if not rows:
        return 0, 0, []

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
        return 0, 0, []

    created = updated = 0
    created_items = []
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
                created_items.append({"ptype": ptype, "code": code, "name": fields["name"]})
    return created, updated, created_items


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


# ================================================================== 消息中心 / 站内通知
# 规则（2026-10-14 与用户确认）：
#   · 管理员新增「真实 / 加急 / 返修」项目 → 通知所有普通账户，登录时弹流光横幅；
#     「库存」项目只进消息中心（未读），不弹横幅。
#   · 普通账户把某项目某平台勾成 ✅ → 通知所有管理员；
#     若该项目所有被设计平台都完成 → 再补一条「已结项，请审核」。
# 已读状态按用户存（notification_reads），所以「谁看过」互不影响。
NOTIF_PROJECT_IN = "project_in"        # 项目入库
NOTIF_PLATFORM_DONE = "platform_done"  # 平台完成
NOTIF_PROJECT_DONE = "project_done"    # 项目结项（待审核）
NOTIF_AUDIT_REQUEST = "audit_request"  # 结项待审核（发给有审核权限的普通账户）
NOTIF_AUDIT_DONE = "audit_done"        # 审核完成（发给管理员）
NOTIF_BROADCAST = "broadcast"          # 全服通告（管理员手动发布，可按平台定向）
NOTIF_CATEGORY_NAME = {
    NOTIF_PROJECT_IN: "项目入库",
    NOTIF_PLATFORM_DONE: "平台完成",
    NOTIF_PROJECT_DONE: "项目结项",
    NOTIF_AUDIT_REQUEST: "待审核",
    NOTIF_AUDIT_DONE: "审核完成",
    NOTIF_BROADCAST: "全服通告",
}
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_AUDITOR = "auditor"   # 不是真实角色，只是「审核员」这一受众标签

# 平台多选上限：四条实验线 + 运营 / 设计（全服通告可选「全体成员」= 传空列表）
BROADCAST_PLATFORM_KEYS = list(PLATFORM_KEYS) + ["ops", "design"]


def norm_target_platform(platforms) -> str:
    """把前端传来的平台列表归一成逗号分隔串（去重、按 SIGNUP_OPTIONS 顺序、非法值丢弃）。

    [] / None / 含未知键 → 返回 ""（= 不限平台，对全体普通账户可见）。
    """
    if not platforms:
        return ""
    if isinstance(platforms, str):
        platforms = [p for p in platforms.replace("，", ",").split(",")]
    picked = {str(p or "").strip().lower() for p in platforms}
    out = [k for k in BROADCAST_PLATFORM_KEYS if k in picked]
    if not out or len(out) == len(BROADCAST_PLATFORM_KEYS):
        return ""                      # 全选 = 不限平台（等价于「全体成员」）
    return ",".join(out)


def target_platform_label(target_platform) -> str:
    """逗号串 → 「细胞、分子」；空串 → 全体成员。"""
    keys = [k for k in str(target_platform or "").split(",") if k]
    if not keys:
        return "全体成员"
    return "、".join(SIGNUP_LABEL.get(k, k) for k in keys)


def _plat_where(alias: str, platform: str) -> str:
    """受众平台过滤 SQL 片段（作用在 notifications.target_platform 上）。"""
    return (f"(COALESCE({alias}.target_platform,'')='' "
            f"OR INSTR(','||{alias}.target_platform||',', ?)>0)")


def _plat_arg(platform: str) -> str:
    return f",{platform or ''},"


def _notif_public(row: dict, read: bool = False) -> dict:
    return {
        "id": row["id"],
        "category": row.get("category") or "",
        "category_name": NOTIF_CATEGORY_NAME.get(row.get("category"), row.get("category") or ""),
        "title": row.get("title") or "",
        "body": row.get("body") or "",
        "ptype": row.get("ptype") or "",
        "type_label": TYPE_NAME.get(row.get("ptype"), row.get("ptype") or ""),
        "code": row.get("code") or "",
        "platform": row.get("platform") or "",
        "platform_label": PLATFORM_LABEL.get(row.get("platform"), row.get("platform") or ""),
        "target_platform": row.get("target_platform") or "",
        "target_platform_label": target_platform_label(row.get("target_platform")),
        "project_id": int(row.get("project_id") or 0),
        "popup": bool(row.get("popup")),
        "created_at": row.get("created_at") or "",
        "read": bool(read),
        # 仅「待审核」类消息会填：对应 project_audits 的 id 与当前状态
        "audit_id": 0,
        "audit_pending": False,
        # sticky = 「粘住不放」：项目还没审核完成时，这条提醒永远按未读渲染、角标也照常计数
        "sticky": False,
    }


def _annotate_audits(items: list) -> list:
    """给「待审核」消息补上 audit_id / audit_pending / sticky。

    ★ sticky（待审核项目的「请审核」提醒）：点过、点过全部已读、清空过都不算数，
    前端仍按未读渲染，角标也继续计数 —— 只有点「审核完成」才取消。
    目的：消息多的时候不遗漏待审核项目。
    """
    keys = {(x["ptype"], x["code"]) for x in items if x.get("category") == NOTIF_AUDIT_REQUEST}
    if not keys:
        return items
    amap = audit_map()
    for x in items:
        if x.get("category") != NOTIF_AUDIT_REQUEST:
            continue
        a = amap.get((x["ptype"], x["code"]))
        if a:
            x["audit_id"] = a["id"]
            x["audit_pending"] = a["status"] == "pending"
            x["sticky"] = bool(x["audit_pending"])
            if a.get("auditor_name"):
                x["audit_auditor"] = a["auditor_name"]
    return items


def add_notification(category: str, target_role: str, title: str, body: str = "",
                     ptype: str = "", code: str = "", platform: str = "",
                     project_id: int = 0, popup: bool = False,
                     created_by: int = 0, target_platform: str = "") -> int:
    """写一条通知，返回 id。target_role = 'user'（全员普通账户）/ 'admin'（全员管理员）。

    target_platform 非空时再按平台收窄受众（逗号分隔，见 SCHEMA 里的说明）。
    """
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO notifications(category,target_role,target_platform,title,body,"
            "ptype,code,platform,project_id,popup,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (category, target_role, str(target_platform or ""), (title or "").strip(),
             (body or "").strip(),
             ptype or "", str(code or ""), platform or "", int(project_id or 0),
             1 if popup else 0, int(created_by or 0), _now()))
        return cur.lastrowid


def add_notifications_bulk(rows: list) -> int:
    """批量写通知（同一事务）。rows = [{category,target_role,title,body,...}, ...]"""
    rows = [r for r in (rows or []) if r and r.get("title")]
    if not rows:
        return 0
    now = _now()
    with tx() as conn:
        for r in rows:
            conn.execute(
                "INSERT INTO notifications(category,target_role,target_platform,title,body,"
                "ptype,code,platform,project_id,popup,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.get("category") or "", r.get("target_role") or ROLE_USER,
                 str(r.get("target_platform") or ""),
                 str(r.get("title") or "").strip(), str(r.get("body") or "").strip(),
                 r.get("ptype") or "", str(r.get("code") or ""), r.get("platform") or "",
                 int(r.get("project_id") or 0), 1 if r.get("popup") else 0,
                 int(r.get("created_by") or 0), now))
    return len(rows)


def _role_of(user) -> str:
    return ROLE_ADMIN if (user or {}).get("role") == ROLE_ADMIN else ROLE_USER


def person_label(user) -> str:
    """通知 / 审核记录里显示「这个人是谁」。

    优先账号的**姓名**（display_name）；如果这个账号压根没单独填姓名
    （注册 / 建号时留空，存储层会把姓名兜底成登录名），就明写成「账号 xxx」，
    不把登录名当成人名显示 —— 用户看到的应该是一个人的姓名，而不是登录账号。
    """
    user = user or {}
    name = str(user.get("display_name") or "").strip()
    uname = str(user.get("username") or "").strip()
    if name and name != uname:
        return name
    return f"账号 {uname}" if uname else "审核员"


def roles_for(user) -> list:
    """这个用户能看到哪些受众标签的通知。

    管理员 → ['admin']；普通账户 → ['user']；**有审核权限的普通账户**再多一个 'auditor'，
    这样他既收得到「项目已入库」，也收得到「结项待审核」。
    """
    if (user or {}).get("role") == ROLE_ADMIN:
        return [ROLE_ADMIN]
    roles = [ROLE_USER]
    if (user or {}).get("can_audit"):
        roles.append(ROLE_AUDITOR)
    return roles


def list_notifications(user_id: int, roles, limit: int = 200,
                       unread_only: bool = False, popup_only: bool = False,
                       platform: str = "") -> list:
    """当前用户可见的通知（按受众标签 + 平台 + 自己的已读状态）。倒序。

    roles 可以是单个字符串（兼容旧调用）或列表。
    platform = 当前用户的岗位；只有「不限平台」或**明确包含该平台**的通告才可见。
    """
    if isinstance(roles, str):
        roles = [roles]
    roles = [r for r in (roles or []) if r] or [ROLE_USER]
    where = [f"n.target_role IN ({','.join('?' * len(roles))})",
             _plat_where("n", platform)]
    args = list(roles) + [_plat_arg(platform)]
    if unread_only:
        where.append("r.notification_id IS NULL")
    if popup_only:
        where.append("n.popup=1")
    # 「清空消息中心」= 给自己写一条 hidden=1 的记录，只对自己隐藏
    where.append("COALESCE(r.hidden,0)=0")
    sql = (f"SELECT n.*, r.read_at FROM notifications n "
           f"LEFT JOIN notification_reads r "
           f"  ON r.notification_id=n.id AND r.user_id=? "
           f"WHERE {' AND '.join(where)} ORDER BY n.id DESC LIMIT ?")
    with tx() as conn:
        rows = conn.execute(sql, [user_id] + args + [int(limit or 200)]).fetchall()
    return _annotate_audits(
        [_notif_public(dict(r), read=r["read_at"] is not None) for r in rows])


def _sticky_audit_cond(alias: str) -> str:
    """「待审核提醒恒算未读」的 SQL 条件（alias = notifications 表别名）。

    条件成立 ⇔ 这条是 audit_request，且它对应的 project_audits 还是 pending。
    用在未读计数里，让红点一直亮到「审核完成」为止。
    """
    return (f"({alias}.category=? AND EXISTS(SELECT 1 FROM project_audits a "
            f"WHERE a.status='pending' AND a.ptype={alias}.ptype "
            f"AND UPPER(a.code)=UPPER({alias}.code)))")


def notif_unread_count(user_id: int, roles, platform: str = "",
                       can_audit: bool = False) -> int:
    """角标红点数量。

    ★ 审核员的「请审核」提醒**恒算未读**：只要项目还是 pending，这条消息无论
    点过、点过「全部已读」、还是清空过消息中心（见 hide_notifications），角标都不消，
    只有点「审核完成」把 project_audits 置成 done 才归零。消息多时不遗漏待审核项目。
    ★ can_audit 时再补上「压根没有提醒消息」的 pending 项目（例如结项那会儿还没有
    审核员，后来才被授予审核权限），否则红点不会亮、工作台里的待办就容易被忘掉。
    """
    if isinstance(roles, str):
        roles = [roles]
    roles = [r for r in (roles or []) if r] or [ROLE_USER]
    ph = ",".join("?" * len(roles))
    with tx() as conn:
        n = conn.execute(
            f"SELECT COUNT(*) n FROM notifications n "
            f"LEFT JOIN notification_reads r "
            f"  ON r.notification_id=n.id AND r.user_id=? "
            f"WHERE n.target_role IN ({ph}) "
            f"  AND {_plat_where('n', platform)} "
            f"  AND (r.notification_id IS NULL OR {_sticky_audit_cond('n')})",
            [user_id] + roles + [_plat_arg(platform), NOTIF_AUDIT_REQUEST]).fetchone()["n"]
        if not can_audit:
            return n
        miss = conn.execute(
            f"SELECT COUNT(*) n FROM project_audits a WHERE a.status='pending' "
            f"AND NOT EXISTS (SELECT 1 FROM notifications n "
            f"  WHERE n.category=? AND n.ptype=a.ptype AND UPPER(n.code)=UPPER(a.code) "
            f"  AND n.target_role IN ({ph}) AND {_plat_where('n', platform)})",
            [NOTIF_AUDIT_REQUEST] + roles + [_plat_arg(platform)]).fetchone()["n"]
    return int(n) + int(miss)

def notif_pending_popups(user_id: int, roles, limit: int = 20,
                        platform: str = "") -> list:
    """需要弹横幅的未读通知（popup=1）。用户标记已读后就不再返回。

    ⚠️「已复核完成」的待审核提醒不再弹：那条 audit_request 只是历史记录，
    再打断审核员毫无意义（列表里仍能看到，状态是 ✅ 已审核）。
    """
    rows = list_notifications(user_id, roles, limit=limit,
                              unread_only=True, popup_only=True, platform=platform)
    return [x for x in rows
            if not (x.get("category") == NOTIF_AUDIT_REQUEST and x.get("audit_id")
                    and not x.get("audit_pending"))]


def mark_notif_read_for_project(user_id: int, category: str, ptype: str,
                                code: str) -> int:
    """把「我」的某项目某类通知标记为已读。

    用于审核完成后自动收掉审核员那条「xx项目已结项，请审核」——
    事情已经办完了，不该继续挂在未读里。
    """
    with tx() as conn:
        rows = conn.execute(
            "SELECT id FROM notifications WHERE category=? AND ptype=? "
            "AND UPPER(code)=UPPER(?)", (category, ptype, str(code or ""))).fetchall()
        now = _now()
        for r in rows:
            conn.execute(
                "INSERT INTO notification_reads(notification_id,user_id,read_at) "
                "VALUES(?,?,?) ON CONFLICT(notification_id,user_id) "
                "DO UPDATE SET read_at=excluded.read_at",
                (r["id"], user_id, now))
    return len(rows)


def mark_notifications_read(user_id: int, ids=None, roles=None,
                            platform: str = "") -> int:
    """标记已读。ids=None 表示「全部已读」，否则只标记给定 id（且必须是自己角色可见的）。

    ⚠️ 全部已读必须按 roles 过滤：审核员还有 target_role='auditor' 的「待审核」消息，
    早期写死 IN ('user','admin') 会让审核员点「全部已读」后那批仍是未读。
    ⚠️ 同时按 platform 过滤，免得把「发给自己平台以外」的通告也写了已读记录
    （万一以后换岗位，那些通告会诡异地显示成已读）。
    """
    ids = [int(i) for i in (ids or [])] if ids is not None else None
    if isinstance(roles, str):
        roles = [roles]
    roles = [r for r in (roles or []) if r] or [ROLE_USER]
    now = _now()
    with tx() as conn:
        if ids is None:
            rows = conn.execute(
                f"SELECT id FROM notifications WHERE target_role IN "
                f"({','.join('?' * len(roles))}) AND {_plat_where('notifications', platform)}",
                roles + [_plat_arg(platform)]).fetchall()
        else:
            if not ids:
                return 0
            marks = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT id FROM notifications WHERE id IN ({marks})", ids).fetchall()
        n = 0
        for r in rows:
            conn.execute(
                "INSERT INTO notification_reads(notification_id,user_id,read_at) "
                "VALUES(?,?,?) ON CONFLICT(notification_id,user_id) "
                "DO UPDATE SET read_at=excluded.read_at",
                (r["id"], user_id, now))
            n += 1
    return n


def hide_notifications(user_id: int, roles, platform: str = "") -> int:
    """清空「我」的消息中心：把当前用户可见的通知全部标记为已读 + 隐藏。

    通知是按受众标签广播的（没有 per-user 行），所以「清空」只能按人隐藏：
    给当前用户写一条 hidden=1 的已读记录。别人的消息中心完全不受影响，
    「待我审核」这类独立工作台也不受影响。
    ★ 例外：**待审核项目的「请审核」提醒清不掉** —— 项目还没审核完成，
    红点与那条消息都必须留着，否则「清空一下」就把待办藏起来了。
    """
    if isinstance(roles, str):
        roles = [roles]
    roles = [r for r in (roles or []) if r] or [ROLE_USER]
    now = _now()
    with tx() as conn:
        rows = conn.execute(
            f"SELECT id FROM notifications WHERE target_role IN "
            f"({','.join('?' * len(roles))}) AND {_plat_where('notifications', platform)} "
            f"AND NOT {_sticky_audit_cond('notifications')}",
            roles + [_plat_arg(platform), NOTIF_AUDIT_REQUEST]).fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO notification_reads(notification_id,user_id,read_at,hidden) "
                "VALUES(?,?,?,1) ON CONFLICT(notification_id,user_id) "
                "DO UPDATE SET read_at=excluded.read_at, hidden=1",
                (r["id"], user_id, now))
    return len(rows)


def clear_notifications() -> int:
    """清空全部通知与已读记录（数据维护用）。"""
    with tx() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM notifications").fetchone()["n"]
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM notification_reads")
    return n


# ================================================================== 全服通告
# 管理员手动发布的公告（如「月末了，请及时填写月末统计」），可选定接收平台；
# 命中平台的普通账户会在消息中心收到，并按 popup 决定要不要弹横幅。
def platform_member_counts() -> dict:
    """各岗位在用的普通账户数（{'cell':3,..., '__total__':9}）。"""
    with tx() as conn:
        rows = conn.execute(
            "SELECT platform, COUNT(*) n FROM users WHERE role=? AND active=1 "
            "GROUP BY platform", (ROLE_USER,)).fetchall()
    out = {k: 0 for k in BROADCAST_PLATFORM_KEYS}
    out["__total__"] = 0
    for r in rows:
        n = int(r["n"] or 0)
        out["__total__"] += n
        out[str(r["platform"] or "")] = n
    return out


def broadcast_audience(target_platform, counts: dict = None) -> int:
    """某条通告的受众规模（人）。target_platform 为空 = 全体普通账户。"""
    counts = counts if counts is not None else platform_member_counts()
    keys = [k for k in str(target_platform or "").split(",") if k]
    if not keys:
        return int(counts.get("__total__") or 0)
    return sum(int(counts.get(k) or 0) for k in keys)


def get_notification(nid: int):
    with tx() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id=?",
                           (int(nid or 0),)).fetchone()
    return _notif_public(dict(row)) if row else None


def add_broadcast(title: str, body: str, platforms, popup: bool = True,
                  created_by: int = 0) -> dict:
    """发布一条全服通告，返回该记录（含受众标签）。

    platforms = 平台键列表；空 / 全选 → 不限平台（全体普通账户可见）。
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("通告标题不能为空")
    tp = norm_target_platform(platforms)
    nid = add_notification(NOTIF_BROADCAST, ROLE_USER, title, body,
                           popup=bool(popup), created_by=int(created_by or 0),
                           target_platform=tp)
    item = get_notification(nid) or {}
    item["audience"] = broadcast_audience(tp)
    item["read_count"] = 0
    return item


def list_broadcasts(limit: int = 100) -> list:
    """已发布通告列表（倒序），带「受众人数 / 已读人数」统计。"""
    counts = platform_member_counts()
    with tx() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE category=? ORDER BY id DESC LIMIT ?",
            (NOTIF_BROADCAST, int(limit or 100))).fetchall()
        out = []
        for r in rows:
            d = _notif_public(dict(r))
            keys = [k for k in str(d["target_platform"]).split(",") if k]
            sql = ("SELECT COUNT(*) n FROM notification_reads rd "
                   "JOIN users u ON u.id=rd.user_id "
                   "WHERE rd.notification_id=? AND rd.read_at IS NOT NULL "
                   "  AND u.role=? AND u.active=1")
            args = [d["id"], ROLE_USER]
            if keys:
                sql += f" AND u.platform IN ({','.join('?' * len(keys))})"
                args += keys
            d["read_count"] = int(conn.execute(sql, args).fetchone()["n"] or 0)
            d["audience"] = broadcast_audience(d["target_platform"], counts)
            out.append(d)
    return out


def delete_broadcast(bid: int) -> bool:
    """撤回通告：连同已读记录一起删掉（广播类没有 per-user 行，直接删干净）。"""
    bid = int(bid or 0)
    with tx() as conn:
        row = conn.execute("SELECT category FROM notifications WHERE id=?", (bid,)).fetchone()
        if not row or (row["category"] or "") != NOTIF_BROADCAST:
            return False
        conn.execute("DELETE FROM notification_reads WHERE notification_id=?", (bid,))
        conn.execute("DELETE FROM notifications WHERE id=?", (bid,))
    return True


# ================================================================== 结项审核
def _audit_public(row: dict) -> dict:
    return {
        "id": row["id"],
        "ptype": row.get("ptype") or "",
        "type_label": TYPE_NAME.get(row.get("ptype"), row.get("ptype") or ""),
        "code": row.get("code") or "",
        "name": row.get("name") or "",
        "project_id": int(row.get("project_id") or 0),
        "status": row.get("status") or "pending",
        "status_name": {"pending": "待审核", "done": "已审核"}.get(row.get("status"), "待审核"),
        "auditor_id": int(row.get("auditor_id") or 0),
        "auditor_name": row.get("auditor_name") or "",
        "created_at": row.get("created_at") or "",
        "reviewed_at": row.get("reviewed_at") or "",
    }


def upsert_audit(ptype: str, code: str, project_id: int = 0, name: str = "") -> dict:
    """项目结项 → 生成/重置一条待审核记录（同一项目只保留一条）。

    先取消勾选再重新勾上（重新结项）时会重置成 pending，审核人信息清空。
    """
    ptype = (ptype or "").strip()
    code = str(code or "").strip().upper()
    if not ptype or not code:
        raise ValueError("项目类型与编号不能为空")
    with tx() as conn:
        conn.execute(
            "INSERT INTO project_audits(ptype,code,project_id,name,status,"
            "auditor_id,auditor_name,created_at,reviewed_at) "
            "VALUES(?,?,?,?,'pending',0,'',?,'') "
            "ON CONFLICT(ptype,code) DO UPDATE SET "
            "  project_id=excluded.project_id, name=excluded.name, status='pending', "
            "  auditor_id=0, auditor_name='', created_at=excluded.created_at, reviewed_at=''",
            (ptype, code, int(project_id or 0), (name or "").strip(), _now()))
        row = conn.execute(
            "SELECT * FROM project_audits WHERE ptype=? AND code=?", (ptype, code)).fetchone()
    return _audit_public(dict(row))


def get_audit(aid: int):
    with tx() as conn:
        row = conn.execute("SELECT * FROM project_audits WHERE id=?", (int(aid or 0),)).fetchone()
    return _audit_public(dict(row)) if row else None


def audit_map() -> dict:
    """{(ptype, code): {id, status, auditor_name}} 一次取全，供通知列表打标。"""
    with tx() as conn:
        rows = conn.execute("SELECT id, ptype, code, status, auditor_name FROM project_audits").fetchall()
    return {(r["ptype"], r["code"]): {"id": r["id"], "status": r["status"],
                                      "auditor_name": r["auditor_name"] or ""} for r in rows}


def list_audits(status: str = "pending", limit: int = 200) -> list:
    where, args = "", []
    if status:
        where = "WHERE status=?"
        args.append(status)
    with tx() as conn:
        rows = conn.execute(
            f"SELECT * FROM project_audits {where} ORDER BY id DESC LIMIT ?",
            args + [int(limit or 200)]).fetchall()
    return [_audit_public(dict(r)) for r in rows]


def count_audits(status: str = "pending") -> int:
    where, args = "", []
    if status:
        where = "WHERE status=?"
        args.append(status)
    with tx() as conn:
        return conn.execute(f"SELECT COUNT(*) n FROM project_audits {where}", args).fetchone()["n"]


def complete_audit(aid: int, auditor: dict):
    """审核员复核完成：标记 done 并记下审核人。已审核过则返回 None（避免重复通知）。

    ⚠️ auditor_name 存的是**姓名**（见 person_label）；账号没填姓名时存「账号 xxx」，
    不存登录名 —— 消息中心里显示的是「✅ 已审核（某某某）」。
    """
    auditor = auditor or {}
    with tx() as conn:
        row = conn.execute("SELECT * FROM project_audits WHERE id=?", (int(aid or 0),)).fetchone()
        if not row:
            return None
        row = dict(row)
        if row.get("status") == "done":
            return None
        conn.execute(
            "UPDATE project_audits SET status='done', auditor_id=?, auditor_name=?, reviewed_at=? "
            "WHERE id=?",
            (int(auditor.get("id") or 0), person_label(auditor),
             _now(), row["id"]))
        new = dict(conn.execute("SELECT * FROM project_audits WHERE id=?", (row["id"],)).fetchone())
    return _audit_public(new)


def list_auditors() -> list:
    """有审核权限、且启用中的普通账户。"""
    with tx() as conn:
        rows = conn.execute(
            "SELECT id, username, display_name, platform FROM users "
            "WHERE role=? AND can_audit=1 AND active=1 ORDER BY id",
            (ROLE_USER,)).fetchall()
    return [dict(r) for r in rows]


def has_auditor() -> bool:
    with tx() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM users WHERE role=? AND can_audit=1 AND active=1 LIMIT 1",
            (ROLE_USER,)).fetchone())
