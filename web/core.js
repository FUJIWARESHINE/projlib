/* 项目库统计程序 · 外壳 / 路由 / 通用工具 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  view: "dashboard",
  types: [],
  dash: null,
  me: null,                 // 当前登录用户
  browse: { ptype: "", search: "", page: 1, page_size: 50, data: null, loading: false,
            platform: "", status: "", order: "asc" },
  import: { preview: null, path: "", busy: false },
  month: { mid: 0, detail: null, list: [] },
};

const TYPE_CLASS = { real: "t-real", urgent: "t-urgent", stock: "t-stock", repair: "t-repair" };
const TYPE_PILL = { real: "real", urgent: "urgent", stock: "stock", repair: "repair" };
const PLATFORM_LABEL = { cell: "细胞", mol: "分子", path: "病理", animal: "动物",
                          ops: "运营", design: "设计" };

/* ------------------------------------------------ 本地偏好（每台浏览器各自记住） */
function savePref(key, val) {
  try { localStorage.setItem("projlib." + key, String(val)); } catch (e) {}
}
function loadPref(key, def) {
  try {
    const v = localStorage.getItem("projlib." + key);
    return v === null ? def : v;
  } catch (e) { return def; }
}
// 项目库排序偏好：进页面时读取
state.browse.order = loadPref("browse_order", "asc");

/* ------------------------------------------------ 脱敏展示 */
/* 界面上不直接暴露用户本机的源文件名，改为「主名…扩展名」的模糊形式。
   "A-加急-返修-库存 信息更新.xlsx" -> "A-加急-返修-库存 信息更新…xlsx"
   前缀取原文件名首段（空格/横线前），过长再截断到 6 字。 */
function maskFile(name) {
  if (!name) return "—";
  const base = String(name).split(/[\\/]/).pop() || String(name);
  const dot = base.lastIndexOf(".");
  const ext = dot > 0 ? base.slice(dot + 1) : "";
  let stem = dot > 0 ? base.slice(0, dot) : base;
  const head = (stem.split(/[\s\-_]+/)[0] || stem).slice(0, 6);
  const tail = ext ? "…" + ext : "…";
  return head + tail;
}

/* ------------------------------------------------ 权限 */
function isAdmin() { return !!state.me && state.me.role === "admin"; }
function myPlatform() { return (state.me && state.me.platform) || ""; }
function isReadonly() { return !isAdmin() && !!(state.me && state.me.readonly); }
function canEditPlatform(p) {
  if (isAdmin()) return true;
  if (isReadonly()) return false;
  return myPlatform() === p;
}

/* ------------------------------------------------ HTTP */
async function api(path, { method = "GET", body = null } = {}) {
  const opt = { method, headers: {} };
  if (body !== null) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw new Error("服务返回异常（" + res.status + "）");
  }
  if (res.status === 401) {
    location.href = "/login.html";
    throw new Error("登录已过期，请重新登录");
  }
  if (!data.ok) throw new Error(data.error || "操作失败");
  return data;
}


function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function money(v) {
  const n = Number(v || 0);
  if (!n) return "—";
  return "¥" + n.toLocaleString("zh-CN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

/* ------------------------------------------------ Toast */
function toast(msg, kind = "success", ms = 2600) {
  const wrap = $("#toastWrap");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  const icon = kind === "error" ? "⚠️" : kind === "warn" ? "⚠️"
    : kind === "info" ? "📁" : "✅";
  el.innerHTML = `<span>${icon}</span><span style="word-break:break-all">${esc(msg)}</span>`;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .22s ease, transform .22s ease";
    el.style.opacity = "0";
    el.style.transform = "translateY(-10px)";
    setTimeout(() => el.remove(), 240);
  }, ms);
}

/* ------------------------------------------------ Modal */
let _modalCloseCb = null;
function openModal({ title, body, footer, wide = false, onMount, onClose }) {
  const root = $("#modalRoot");
  root.innerHTML = `
    <div class="mask">
      <div class="modal${wide ? " wide" : ""}">
        <div class="modal-head">
          <h3>${esc(title)}</h3>
          <button class="modal-close" data-close>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="modal-body">${body}</div>
        ${footer ? `<div class="modal-foot">${footer}</div>` : ""}
      </div>
    </div>`;
  const mask = $(".mask", root);
  mask.addEventListener("click", (e) => { if (e.target === mask) closeModal(); });
  $$("[data-close]", root).forEach((b) => b.addEventListener("click", closeModal));
  _modalCloseCb = typeof onClose === "function" ? onClose : null;
  if (onMount) onMount(root);
  document.addEventListener("keydown", escClose);
}
function escClose(e) { if (e.key === "Escape") closeModal(); }
function closeModal() {
  $("#modalRoot").innerHTML = "";
  document.removeEventListener("keydown", escClose);
  const cb = _modalCloseCb;      // 关闭钩子：用于「用户取消」时回调（如导出目标选择）
  _modalCloseCb = null;
  if (cb) cb();
}

