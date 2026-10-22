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
            platform: "", status: "", order: "asc", sort: "code", group: "type" },
  import: { preview: null, path: "", busy: false },
  month: { mid: 0, detail: null, list: [] },
  notif: { items: [], unread: 0, popups: [], audits: [], canAudit: false,
           open: false, bannerOpen: false, timer: 0 },
};

const TYPE_CLASS = { real: "t-real", urgent: "t-urgent", stock: "t-stock", repair: "t-repair" };
const TYPE_PILL = { real: "real", urgent: "urgent", stock: "stock", repair: "repair" };
const TYPE_LABEL = { real: "真实", urgent: "加急", stock: "库存", repair: "返修" };
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
// 项目库排序偏好：进页面时读取（sort = 排序依据，order = 方向，group = 是否按类型分组）
state.browse.order = loadPref("browse_order", "asc");
state.browse.sort = loadPref("browse_sort", "code");
state.browse.group = loadPref("browse_group", "type");

/* ------------------------------------------------ 主题（明亮 / 暗黑）
   默认明亮；用户手动切过就记在 localStorage["projlib.theme"]。
   首屏的主题由 index.html 里的小脚本提前写好 data-theme，避免闪白。 */
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}
function applyTheme(t) {
  const next = t === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  const btn = $("#themeBtn");
  if (btn) btn.setAttribute("title", next === "dark" ? "切换到明亮主题" : "切换到暗黑主题");
}
function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  savePref("theme", next);
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Chromium 支持 View Transitions：整页做一次淡入揭示，接近 iOS 换外观的手感
  if (typeof document.startViewTransition === "function" && !reduced) {
    document.startViewTransition(() => applyTheme(next));
  } else {
    applyTheme(next);
  }
}
function bindTheme() {
  applyTheme(currentTheme());
  const btn = $("#themeBtn");
  if (btn && !btn.dataset.themeBound) {
    btn.dataset.themeBound = "1";
    btn.addEventListener("click", toggleTheme);
  }
}

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

/* 账号人名口径（与后端 db.person_label 一致）：
   通知 / 审核记录里显示的是**姓名**；账号没单独填姓名时写成「账号 xxx」，
   不把登录名当人名显示。 */
function personLabel(u) {
  u = u || {};
  const name = String(u.display_name || "").trim();
  const uname = String(u.username || "").trim();
  if (name && name !== uname) return name;
  return uname ? `账号 ${uname}` : "审核员";
}
// 该账号是否「没单独填姓名」（空 / 与登录名相同，存储层会把姓名兜底成登录名）
function noNameOf(u) {
  u = u || {};
  const name = String(u.display_name || "").trim();
  const uname = String(u.username || "").trim();
  return !name || name === uname;
}
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

/* 轻量提醒弹窗，**可以叠在已有弹窗之上**（openModal 会清空 #modalRoot，
   所以这里直接挂 body，并用 capture 阶段拦掉 Esc，免得连带把底下的弹窗关掉、
   把用户刚填的内容弄丢）。关闭后 resolve，方便调用方 await。 */
function alertDialog(title, bodyHtml, okText = "知道了") {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "mask mask-stack";
    wrap.innerHTML = `
      <div class="modal" style="width:460px">
        <div class="modal-head">
          <h3>${esc(title)}</h3>
          <button class="modal-close" data-ok>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="modal-body">${bodyHtml}</div>
        <div class="modal-foot"><button class="btn btn-primary" data-ok>${esc(okText)}</button></div>
      </div>`;
    document.body.appendChild(wrap);
    let closed = false;
    const done = () => {
      if (closed) return;
      closed = true;
      wrap.remove();
      document.removeEventListener("keydown", onKey, true);
      resolve();
    };
    const onKey = (e) => {
      if (e.key !== "Escape" && e.key !== "Enter") return;
      // 光标还在文本框里时，回车留给换行，不抢
      const tag = (document.activeElement || {}).tagName || "";
      if (e.key === "Enter" && (tag === "TEXTAREA" || tag === "INPUT")) return;
      e.preventDefault();
      e.stopPropagation();      // capture 阶段掐断，别让底层弹窗的 Esc 也跟着触发
      done();
    };
    wrap.addEventListener("click", (e) => {
      if (e.target === wrap || e.target.closest("[data-ok]")) done();
    });
    document.addEventListener("keydown", onKey, true);
    const okBtn = wrap.querySelector(".modal-foot [data-ok]");
    if (okBtn) okBtn.focus();
  });
}

