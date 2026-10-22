/* 项目库统计程序 · 登录 / 注册 */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const POSTS = [
  { key: "cell", label: "细胞", readonly: false },
  { key: "mol", label: "分子", readonly: false },
  { key: "path", label: "病理", readonly: false },
  { key: "animal", label: "动物", readonly: false },
  { key: "ops", label: "运营", readonly: true },
  { key: "design", label: "设计", readonly: true },
];

let MODE = "login";        // login | register
let POSTS_LIST = POSTS;

async function api(path, { method = "GET", body = null } = {}) {
  const opt = { method, headers: {} };
  if (body !== null) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  let data;
  try { data = await res.json(); }
  catch (e) { throw new Error("服务返回异常（" + res.status + "）"); }
  if (!data.ok) throw new Error(data.error || "操作失败");
  return data;
}

function toast(msg, kind = "success") {
  const wrap = $("#toastWrap");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML = `<span>${kind === "error" ? "⚠️" : kind === "warn" ? "⚠️" : "✅"}</span><span>${esc(msg)}</span>`;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .22s ease, transform .22s ease";
    el.style.opacity = "0";
    el.style.transform = "translateY(-10px)";
    setTimeout(() => el.remove(), 240);
  }, 2800);
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* ------------------------------------------------ 渲染 */
function render() {
  const wrap = $("#formWrap");
  const tabs = $("#tabs");

  tabs.hidden = false;
  $$(".login-tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === MODE));
  if (MODE === "login") {
    wrap.innerHTML = `
      ${field("用户名", "username", "text", "请输入用户名")}
      ${field("密码", "password", "password", "请输入密码")}
      <button class="btn btn-primary login-btn" id="btnSubmit">登 录</button>`;
    bind("login");
  } else {
    wrap.innerHTML = `
      <div class="notice info" style="margin-bottom:16px">
        <span>ℹ️</span><div>自助注册只能创建<b>普通用户</b>，请选择你所在的实验平台，
          之后可在月末统计里填写本平台的完成情况。<br>
          （需要管理员账号？请让已有管理员在「用户管理」里创建。）</div>
      </div>
      ${field("用户名", "username", "text", "至少 3 个字符")}
      ${field("姓名 / 昵称", "display_name", "text", "如：张小凡（可不填）")}
      ${field("密码", "password", "password", "至少 6 位")}
      ${platformSelect()}
      <button class="btn btn-primary login-btn" id="btnSubmit">注 册</button>`;
    bind("register");
  }
}

function field(label, id, type, ph) {
  return `<div class="field login-field">
    <label>${label}</label>
    <input class="input" id="f_${id}" type="${type}" placeholder="${ph}" autocomplete="off">
  </div>`;
}

function platformSelect() {
  return `<div class="field login-field">
    <label>所属岗位 <span style="color:var(--red)">*</span></label>
    <div class="plat-pick" id="platPick">
      ${POSTS_LIST.map((p, i) => `
        <label class="plat-opt${i === 0 ? " active" : ""}${p.readonly ? " ro" : ""}">
          <input type="radio" name="platform" value="${p.key}"${i === 0 ? " checked" : ""}>
          <span>${p.label}</span>
        </label>`).join("")}
    </div>
    <div class="hint" style="margin-top:8px">运营 / 设计 为只读岗位，可查看全部数据但不能填写</div>
  </div>`;
}

function bind(mode) {
  const btn = $("#btnSubmit");
  // 平台选择高亮
  $$(".plat-opt").forEach((l) => {
    l.addEventListener("click", () => {
      $$(".plat-opt").forEach((x) => x.classList.remove("active"));
      l.classList.add("active");
    });
  });
  const submit = () => doSubmit(mode).catch((e) => toast(e.message, "error"));
  btn.addEventListener("click", submit);
  $$("#formWrap .input").forEach((i) =>
    i.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); }));
  const first = $("#formWrap .input");
  if (first) first.focus();
}

async function doSubmit(mode) {
  const username = ($("#f_username") || {}).value?.trim() || "";
  const password = ($("#f_password") || {}).value || "";
  const display_name = ($("#f_display_name") || {}).value?.trim() || "";
  if (!username) return toast("请输入用户名", "warn");
  if (!password) return toast("请输入密码", "warn");

  const btn = $("#btnSubmit");
  btn.disabled = true;
  try {
    let path = "/api/auth/login", payload = { username, password };
    if (mode === "register") {
      path = "/api/auth/register"; payload.display_name = display_name;
      const sel = $("input[name=platform]:checked");
      if (!sel) return toast("请选择所属平台", "warn");
      payload.platform = sel.value;
    }
    const r = await api(path, { method: "POST", body: payload });
    if (mode === "register") {
      toast("注册成功，请登录");
      MODE = "login"; render();
      return;
    }
    const u = r.data.user;
    toast(`欢迎，${u.display_name}（${u.role_name}）`);
    setTimeout(() => { location.href = "/index.html"; }, 420);
  } catch (e) {
    btn.disabled = false;
    toast(e.message, "error");
  }
}

/* ------------------------------------------------ 主题（明亮 / 暗黑，默认明亮） */
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
  try { localStorage.setItem("projlib.theme", next); } catch (e) {}
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (typeof document.startViewTransition === "function" && !reduced) {
    document.startViewTransition(() => applyTheme(next));
  } else {
    applyTheme(next);
  }
}
function bindTheme() {
  applyTheme(currentTheme());
  const btn = $("#themeBtn");
  if (btn) btn.addEventListener("click", toggleTheme);
}

/* ------------------------------------------------ 启动 */
async function boot() {
  bindTheme();
  $$(".login-tab").forEach((b) => b.addEventListener("click", () => {
    MODE = b.dataset.tab; render();
  }));
  try {
    const st = await api("/api/auth/status");
    if (st.data.posts && st.data.posts.length) POSTS_LIST = st.data.posts;
    try {
      const me = await api("/api/auth/me");
      if (me.data.user) { location.href = "/index.html"; return; }
    } catch (e) { /* 未登录，正常 */ }
  } catch (e) {
    $("#formWrap").innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`;
    return;
  }
  render();
}

boot();