/* ------------------------------------------------ 导航 */
const ICONS = {
  dashboard: '<path d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 9h8V3h-8z"/>',
  month: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/><path d="M9 15l2 2 4-4"/>',
  worklog: '<path d="M5 3h11l3 3v15a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M15 3v4h4"/><path d="M8 12h8M8 16h6M8 8h3"/>',
  browse: '<path d="M4 4a2 2 0 0 1 2-2h6l2 3h4a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>',
  import: '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 11a3 3 0 1 0 0-6"/><path d="M18 20a6 6 0 0 0-3-5.2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 13.7H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10.3 3V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9"/>',
};

function navItems() {
  // 所有登录用户：仪表盘 / 月末项目统计 / 项目库 / 工作日志
  const items = [
    { key: "dashboard", label: "仪表盘" },
    { key: "month", label: "月末项目统计" },
    { key: "browse", label: "项目库" },
    { key: "worklog", label: "工作日志" },
  ];
  // 仅管理员：导入项目库 / 用户管理 / 设置
  if (isAdmin()) {
    items.push({ key: "import", label: "导入项目库" });
    items.push({ key: "users", label: "用户管理" });
    items.push({ key: "settings", label: "设置" });
  }
  return items;
}

function renderNav() {
  $("#nav").innerHTML = navItems().map((n) => `
    <div class="nav-item${state.view === n.key ? " active" : ""}" data-nav="${n.key}">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[n.key] || ""}</svg>
      <span>${esc(n.label)}</span>
    </div>`).join("");
  $$("[data-nav]").forEach((el) => el.addEventListener("click", () => go(el.dataset.nav)));
}

function renderUserBox() {
  const u = state.me;
  if (!u) return;
  const badge = u.role === "admin"
    ? '<span class="ub-badge admin">管理</span>'
    : `<span class="ub-badge user">${esc(PLATFORM_LABEL[u.platform] || "用户")}</span>`;
  const sub = u.role === "admin" ? "全部权限"
    : (u.readonly ? "只读岗位" : (PLATFORM_LABEL[u.platform] || "") + "平台 · 可填报");
  $("#userBox").innerHTML = `
    <div class="ub-info">
      <div class="ub-name">${esc(u.display_name)}${badge}</div>
      <div class="ub-sub">${esc(sub)}</div>
    </div>
    <button class="ub-out" id="btnLogout" title="退出登录">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>
    </button>`;
  $("#btnLogout").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST", body: {} }); } catch (e) {}
    location.href = "/login.html";
  });
}

const TITLES = {
  dashboard: ["仪表盘", "项目库整体概览"],
  month: ["月末项目统计", "各平台填报当月完成项目 → 管理员一键导出"],
  browse: ["项目库", "浏览与维护全部项目"],
  worklog: ["工作日志", "记录每日工作 · 仅自己可见"],
  import: ["导入项目库", "从 Excel 批量导入 / 更新"],
  users: ["用户管理", "账号与平台岗位"],
  settings: ["设置", "导出目录与数据维护"],
};

async function go(view) {
  state.view = view;
  const allowed = navItems().map((n) => n.key);
  if (!allowed.includes(view)) view = "dashboard", state.view = view;
  renderNav();
  const [t, sub] = TITLES[view] || ["", ""];
  $("#pageTitle").textContent = t;
  $("#pageSub").textContent = sub;
  $("#topActions").innerHTML = "";
  const view$ = $("#view");
  view$.innerHTML = '<div class="loading">加载中…</div>';
  try {
    await VIEWS[view](view$);
  } catch (e) {
    view$.innerHTML = `<div class="empty"><p>加载失败：${esc(e.message)}</p></div>`;
  }
}

async function loadTypes() {
  const r = await api("/api/types");
  state.types = r.data;
  const counts = {};
  if (state.dash) state.dash.by_type.forEach((t) => { counts[t.key] = t.count; });
  return state.types.map((t) => ({ ...t, count: counts[t.key] }));
}

async function boot() {
  try {
    const me = await api("/api/auth/me");
    state.me = me.data.user;
  } catch (e) {
    location.href = "/login.html";
    return;
  }
  renderNav();
  renderUserBox();
  try {
    const r = await api("/api/dashboard");
    state.dash = r.data;
    await loadTypes();
  } catch (e) {
    toast(e.message, "error");
  }
  go("dashboard");
}