/* ================================================ 消息中心（站内通知）
   数据来自 /api/notifications。通知按**角色**广播（普通账户一套 / 管理员一套），
   已读状态按**用户**存 —— 谁看过互不影响，看过之后就不再弹横幅。
     · 项目入库（普通账户收）：真实/加急/返修 → 登录弹「流光横幅」；库存只进消息中心。
     · 平台完成 / 项目结项（管理员收）：只在消息中心显示未读，不弹窗。 */
const NOTIF_ICON = { project_in: "📦", platform_done: "✅", project_done: "🏁",
                     audit_request: "🔎", audit_done: "🛡️", broadcast: "📢" };
const NOTIF_TAG = { project_in: "项目入库", platform_done: "平台完成", project_done: "项目结项",
                    audit_request: "待审核", audit_done: "审核完成", broadcast: "全服通告" };

function notifTime(s) {
  const t = String(s || "");
  const m = t.match(/^\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2})/);   // 2026-10-14 09:12:33
  return m ? `${m[1]} ${m[2]}` : (t || "");
}

/* 消息里的项目类型标签（真实 / 加急 / 库存 / 返修），横幅与消息中心都用它，
   让人一眼看出这条通知说的是哪一类项目。 */
function notifTypePill(ptype) {
  if (!ptype) return "";
  const label = TYPE_LABEL[ptype] || ptype;
  return `<span class="pill ${TYPE_PILL[ptype] || ""}">${esc(label)}</span>`;
}

async function loadNotifications({ popup = true, silent = true } = {}) {
  let r;
  try {
    r = await api("/api/notifications");
  } catch (e) {
    if (!silent) toast(e.message, "error");
    return;
  }
  const d = r.data || {};
  state.notif.items = d.items || [];
  state.notif.unread = d.unread || 0;
  state.notif.popups = d.popups || [];
  state.notif.canAudit = !!d.can_audit;
  state.notif.audits = d.audits || [];
  renderNotifBadge();
  if (state.notif.open) renderNotifPanel();
  if (popup && state.notif.popups.length) showNotifBanner(state.notif.popups);
}

function renderNotifBadge() {
  const b = $("#notifBadge");
  const bell = $("#notifBell");
  if (!b) return;
  const n = state.notif.unread || 0;
  if (n > 0) { b.hidden = false; b.textContent = n > 99 ? "99+" : String(n); }
  else { b.hidden = true; b.textContent = "0"; }
  b.title = n > 0
    ? (state.notif.canAudit && n > 0
        ? `${n} 条未读（含待审核，点「审核完成」后红点才消）`
        : `${n} 条未读`)
    : "";
  if (bell) bell.classList.toggle("has-unread", n > 0);
}

function notifItemHtml(n) {
  const tag = NOTIF_TAG[n.category] || "";
  const icon = NOTIF_ICON[n.category] || "🔔";
  const isAudit = n.category === "audit_request";
  // sticky = 待审核项目的「请审核」提醒：点过、点过「全部已读」、清空过都不算数，
  //   这条永远按未读渲染（右侧红点不灭）、右上角角标也一直计数，
  //   只有点了「审核完成」才取消 —— 消息多的时候不至于漏掉待审核项目。
  const unread = !n.read || !!n.sticky;
  // 同一项目可能有多轮「请审核」提醒：旧的那轮已被新一轮替代，别再给按钮
  const act = isAudit
    ? (n.superseded
        ? `<div class="ni-act"><span class="ni-done">↩︎ 已被新一轮提醒替代</span></div>`
        : n.audit_pending
        ? `<div class="ni-act"><button class="btn btn-green btn-sm" data-audit="${n.audit_id}">审核完成</button>
             <span class="ni-hint">复核无误后点这里；<b>没点之前红点不会消</b>，以免漏审</span></div>`
        : `<div class="ni-act"><span class="ni-done">✅ 已审核${n.audit_auditor ? "（" + esc(n.audit_auditor) + "）" : ""}</span></div>`)
    : "";
  // 全服通告：标出这条是发给哪些平台的（全体成员 / 细胞、分子）
  const aud = n.category === "broadcast"
    ? `<span class="ni-plat">接收：${esc(n.target_platform_label || "全体成员")}</span>` : "";
  return `<div class="notif-item${unread ? " unread" : ""}${n.sticky ? " sticky" : ""}" data-nid="${n.id}"
      ${n.code ? `data-code="${esc(n.code)}"` : ""}
      title="${n.code ? "点击到项目库查看该项目" : ""}">
    <span class="ni-icon">${icon}</span>
    <div class="ni-main">
      <div class="ni-title">${notifTypePill(n.ptype)}${esc(n.title)}</div>
      ${n.body ? `<div class="ni-body">${esc(n.body)}</div>` : ""}
      <div class="ni-meta">${tag ? `<span class="ni-tag">${esc(tag)}</span>` : ""}${n.sticky ? '<span class="ni-tag ni-lock">待办 · 未审核完成前一直提醒</span>' : ""}${aud}<span>${esc(notifTime(n.created_at))}</span></div>
      ${act}
    </div>
    ${unread ? '<span class="ni-dot"></span>' : ''}
  </div>`;
}

/* 「待我审核」区块：审核员的工作台。比通知更可靠 —— 只要项目还是 pending 就在这儿。 */
function notifAuditHtml() {
  const list = state.notif.audits || [];
  if (!state.notif.canAudit || !list.length) return "";
  const shown = list.slice(0, 8);
  return `
    <div class="notif-audit">
      <div class="na-head">待我审核 <span class="np-pill">${list.length} 个</span></div>
      ${shown.map((a) => `
        <div class="na-item">
          <div class="na-main">
            <div class="na-title">${notifTypePill(a.ptype)}${esc(a.code)} 项目已结项</div>
            <div class="na-sub">${esc(a.name) || "—"}</div>
          </div>
          <button class="btn btn-green btn-sm" data-audit="${a.id}">审核完成</button>
        </div>`).join("")}
      ${list.length > shown.length ? `<div class="na-more">还有 ${list.length - shown.length} 个…</div>` : ""}
    </div>`;
}

function renderNotifPanel() {
  const root = $("#notifRoot");
  if (!root) return;
  if (!state.notif.open) {
    // 只清面板，别把正在显示的横幅一起清掉
    const p = $("#notifPanel", root);
    if (p) p.remove();
    const m = $("[data-notif-mask]", root);
    if (m) m.remove();
    return;
  }
  const items = state.notif.items || [];
  root.innerHTML = `
    <div class="notif-mask" data-notif-mask></div>
    <div class="notif-panel" id="notifPanel">
      <div class="notif-head">
        <h4>消息中心${state.notif.unread ? ` <span class="np-pill">${state.notif.unread} 条未读</span>` : ""}</h4>
        <div class="np-acts">
          <button class="btn btn-ghost btn-sm" data-notif-allread${state.notif.unread ? "" : " disabled"}>全部已读</button>
          <button class="btn btn-ghost btn-sm" data-notif-clear${items.length ? "" : " disabled"}>清空</button>
          <button class="np-close" data-notif-close title="关闭">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>
      </div>
      <div class="notif-list">
        ${notifAuditHtml()}
        ${items.length ? items.map(notifItemHtml).join("")
          : '<div class="notif-empty">暂时没有消息</div>'}
      </div>
      <div class="notif-foot">${isAdmin()
        ? "实验员把平台勾成 ✅、审核员复核完成后，这里会收到「平台完成 / 项目结项 / 审核完成」提醒。要发全服提醒，去左侧「全服通告」。"
        : state.notif.canAudit
          ? "管理员新增项目后这里会收到「项目已入库」；项目结项后这里会出现「待审核」，复核完点「审核完成」；📢全服通告也会送到这里。待审核项目在点「审核完成」之前，这条消息和右上角红点会一直保留（点过、全部已读、清空都消不掉）。"
          : "管理员新增项目后，这里会收到「项目已入库」提醒；管理员发布的📢全服通告（发给你所在平台的）也会送到这里。"}</div>
    </div>`;
  bindNotifPanel();
}

/* 审核完成：复核无误 → 后端标记 done 并通知全体管理员「某某某已审核完成，请复审」。 */
async function finishAudit(aid, btn) {
  if (!aid) return;
  const el = btn || null;
  if (el) el.disabled = true;
  try {
    await api(`/api/audits/${aid}/done`, { method: "POST", body: {} });
  } catch (e) {
    if (el) el.disabled = false;
    return toast(e.message, "error");
  }
  toast("已审核完成，已通知管理员复审");
  await loadNotifications({ popup: false });
  if (state.notif.open) renderNotifPanel();
}

function bindNotifPanel() {
  const root = $("#notifRoot");
  if (!root || !state.notif.open) return;
  const panel = $("#notifPanel", root);
  if (!panel) return;
  const close = () => { state.notif.open = false; renderNotifPanel(); };
  root.querySelectorAll("[data-notif-mask],[data-notif-close]").forEach((el) =>
    el.addEventListener("click", close));

  // 「审核完成」按钮：别让它冒泡触发外层的「点消息跳项目库」
  root.querySelectorAll("[data-audit]").forEach((el) => el.addEventListener("click", (ev) => {
    ev.stopPropagation();
    ev.preventDefault();
    finishAudit(Number(el.dataset.audit), el);
  }));

  const all = panel.querySelector("[data-notif-allread]");
  if (all) all.addEventListener("click", async () => {
    try { await api("/api/notifications/read", { method: "POST", body: { all: true } }); }
    catch (e) { return toast(e.message, "error"); }
    (state.notif.items || []).forEach((x) => { x.read = true; });
    const stickyLeft = (state.notif.items || []).filter((x) => x.sticky).length;
    state.notif.popups = [];
    await loadNotifications({ popup: false });   // 未读数以后端为准：待审核的仍算未读
    toast(stickyLeft
      ? `已全部标记为已读；还有 ${stickyLeft} 个待审核项目，点「审核完成」后红点才会消`
      : "已全部标记为已读");
  });

  // 清空：把「我」的消息列表整个清掉（按人隐藏，别人的消息中心不受影响）
  const clr = panel.querySelector("[data-notif-clear]");
  if (clr) clr.addEventListener("click", async () => {
    if (!(state.notif.items || []).length) return;
    if (!confirm("确定清空消息中心？\n\n"
      + "· 只清空你自己的消息列表，其他账号的消息不受影响；\n"
      + "· 「待我审核」工作台不受影响，待审核项目仍会列出；\n"
      + "· 待审核项目的「请审核」提醒清不掉（右上角红点也不会消），点了「审核完成」才消失；\n"
      + "· 清空后这些消息不会再弹横幅。")) return;
    clr.disabled = true;
    try {
      await api("/api/notifications/clear", { method: "POST", body: {} });
    } catch (e) {
      clr.disabled = false;
      return toast(e.message, "error");
    }
    state.notif.popups = [];
    await loadNotifications({ popup: false });
    const left = (state.notif.items || []).filter((x) => x.sticky).length;
    toast(left ? `消息已清空；${left} 个待审核项目仍会提醒`
               : "消息已清空");
  });

  panel.querySelectorAll(".notif-item").forEach((el) => el.addEventListener("click", async () => {
    const nid = Number(el.dataset.nid);
    const code = el.dataset.code || "";
    const item = (state.notif.items || []).find((x) => x.id === nid);
    if (item && !item.read) {
      try { await api("/api/notifications/read", { method: "POST", body: { ids: [nid] } }); }
      catch (e) { /* 标已读失败不影响跳转 */ }
      item.read = true;
      // 待审核的提醒是「粘住」的：标已读也照样算未读，别去减角标
      if (!item.sticky) {
        state.notif.unread = Math.max(0, (state.notif.unread || 0) - 1);
      }
      state.notif.popups = (state.notif.popups || []).filter((x) => x.id !== nid);
      renderNotifBadge();
    }
    if (code) {
      state.notif.open = false;
      renderNotifPanel();
      state.browse.search = code;
      state.browse.page = 1;
      go("browse");                     // 直接跳到项目库并按编号搜出来
    } else {
      renderNotifPanel();
    }
  }));
}

function toggleNotifPanel() {
  state.notif.open = !state.notif.open;
  renderNotifPanel();
  if (state.notif.open) loadNotifications({ popup: false });
}

/* 登录后的「闪烁流光横幅」：一条或多条入库通知合并成一个横幅，
   点「立即查看」跳到消息中心，「知道了」直接关掉 —— 两种操作都标记已读，
   标记过之后同一账户再登录就不会再弹（已读按用户存）。 */
function showNotifBanner(popups) {
  if (state.notif.bannerOpen) return;              // 一次只弹一个，别叠罗汉
  const list = (popups || []).filter((x) => x && x.id);
  if (!list.length) return;
  const root = $("#notifRoot");
  if (!root) return;
  state.notif.bannerOpen = true;
  const n = list.length;
  const first = list[0];
  const more = list.slice(1, 4)
    .map((x) => `<li>${notifTypePill(x.ptype)}${esc(x.title)}</li>`).join("");
  const extra = n > 1
    ? `<div class="nb-more">另有 ${n - 1} 条：<ul>${more}${n > 4 ? "<li>…</li>" : ""}</ul></div>`
    : "";
  const wrap = document.createElement("div");
  wrap.className = "notif-banner-wrap";
  wrap.innerHTML = `
    <div class="notif-banner">
      <span class="nb-ring"></span>
      <div class="nb-icon">🔔</div>
      <div class="nb-text">
        <div class="nb-kicker">新消息提醒</div>
        <div class="nb-title">${notifTypePill(first.ptype)}${esc(first.title)}</div>
        ${first.body ? `<div class="nb-sub">${esc(first.body)}</div>` : ""}
        ${extra}
      </div>
      <div class="nb-acts">
        <button class="btn btn-primary btn-sm" data-nb-view>立即查看</button>
        <button class="btn btn-ghost btn-sm" data-nb-ok>知道了</button>
      </div>
    </div>`;
  root.appendChild(wrap);
  const ids = list.map((x) => x.id);
  let closed = false;
  const done = async (view) => {
    if (closed) return;
    closed = true;
    wrap.remove();
    state.notif.bannerOpen = false;
    try { await api("/api/notifications/read", { method: "POST", body: { ids } }); }
    catch (e) { /* 失败也照常关闭，下次登录还会再提醒 */ }
    state.notif.popups = [];
    await loadNotifications({ popup: false });
    if (view) { state.notif.open = true; renderNotifPanel(); }
  };
  wrap.querySelector("[data-nb-view]").addEventListener("click", () => done(true));
  wrap.querySelector("[data-nb-ok]").addEventListener("click", () => done(false));
}

/* 轮询：管理员新加项目时，已经开着的普通账户也能收到横幅（不必等重新登录）。
   重新获得焦点时也拉一次，够轻量。 */
function startNotifPolling(ms = 45000) {
  if (state.notif.timer) clearInterval(state.notif.timer);
  state.notif.timer = setInterval(() => loadNotifications({ popup: true }), ms);
}
window.addEventListener("focus", () => {
  if (state.me) loadNotifications({ popup: true });
});

/* ------------------------------------------------ 导航 */
const ICONS = {
  dashboard: '<path d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 9h8V3h-8z"/>',
  month: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/><path d="M9 15l2 2 4-4"/>',
  worklog: '<path d="M5 3h11l3 3v15a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M15 3v4h4"/><path d="M8 12h8M8 16h6M8 8h3"/>',
  browse: '<path d="M4 4a2 2 0 0 1 2-2h6l2 3h4a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>',
  import: '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 11a3 3 0 1 0 0-6"/><path d="M18 20a6 6 0 0 0-3-5.2"/>',
  notice: '<path d="M3 11v2a1 1 0 0 0 1 1h2l4 4V6L6 10H4a1 1 0 0 0-1 1z"/><path d="M14 7.5a5 5 0 0 1 0 9"/><path d="M17.5 5a9 9 0 0 1 0 14"/>',
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
  // 仅管理员：导入项目库 / 全服通告 / 用户管理 / 设置
  if (isAdmin()) {
    items.push({ key: "import", label: "导入项目库" });
    items.push({ key: "notice", label: "全服通告" });
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
    <button class="ub-out" id="btnLogout" title="切换账号 / 退出登录">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>
    </button>
    <div class="ub-menu" id="ubMenu" hidden>
      <button type="button" data-ub="switch">
        <span class="ubm-t">切换账号</span>
        <span class="ubm-s">用另一个账号登录</span>
      </button>
      <button type="button" data-ub="logout">
        <span class="ubm-t">退出登录</span>
        <span class="ubm-s">结束当前会话</span>
      </button>
    </div>`;
  bindUserMenu();
}

/* 侧栏底部账号菜单：点右侧按钮弹出「切换账号 / 退出登录」。
   两者都清掉服务端会话后回登录页（登录页直接用新账号覆盖即可）。 */
let _ubDocBound = false;
function bindUserMenu() {
  const menu = $("#ubMenu");
  const btn = $("#btnLogout");
  if (!menu || !btn) return;
  const close = () => { menu.hidden = true; };
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    menu.hidden = !menu.hidden;
  });
  $$("[data-ub]", menu).forEach((el) => el.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    close();
    try { await api("/api/auth/logout", { method: "POST", body: {} }); } catch (e) { /* 忽略 */ }
    location.href = "/login.html";
  }));
  if (!_ubDocBound) {
    _ubDocBound = true;
    document.addEventListener("click", () => {
      const m = $("#ubMenu");
      if (m && !m.hidden) m.hidden = true;
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      const m = $("#ubMenu");
      if (m && !m.hidden) m.hidden = true;
    });
  }
}

const TITLES = {
  dashboard: ["仪表盘", "项目库整体概览"],
  month: ["月末项目统计", "各平台填报当月完成项目 → 管理员一键导出"],
  browse: ["项目库", "浏览与维护全部项目"],
  worklog: ["工作日志", "记录每日工作 · 仅自己可见"],
  import: ["导入项目库", "从 Excel 批量导入 / 更新"],
  notice: ["全服通告", "按平台定向发布提醒 · 收到的人会弹窗"],
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
    // 视图落位动画：换页时重新触发一次（重新加类名才会重放）
    view$.classList.remove("view-in");
    void view$.offsetWidth;
    view$.classList.add("view-in");
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
  bindTheme();
  try {
    const me = await api("/api/auth/me");
    state.me = me.data.user;
  } catch (e) {
    location.href = "/login.html";
    return;
  }
  renderNav();
  renderUserBox();
  const bell = $("#notifBell");
  if (bell) bell.addEventListener("click", toggleNotifPanel);
  try {
    const r = await api("/api/dashboard");
    state.dash = r.data;
    await loadTypes();
  } catch (e) {
    toast(e.message, "error");
  }
  go("dashboard");
  // 消息中心：登录后拉一次（有未读的入库通知就弹流光横幅），之后定时轮询
  await loadNotifications({ popup: true });
  startNotifPolling();
}

