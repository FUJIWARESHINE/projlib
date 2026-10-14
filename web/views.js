/* 项目库统计程序 · 各视图 */
"use strict";

const VIEWS = {};

/* ------------------------------------------------ 状态图标 */
/* 所有状态格子都套一层 .sbox（固定 28px 高的盒子），
   这样「只读图标格」和「可编辑下拉格」尺寸完全一致，全表行高才会齐。
   ⚠️ 空白状态的 class 用 .st.blank，不要用 .empty——.empty 是空列表大块的样式
   （padding:46px 20px），会把整行撑到 105px。 */
function stIcon(v) {
  const s = (v || "").trim();
  if (s === "✅") return '<span class="sbox st done">✅</span>';
  if (s === "❌") return '<span class="sbox st undone">❌</span>';
  if (s === "/") return '<span class="sbox st none">/</span>';
  if (!s) return '<span class="sbox st blank">·</span>';
  return `<span class="sbox st">${esc(s)}</span>`;
}

/* 状态 → 内联下拉的样式类 */
function stClass(v) {
  const s = (v || "").trim();
  if (s === "✅") return "st-inline-done";
  if (s === "❌") return "st-inline-undone";
  if (s === "/") return "st-inline-none";
  return "st-inline-empty";
}

/* 表格内联的状态单元格：可编辑则渲染成下拉，否则退回只读图标 */
const ST_OPTS = [["", "（空）"], ["/", "/"], ["✅", "✅"], ["❌", "❌"]];
function stCell(id, plat, v) {
  const s = (v == null ? "" : String(v)).trim();
  if (!canEditPlatform(plat)) return stIcon(s);
  const opts = ST_OPTS.map(([val, lab]) =>
    `<option value="${val}"${val === s ? " selected" : ""}>${lab}</option>`).join("");
  return `<select class="st-inline ${stClass(s)}" data-id="${id}" data-plat="${plat}" title="点击修改">${opts}</select>`;
}

function typePill(ptype) {
  const label = { real: "真实", urgent: "加急", stock: "库存", repair: "返修" }[ptype] || ptype;
  return `<span class="pill ${TYPE_PILL[ptype] || ""}">${esc(label)}</span>`;
}

/* ------------------------------------------------ 导出（本机 / 服务器 二选一） */
/* 让浏览器把服务器上的文件保存到「当前这台电脑」的下载目录 */
function triggerDownload(path, name) {
  if (!path) return false;
  const a = document.createElement("a");
  a.href = "/api/download?path=" + encodeURIComponent(path);
  if (name) a.download = name;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 1500);
  return true;
}

/* 导出前询问：下载到本机 / 只存服务器。取消返回 null。 */
function askExportTarget(what) {
  return new Promise((resolve) => {
    let picked = null;
    openModal({
      title: "导出到哪里？",
      body: `
        <div class="hint" style="margin-bottom:12px">${esc(what || "统计表")}会先生成在服务器上，然后按你的选择处理：</div>
        <div class="exp-choice" data-v="local">
          <b>💻 下载到本机（这台电脑）</b>
          <span>生成后立刻用浏览器下载到你当前电脑的「下载」文件夹，不用再去服务器上取文件。</span>
        </div>
        <div class="exp-choice" data-v="server">
          <b>🗄 保存到服务器</b>
          <span>文件留在服务器的导出目录里（会记录导出历史），页面给出下载入口，随时可以取。</span>
        </div>`,
      footer: `<button class="btn btn-ghost" data-close>取消</button>`,
      onClose: () => resolve(picked),
      onMount(root) {
        root.querySelectorAll(".exp-choice").forEach((el) => el.addEventListener("click", () => {
          picked = el.dataset.v;
          closeModal();
        }));
      },
    });
  });
}

/* 导出完成弹窗：按导出目标给出不同提示与按钮 */
function showExportDone(d, target) {
  const path = d.path || "";
  const name = d.name || (path.split(/[\\/]/).pop() || "");
  const toLocal = target === "local";
  const dl = name ? `/api/download?path=${encodeURIComponent(path)}` : "";
  openModal({
    title: "导出完成",
    body: `
      <div class="notice info" style="margin-bottom:14px"><span>✅</span>
        <div>${toLocal
          ? "统计表已生成，并已<b>下载到本机</b>（浏览器默认下载目录）。没看到下载？再点下面的按钮。"
          : "统计表已<b>保存在服务器</b>的导出目录。要在本机打开，点下面的按钮下载。"}</div></div>
      <div class="kv"><span class="k">文件名</span><span style="font-size:12px;word-break:break-all">${esc(name)}</span></div>
      <div class="kv"><span class="k">保存位置</span><span style="font-size:12px;font-family:Consolas,monospace;word-break:break-all">${esc(path)}</span></div>
      ${d.missing && d.missing.length ? `
        <div class="divider"></div>
        <div class="notice warn"><span>⚠️</span>
          <div>以下编号在项目库中<b>未匹配到</b>，已在表格中标红：${esc(d.missing.join("、"))}</div></div>` : ""}`,
    footer: dl ? `<a class="btn ${toLocal ? "btn-ghost" : "btn-primary"}" href="${dl}" download>下载到本机</a>
                  <button class="btn btn-ghost" data-close>关闭</button>`
               : `<button class="btn btn-ghost" data-close>关闭</button>`,
  });
}

/* ==================================================================== 仪表盘 */
VIEWS.dashboard = async (root) => {
  const admin = isAdmin();
  const r = await api("/api/dashboard");
  state.dash = r.data;
  await loadTypes();
  const d = state.dash;

  const kpis = [
    { cls: "", label: "项目总记录", num: d.total, sub: "真实 + 加急 + 库存 + 返修" },
    ...d.by_type.map((t) => ({
      cls: { real: "green", urgent: "amber", stock: "", repair: "purple" }[t.key] || "",
      label: t.label, num: t.count,
      sub: `${t.done} 项全平台完成`,
    })),
  ];

  root.innerHTML = `
    <div class="kpi-grid">
      ${kpis.map((k) => `
        <div class="kpi ${k.cls}">
          <div class="k-label">${esc(k.label)}</div>
          <div class="k-num">${k.num}</div>
          <div class="k-sub">${esc(k.sub)}</div>
        </div>`).join("")}
    </div>

    <div class="dash-grid">
      <div class="card">
        <div class="card-head"><h3>各平台完成情况</h3>
          <span class="hint">已设计该平台的项目数（"/" 未设计不计入）</span>
        </div>
        <div class="card-body">
          ${d.platforms.map(platRow).join("")}
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h3>报价金额</h3></div>
        <div class="card-body">
          <div class="summary">
            ${d.by_type.map((t) => t.key === "repair"
              ? `<div class="sum-row" style="opacity:.7"><span>${esc(t.label)}</span><span>不计价</span></div>`
              : `<div class="sum-row"><span>${esc(t.label)}合计</span><b>${money(t.price)}</b></div>`).join("")}
            <div class="sum-total"><span>全部合计</span> <b>${money(d.total_price)}</b></div>
          </div>
          <div class="divider"></div>
          <div class="stat-row"><div class="s-name">${d.by_type.map((t) => esc(t.label)).join(" / ")}</div>
            <div class="s-val">${d.by_type.map((t) => t.count).join(" / ")}</div></div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:18px">
      <div class="card-head"><h3>快速开始</h3></div>
      <div class="card-body">
        <div class="notice info">
          <span>💡</span>
          <div>月末到了？去 <b>「月末项目统计」</b>：管理员先新建当月统计，各平台实验员登录后填写本平台完成的编号，
            最后由管理员一键导出标准统计表。
            ${admin ? "<br>要换数据？去 <b>「导入项目库」</b> 上传新的信息表；想推倒重来，去 <b>「设置 → 数据维护 → 清空项目库」</b>。"
                    : "<br>日常填报可直接在 <b>「项目库」</b> 列表里点自己平台那一列的状态。"}</div>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn btn-primary" data-go="month">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/></svg>
            月末项目统计</button>
          <button class="btn btn-ghost" data-go="browse">浏览项目库</button>
          ${admin ? '<button class="btn btn-ghost" data-go="import">导入项目库</button>' : ""}
        </div>
      </div>
    </div>`;

  root.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
};

function byTypePrice(key) {
  const t = (state.dash?.by_type || []).find((x) => x.key === key);
  return t ? t.price : 0;
}

function platRow(p) {
  const total = p.total || 0;                 // 已设计该平台的项目数（不含 "/"）
  const denom = total || 1;
  const wD = (p.done / denom * 100).toFixed(1);
  const wU = (p.undone / denom * 100).toFixed(1);
  return `
    <div class="plat-row">
      <div class="plat-name">${esc(p.label)}</div>
      <div class="plat-bar">
        <i class="p-done" style="width:${wD}%"></i>
        <i class="p-undone" style="width:${wU}%"></i>
      </div>
      <div class="plat-nums">
        <b>✅ ${p.done}</b> · <span class="u">未完成 ${p.undone}</span>
        <span style="color:var(--text3)">（完成 ${p.pct}% · 未设计 ${p.none}）</span>
      </div>
    </div>`;
}

/* ==================================================================== 项目库 */
/* 列定义：[key, 表头, 默认宽, 最小宽]。列宽由用户拖表头右边界调整，
   存 localStorage["projlib.col_w"]（只记录手动调过的列）。 */
const BROWSE_COLS = [
  ["type", "类型", 76, 56],
  ["code", "编号", 104, 72],
  ["name", "项目名", 360, 120],
  ["cell", "细胞", 74, 52],
  ["mol", "分子", 74, 52],
  ["path", "病理", 74, 52],
  ["animal", "动物", 74, 52],
  ["price", "报价", 110, 72],
  ["note", "备注", 190, 80],
  ["date", "发送日期", 100, 76],
  ["ops", "操作", 76, 60],
];
const COL_DEF = {}, COL_MIN = {};
BROWSE_COLS.forEach((c) => { COL_DEF[c[0]] = c[2]; COL_MIN[c[0]] = c[3]; });
const PLAT_COLS = ["cell", "mol", "path", "animal"];

function browseCols() {
  return BROWSE_COLS.filter((c) => c[0] !== "ops" || isAdmin());
}

/* 读取列宽偏好：{w: 生效宽度, man: 是否被手动调过} */
function loadColW() {
  let raw = {};
  try { raw = JSON.parse(loadPref("col_w", "{}")) || {}; } catch (e) { raw = {}; }
  const w = {}, man = {};
  Object.keys(COL_DEF).forEach((k) => {
    const v = Number(raw[k]);
    if (Number.isFinite(v)) { w[k] = Math.max(COL_MIN[k], Math.round(v)); man[k] = true; }
    else w[k] = COL_DEF[k];
  });
  return { w, man };
}
function saveColW(C) {
  const out = {};
  Object.keys(C.man).forEach((k) => { if (C.man[k]) out[k] = Math.round(C.w[k]); });
  savePref("col_w", JSON.stringify(out));
}

/* 应用列宽：表格总宽 = 各列之和。项目名列未被手动调过时吃掉剩余空间（默认铺满）。 */
function fitCols(table, C) {
  const wrap = table.parentElement;
  if (!wrap) return;
  const avail = Math.max(360, wrap.clientWidth - 2);
  const cols = Array.from(table.querySelectorAll("col")).map((c) => c.dataset.col);
  const others = cols.filter((k) => k !== "name").reduce((s, k) => s + C.w[k], 0);
  let nameW = C.w["name"];
  if (!C.man["name"]) nameW = Math.max(COL_MIN["name"], avail - others);
  table.style.width = (others + nameW) + "px";
  table.querySelectorAll("col").forEach((col) => {
    const k = col.dataset.col;
    col.style.width = (k === "name" ? nameW : C.w[k]) + "px";
  });
}

function nameColCurrentW(table, C) {
  const others = Array.from(table.querySelectorAll("col"))
    .filter((c) => c.dataset.col !== "name")
    .reduce((s, c) => s + C.w[c.dataset.col], 0);
  const avail = Math.max(360, (table.parentElement ? table.parentElement.clientWidth : 0) - 2);
  return Math.max(COL_MIN["name"], avail - others);
}

/* 表头右边界拖动改列宽；双击恢复该列默认宽度 */
function bindColResize(table, C) {
  $$(".col-rs", table).forEach((h) => {
    const key = h.dataset.col;
    h.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (key === "name" && !C.man["name"]) {   // 首次拖项目名列：以当前实际宽度为起点
        C.w["name"] = Math.round(nameColCurrentW(table, C));
        C.man["name"] = true;
      }
      const x0 = ev.clientX;
      let w0 = C.w[key];
      document.body.classList.add("col-resizing");
      const move = (e) => {
        C.w[key] = Math.max(COL_MIN[key], Math.min(900, Math.round(w0 + e.clientX - x0)));
        C.man[key] = true;
        fitCols(table, C);
      };
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        document.body.classList.remove("col-resizing");
        saveColW(C);
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
    h.addEventListener("dblclick", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      C.w[key] = COL_DEF[key];
      delete C.man[key];
      fitCols(table, C);
      saveColW(C);
    });
  });
}

VIEWS.browse = async (root) => {
  const B = state.browse;
  const admin = isAdmin();
  const types = await loadTypes();
  const C = loadColW();
  const cols = browseCols();
  const platOpts = [["", "全部平台"], ["cell", "细胞"], ["mol", "分子"], ["path", "病理"], ["animal", "动物"]];

  root.innerHTML = `
    <div class="card">
      <div class="toolbar">
        <div class="search-box" style="width:260px">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
          <input class="input" id="q" placeholder="搜索编号 / 项目名" value="${esc(B.search)}">
        </div>
        <select class="select" id="typeSel" style="width:150px">
          <option value="">全部类型</option>
          ${types.map((t) => `<option value="${t.key}"${B.ptype === t.key ? " selected" : ""}>${esc(t.label)}（${t.count}）</option>`).join("")}
        </select>
        <select class="select" id="platSel" style="width:140px">
          ${platOpts.map(([v, l]) => `<option value="${v}"${(B.platform || "") === v ? " selected" : ""}>${l}</option>`).join("")}
        </select>
        <select class="select" id="stSel" style="width:170px">
          <option value="">（选平台后生效）</option>
          <option value="undone">未完成（含未填）</option>
          <option value="✅">仅已完成</option>
          <option value="❌">仅标记未完成</option>
          <option value="/">仅未设计</option>
        </select>
        <select class="select" id="grpSel" style="width:132px" title="是否先按类型分组再排序">
          <option value="type"${(B.group || "type") === "type" ? " selected" : ""}>按类型分组</option>
          <option value="all"${B.group === "all" ? " selected" : ""}>全部混排</option>
        </select>
        <select class="select" id="sortSel" style="width:132px" title="排序依据">
          <option value="code"${(B.sort || "code") === "code" ? " selected" : ""}>按编号排</option>
          <option value="src"${B.sort === "src" ? " selected" : ""}>按录入顺序</option>
        </select>
        <select class="select" id="ordSel" style="width:104px" title="排序方向">
          <option value="asc"${(B.order || "asc") === "asc" ? " selected" : ""}>正序</option>
          <option value="desc"${B.order === "desc" ? " selected" : ""}>倒序</option>
        </select>
        <span class="spacer"></span>
        <button class="btn btn-ghost btn-sm" id="btnColReset" title="所有列恢复默认宽度">重置列宽</button>
        ${admin ? `
          <button class="btn btn-ghost btn-sm" id="btnExportAll">导出整库</button>
          <button class="btn btn-primary btn-sm" id="btnNew">新增项目</button>`
        : `<span class="ro-tag">只读浏览</span>`}
      </div>
      <div class="hint" style="padding:0 20px 12px">拖动表头右边界可调整列宽（双击恢复该列默认宽度），列宽按浏览器记住。</div>
      <div id="tableWrap"></div>
    </div>`;

  const load = async () => {
    $("#tableWrap").innerHTML = '<div class="loading">加载中…</div>';
    B.platform = $("#platSel").value;
    B.status = $("#stSel").value;
    B.order = $("#ordSel").value;
    B.sort = $("#sortSel").value;
    B.group = $("#grpSel").value;
    // 只看单个类型时「分组」没意义（就一组），禁用避免误解
    $("#grpSel").disabled = !!B.ptype;
    const q = new URLSearchParams({
      ptype: B.ptype, search: B.search, page: B.page, page_size: B.page_size,
      platform: B.platform, status: B.status, order: B.order || "asc",
      sort: B.sort || "code", group: B.group || "type",
    });
    try {
      const r = await api("/api/projects?" + q.toString());
      B.data = r.data;
      renderTable();
    } catch (e) {
      $("#tableWrap").innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`;
    }
  };

  function renderTable() {
    const d = B.data;
    if (!d.rows.length) {
      $("#tableWrap").innerHTML = `<div class="empty">
        <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M4 4a2 2 0 0 1 2-2h6l2 3h4a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/></svg>
        <p>没有项目。去「导入项目库」导入你的 Excel 试试。</p></div>`;
      return;
    }
    const pages = Math.max(1, Math.ceil(d.total / d.page_size));
    const colgroup = `<colgroup>${cols.map(([k]) => `<col data-col="${k}">`).join("")}</colgroup>`;
    const head = cols.map(([k, label]) => {
      const cls = PLAT_COLS.includes(k) ? "plat" : (k === "ops" ? "ops-cell" : "");
      return `<th${cls ? ` class="${cls}"` : ""} data-col="${k}">${esc(label)}`
        + `<i class="col-rs" data-col="${k}" title="拖动调整列宽 · 双击恢复默认"></i></th>`;
    }).join("");
    $("#tableWrap").innerHTML = `
      <div class="table-wrap">
        <table class="tbl tbl-fixed">
          ${colgroup}
          <thead><tr>${head}</tr></thead>
          <tbody>
            ${d.rows.map((r) => {
              // 报价列只显示报价：返修不计价 → —（「要求」统一放备注列，不再重复）
              const price = (r.ptype !== "repair" && r.price) ? money(r.price) : "—";
              const note = r.note || (r.ptype === "repair" ? r.extra : "");
              return `<tr data-id="${r.id}">
              <td>${typePill(r.ptype)}</td>
              <td><span class="code-cell">${esc(r.code)}</span>${
                (r.seq || 0) > 0
                  ? `<span class="pill pill-times" title="同一编号的第 ${r.seq + 1} 次记录（如同一个项目多次返修）">×${r.seq + 1}</span>`
                  : ""}</td>
              <td><span class="name-cell" title="${esc(r.name)}">${esc(r.name)}</span></td>
              ${PLAT_COLS.map((p) => `<td class="plat">${stCell(r.id, p, r[p])}</td>`).join("")}
              <td><span class="price-cell" title="${esc(r.extra)}">${price}</span></td>
              <td><span class="note-cell" title="${esc(note)}">${esc(note) || "—"}</span></td>
              <td><span class="date-cell">${esc(r.send_date) || "—"}</span></td>
              ${admin ? `<td class="ops-cell">
                <button class="btn btn-ghost btn-sm btn-icon" data-edit="${r.id}" title="编辑">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
                </button>
              </td>` : ""}
            </tr>`;
            }).join("")}
          </tbody>
        </table>
      </div>
      <div class="pager">
        <span>共 ${d.total} 条 · 第 ${d.page}/${pages} 页</span>
        <button class="btn btn-ghost btn-sm" data-pg="prev"${d.page <= 1 ? " disabled" : ""}>上一页</button>
        <button class="btn btn-ghost btn-sm" data-pg="next"${d.page >= pages ? " disabled" : ""}>下一页</button>
      </div>`;

    const table = $("#tableWrap table");
    fitCols(table, C);
    bindColResize(table, C);

    $$("[data-edit]").forEach((b) => b.addEventListener("click", () => editProject(Number(b.dataset.edit), load)));
    bindInline();
    $$("[data-pg]").forEach((b) => b.addEventListener("click", () => {
      B.page += b.dataset.pg === "next" ? 1 : -1;
      if (B.page < 1) B.page = 1;
      load();
    }));
  }

  /* 表格内联改状态：有该平台权限的用户直接点选，不必进编辑弹窗 */
  function bindInline() {
    $$("select.st-inline").forEach((sel) => {
      sel.addEventListener("change", async () => {
        const { id, plat } = sel.dataset;
        const v = sel.value;
        const prev = (B.data.rows || []).find((x) => String(x.id) === String(id))?.[plat] ?? "";
        sel.disabled = true;
        try {
          await api(`/api/projects/${id}/status`, { method: "PUT", body: { platform: plat, value: v } });
          const row = (B.data.rows || []).find((x) => String(x.id) === String(id));
          if (row) row[plat] = v;                       // 同步本地数据，避免整页重载
          sel.classList.remove("st-inline-empty", "st-inline-done", "st-inline-undone", "st-inline-none");
          sel.classList.add(stClass(v));
          toast("已更新");
        } catch (e) {
          sel.value = prev;                             // 失败回滚
          toast(e.message, "error");
        } finally { sel.disabled = false; }
      });
    });
  }

  $("#q").addEventListener("input", debounce((e) => { B.search = e.target.value; B.page = 1; load(); }, 320));
  $("#typeSel").addEventListener("change", (e) => { B.ptype = e.target.value; B.page = 1; load(); });
  $("#platSel").addEventListener("change", () => { B.page = 1; load(); });
  $("#stSel").addEventListener("change", () => { B.page = 1; load(); });
  $("#grpSel").addEventListener("change", () => { savePref("browse_group", $("#grpSel").value); B.page = 1; load(); });
  $("#sortSel").addEventListener("change", () => { savePref("browse_sort", $("#sortSel").value); B.page = 1; load(); });
  $("#ordSel").addEventListener("change", () => { savePref("browse_order", $("#ordSel").value); B.page = 1; load(); });
  $("#btnColReset").addEventListener("click", () => {
    Object.keys(COL_DEF).forEach((k) => { C.w[k] = COL_DEF[k]; delete C.man[k]; });
    saveColW(C);
    const t = $("#tableWrap table");
    if (t) fitCols(t, C);
    toast("列宽已恢复默认");
  });
  // 窗口尺寸变化时重新分配剩余宽度
  if (B._onResize) window.removeEventListener("resize", B._onResize);
  B._onResize = debounce(() => {
    const t = $("#tableWrap table");
    if (t) fitCols(t, C);
  }, 140);
  window.addEventListener("resize", B._onResize);
  if (admin) {
    $("#btnNew").addEventListener("click", () => editProject(null, load));
    $("#btnExportAll").addEventListener("click", async () => {
      const target = await askExportTarget("整库汇总表");
      if (!target) return;
      try {
        const r = await api("/api/export/all?ptype=" + encodeURIComponent(B.ptype));
        if (target === "local") triggerDownload(r.data.path, r.data.name);
        toast(target === "local" ? "已导出，正在下载到本机" : "已导出到服务器");
        showExportDone({ path: r.data.path, name: r.data.name, missing: [] }, target);
      } catch (e) { toast(e.message, "error"); }
    });
  }

  await load();
};

function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function editProject(id, reload) {
  const isNew = id == null;
  const row = isNew
    ? { ptype: state.browse.ptype || "urgent", code: "", name: "", cell: "", mol: "", path: "", animal: "", extra: "", price: 0, audit_sun: "", audit_xu: "", send_date: "", note: "" }
    : null;

  const doOpen = (data) => {
    const stOpts = (v) => ["", "/", "✅", "❌"].map((s) =>
      `<option value="${s}"${(data[s === "" ? "__none__" : ""] , (v || "") === s) ? " selected" : ""}>${s === "" ? "（空）" : s}</option>`).join("");
    openModal({
      title: isNew ? "新增项目" : "编辑项目",
      wide: true,
      body: `
        ${isNew ? "" : `<div class="hint" style="margin:0 0 12px">改「项目类型」就能把项目换个分类（如库存 → 加急），不必删除后重建。</div>`}
        <div class="grid2">
          <div class="field"><label>项目类型</label>
            <select class="select" id="f_ptype">
              ${state.types.map((t) => `<option value="${t.key}"${data.ptype === t.key ? " selected" : ""}>${esc(t.label)}</option>`).join("")}
            </select></div>
          <div class="field"><label>项目编号</label>
            <input class="input" id="f_code" value="${esc(data.code)}"></div>
        </div>
        <div class="field" style="margin-top:14px"><label>项目名</label>
          <input class="input" id="f_name" value="${esc(data.name)}"></div>
        <div class="grid4" style="margin-top:14px">
          <div class="field"><label>细胞</label><select class="select" id="f_cell">${stOpts2(data.cell)}</select></div>
          <div class="field"><label>分子</label><select class="select" id="f_mol">${stOpts2(data.mol)}</select></div>
          <div class="field"><label>病理</label><select class="select" id="f_path">${stOpts2(data.path)}</select></div>
          <div class="field"><label>动物</label><select class="select" id="f_animal">${stOpts2(data.animal)}</select></div>
        </div>
        <div class="grid3" style="margin-top:14px">
          <div class="field" style="grid-column:span 2"><label id="lb_extra">报价说明</label>
            <input class="input" id="f_extra" value="${esc(data.extra)}"></div>
          <div class="field"><label>报价金额（数字）</label>
            <input class="input money" id="f_price" value="${data.price || ""}"></div>
        </div>
        <div class="grid3" style="margin-top:14px">
          <div class="field"><label>孙审核</label><select class="select" id="f_sun">${stOpts2(data.audit_sun, true)}</select></div>
          <div class="field"><label>徐审核</label><select class="select" id="f_xu">${stOpts2(data.audit_xu, true)}</select></div>
          <div class="field"><label>发送日期</label>
            <input class="input" id="f_date" value="${esc(data.send_date)}" placeholder="2026.1.16"></div>
        </div>
        <div class="field" style="margin-top:14px"><label>备注</label>
          <input class="input" id="f_note" value="${esc(data.note)}"></div>`,
      footer: `${isNew ? "" : '<button class="btn btn-danger" data-del>删除</button>'}<span style="flex:1"></span>
        <button class="btn btn-ghost" data-close>取消</button>
        <button class="btn btn-primary" data-save>保存</button>`,
      onMount(root) {
        // 「报价说明 / 返修要求」按所选类型自动换名（同一字段，语义不同）
        const lbExtra = $("#lb_extra", root);
        const syncExtraLabel = () => {
          const isRepair = $("#f_ptype").value === "repair";
          lbExtra.textContent = isRepair ? "返修要求" : "报价说明（原文）";
          $("#f_price").closest(".field").style.visibility = isRepair ? "hidden" : "visible";
        };
        $("#f_ptype").addEventListener("change", syncExtraLabel);
        syncExtraLabel();
        // 编号统一大写（项目库编号均为大写，避免大小写不一致导致的重复/漏配）
        const codeInput = $("#f_code", root);
        if (codeInput && !codeInput.readOnly) {
          codeInput.addEventListener("input", () => {
            const p = codeInput.selectionStart;
            codeInput.value = codeInput.value.toUpperCase();
            codeInput.setSelectionRange(p, p);
          });
        }
        $("[data-save]", root).addEventListener("click", async () => {
          const payload = {
            ptype: $("#f_ptype").value, code: $("#f_code").value.trim().toUpperCase(), name: $("#f_name").value.trim(),
            cell: $("#f_cell").value, mol: $("#f_mol").value, path: $("#f_path").value, animal: $("#f_animal").value,
            extra: $("#f_extra").value, price: parseFloat($("#f_price").value) || 0,
            audit_sun: $("#f_sun").value, audit_xu: $("#f_xu").value,
            send_date: $("#f_date").value.trim(), note: $("#f_note").value,
          };
          if (!payload.code) return toast("项目编号不能为空", "warn");
          try {
            if (isNew) {
              // 同类型下已有同编号时先确认：同一编号允许多条（多次返修），
              // 但直接再新增多半是「想改归属」而不是真想再记一条。
              const chk = await api(`/api/projects?ptype=${encodeURIComponent(payload.ptype)}`
                + `&search=${encodeURIComponent(payload.code)}&page_size=200`);
              const same = (chk.data.rows || []).filter((r) => r.code === payload.code);
              if (same.length) {
                const zh = { real: "真实", urgent: "加急", stock: "库存", repair: "返修" }[payload.ptype] || payload.ptype;
                const ok2 = confirm(`「${payload.code}」在「${zh}」下已经有 ${same.length} 条记录。\n\n`
                  + `· 若只是想改归属/内容：取消，然后编辑原记录（列表右侧的铅笔按钮）。\n`
                  + `· 若确实要再记一条（如再次返修）：确定，会追加为新的第 ${same.length + 1} 条。`);
                if (!ok2) return;
              }
              await api("/api/projects", { method: "POST", body: payload });
            } else {
              await api("/api/projects/" + id, { method: "PUT", body: payload });
            }
            closeModal(); toast("已保存"); reload();
          } catch (e) { toast(e.message, "error"); }
        });
        const delBtn = $("[data-del]", root);
        if (delBtn) delBtn.addEventListener("click", async () => {
          if (!confirm("确定删除该项目？此操作不可恢复。")) return;
          try { await api("/api/projects/" + id, { method: "DELETE" }); closeModal(); toast("已删除"); reload(); }
          catch (e) { toast(e.message, "error"); }
        });
      },
    });
  };

  if (isNew) doOpen(row);
  else api("/api/projects/" + id).then((r) => doOpen(r.data)).catch((e) => toast(e.message, "error"));
}

function stOpts2(v, noUndone) {
  const opts = ["", "/", "✅"];
  if (!noUndone) opts.push("❌");
  return opts.map((s) => `<option value="${s}"${(v || "") === s ? " selected" : ""}>${s === "" ? "（空）" : s}</option>`).join("");
}

/* ==================================================================== 导入 */
VIEWS.import = async (root) => {
  const I = state.import;
  root.innerHTML = `
    <div class="notice info">
      <span>💡</span>
      <div>导入项目信息表：程序会自动识别
        <b>真实 / 加急 / 返修 / 库存</b> 各张工作表，按「类型 + 项目编号」覆盖更新——
        编号已存在的更新，新的则新增。源文件不会被修改。</div>
    </div>

    <div class="card">
      <div class="card-head"><h3>选择文件</h3></div>
      <div class="card-body">
        <div class="drop-zone" id="dropZone">
          <div class="big">点击选择 Excel 文件，或把文件拖到这里</div>
          <div class="sm">支持 .xlsx（含名为 真实 / 加急 / 返修 / 库存 的工作表）</div>
          <input type="file" id="fileInput" accept=".xlsx,.xlsm" style="display:none">
        </div>
      </div>
    </div>

    <div class="card" id="previewCard" style="display:none">
      <div class="card-head"><h3>识别结果预览</h3>
        <div style="display:flex;gap:8px">
          <button class="btn btn-ghost btn-sm" id="btnUpsert">导入 / 更新</button>
          <button class="btn btn-danger btn-sm" id="btnRebuild">清空后全量重建</button>
        </div>
      </div>
      <div class="card-body" id="previewBody"></div>
    </div>

    <div class="card">
      <div class="card-head"><h3>当前库状态</h3>
        <button class="btn btn-ghost btn-sm" id="btnOpenDir">查看数据目录</button>
      </div>
      <div class="card-body" id="infoBody"><div class="loading">加载中…</div></div>
    </div>`;

  const loadInfo = async () => {
    try {
      const r = await api("/api/system/info");
      const d = r.data;
      const dash = await api("/api/dashboard");
      $("#infoBody").innerHTML = `
        <div class="grid3">
          ${dash.data.by_type.map((t) => `
            <div class="kv"><span class="k">${esc(t.label)}</span><b>${t.count} 条</b></div>`).join("")}
        </div>
        <div class="divider"></div>
        <div class="kv"><span class="k">数据目录</span><span style="font-size:12px;font-family:Consolas,monospace">${esc(d.data_dir)}</span></div>
        <div class="kv"><span class="k">上次导入来源</span><span style="font-size:12px">${esc(maskFile(d.source_file))}</span></div>
        <div class="kv"><span class="k">上次导入时间</span><span>${esc(d.last_import || "—")}</span></div>`;
    } catch (e) {
      $("#infoBody").innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`;
    }
  };

  $("#btnOpenDir").addEventListener("click", async () => {
    try {
      const r = await api("/api/system/data-dir");
      if (r.data && r.data.no_gui) toast("数据目录：服务器数据卷 /data（详见「设置」页）", "info", 5000);
      else toast("已在服务器上打开数据目录");
    } catch (e) { toast(e.message, "error"); }
  });

  function showImported(d) {
    const labelMap = { real: "真实", urgent: "加急", stock: "库存", repair: "返修" };
    const reps = Object.entries(d.repeats || {});
    const repTip = reps.length
      ? `<div class="hint" style="margin-top:8px">其中 ${reps.length} 个编号在表里出现多次
         （同一项目多次返修 / 多行报价），已<b>全部保留</b>，没有丢行：
         ${reps.slice(0, 8).map(([c, n]) => `${esc(c)}×${n + 1}`).join("、")}${reps.length > 8 ? " …" : ""}</div>`
      : "";
    $("#previewCard").style.display = "";
    $("#previewBody").innerHTML = `
      <div class="notice info" style="margin-bottom:12px"><span>✅</span>
        <div>导入完成：新增 <b>${d.created||0}</b> 条，更新 <b>${d.updated||0}</b> 条${d.total_rows ? `，共处理 <b>${d.total_rows}</b> 行` : ""}。</div></div>
      <div class="sheet-preview">
        ${Object.entries(d.by_type || {}).map(([k, n]) => `
          <div class="sheet-card">
            <h4>${typePill(k)} ${esc(labelMap[k] || k)}</h4>
            <div class="n">${n}</div><div class="lbl">条记录</div>
          </div>`).join("")}
      </div>${repTip}`;
    loadInfo();
  }

  // 拖拽 / 点击上传 → 预览
  const dz = $("#dropZone");
  const fi = $("#fileInput");
  dz.addEventListener("click", () => fi.click());
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault(); dz.classList.remove("over");
    const f = e.dataTransfer.files[0];
    if (f) uploadFile(f);
  });
  fi.addEventListener("change", () => { if (fi.files[0]) uploadFile(fi.files[0]); });

  async function uploadFile(file) {
    const text = await file.text ? null : null;
    const reader = new FileReader();
    reader.onload = async () => {
      state.import.pending = { content: reader.result, filename: file.name };
      try {
        const r = await api("/api/import/preview", { method: "POST", body: { content: reader.result, filename: file.name } });
        renderPreview(r.data);
      } catch (e) { toast(e.message, "error"); }
    };
    reader.readAsDataURL(file);
  }

  function renderPreview(data) {
    const labelMap = { real: "真实", urgent: "加急", stock: "库存", repair: "返修" };
    $("#previewCard").style.display = "";
    const sheets = data.sheets || [];
    const total = Object.values(data.by_type || {}).reduce((a, b) => a + b, 0);
    $("#previewBody").innerHTML = `
      <div class="notice info" style="margin-bottom:12px"><span>🔍</span>
        <div>共识别 <b>${total}</b> 条项目记录，来自 ${sheets.filter(s=>!s.skipped).length} 张工作表。
          确认无误后点右上角「导入 / 更新」写入数据库。</div></div>
      <div class="sheet-preview">
        ${sheets.map((s) => `
          <div class="sheet-card">
            <h4>${s.type ? typePill(s.type) + " " + esc(labelMap[s.type] || "") : '<span class="pill">忽略</span> ' + esc(s.sheet)}</h4>
            <div class="n">${s.rows}</div><div class="lbl">${s.skipped ? "未识别类型，跳过" : "条记录"}</div>
          </div>`).join("")}
      </div>`;
    $("#btnUpsert").onclick = () => runImport("upsert");
    $("#btnRebuild").onclick = () => runImport("rebuild");
  }

  async function runImport(mode) {
    const p = state.import.pending;
    if (!p) return toast("请先选择文件", "warn");
    if (mode === "rebuild" && !confirm("「清空后全量重建」会先删除当前库里的全部项目，\n再用这份 Excel 重新写入。确定继续？")) return;
    const btns = [$("#btnUpsert"), $("#btnRebuild")];
    btns.forEach((b) => b.disabled = true);
    try {
      const r = await api("/api/import/run", { method: "POST", body: { content: p.content, filename: p.filename, mode } });
      showImported(r.data);
      toast("导入成功");
      state.dash = null;
    } catch (e) { toast(e.message, "error"); }
    finally { btns.forEach((b) => b.disabled = false); }
  }

  await loadInfo();
};

/* ==================================================================== 月末项目统计 */
VIEWS.month = async (root) => {
  const M = state.month;
  let listData;
  try {
    const r = await api("/api/months");
    listData = r.data;
  } catch (e) {
    root.innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`;
    return;
  }
  M.list = listData.months || [];

  // 默认选中第一个月份
  if (!M.mid || !M.list.some((m) => m.id === M.mid)) {
    M.mid = M.list.length ? M.list[0].id : 0;
  }

  const admin = isAdmin();
  root.innerHTML = `
    <div class="month-layout">
      <div class="card month-side">
        <div class="card-head"><h3>统计月份</h3>
          ${admin ? '<button class="btn btn-primary btn-sm" id="btnNewMonth">新建月份</button>' : ""}
        </div>
        <div class="card-body" id="monthList"></div>
      </div>
      <div class="month-main" id="monthMain"></div>
    </div>`;

  renderMonthList();

  if (admin) {
    $("#btnNewMonth").addEventListener("click", newMonthDialog);
  }

  if (!M.mid) {
    $("#monthMain").innerHTML = `<div class="card"><div class="empty">
      <svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/></svg>
      <p>${admin ? "还没有任何月份统计。点左侧「新建月份」创建，比如「2026年9月」。" : "还没有月份统计，请等待管理员创建。"}</p>
      </div></div>`;
    return;
  }
  await loadMonth();
};

function renderMonthList() {
  const M = state.month;
  const el = $("#monthList");
  if (!el) return;
  if (!M.list.length) {
    el.innerHTML = `<div class="empty" style="padding:20px 0"><p style="font-size:12px">暂无月份</p></div>`;
    return;
  }
  el.innerHTML = M.list.map((m) => `
    <div class="month-item${m.id === M.mid ? " active" : ""}" data-mid="${m.id}">
      <div class="month-item-row1">
        <span class="mi-name" title="${esc(m.name)}">${esc(m.name)}</span>
        <span class="mi-st ${m.status}">${esc(m.status_name)}</span>
      </div>
      ${m.exported_at ? `<div class="mi-meta"><span class="mi-exp">已导出</span></div>` : ""}
    </div>`).join("");
  $$("[data-mid]").forEach((el2) => el2.addEventListener("click", () => {
    state.month.mid = Number(el2.dataset.mid);
    renderMonthList();
    loadMonth();
  }));
}

/* 重新渲染后把滚动位置还原。
   —— 拖动 / 编辑 / 删除这类「原地操作」绝不能把页面弹回顶部：
      ① 记下被操作那条编号此刻的视口位置，渲染后把它对回原位（跨平台拖动导致卡片高度变化也能对准）；
      ② 记不住锚点（比如整条被删了）就退回原来的 scrollTop，超出新内容高度时浏览器会自动收敛；
      ③ 只有「切换月份 / 进入页面」才回顶部。 */
function restoreMonthScroll(inPlace, anchorEid, anchorTop, keepTop) {
  const scroller = $("#view");
  if (!scroller) return;
  if (!inPlace) { scroller.scrollTop = 0; return; }
  if (anchorEid && anchorTop != null) {
    const el = document.querySelector(`.entry-row[data-eid="${anchorEid}"]`);
    if (el) {
      scroller.scrollTop += el.getBoundingClientRect().top - anchorTop;
      return;
    }
  }
  scroller.scrollTop = keepTop;
}

/* 拉取并渲染月份详情。
   opts.inPlace   —— 原地操作（拖动/编辑/删除/锁定）：**不清空旧内容**，等新数据回来一次性替换，
                     避免内容瞬间塌陷导致滚动条被顶到顶部，也不会闪一下「加载中…」。
   opts.anchorEid —— 原地操作时被改的那条记录 id，用于把它的视口位置对回原位。 */
async function loadMonth(opts = {}) {
  const wrap = $("#monthMain");
  if (!wrap) return;
  const inPlace = !!opts.inPlace;
  const anchorEid = Number(opts.anchorEid) || 0;
  const scroller = $("#view");
  const keepTop = scroller ? scroller.scrollTop : 0;
  const anchorEl = anchorEid
    ? document.querySelector(`.entry-row[data-eid="${anchorEid}"]`) : null;
  const anchorTop = anchorEl ? anchorEl.getBoundingClientRect().top : null;

  if (!inPlace) wrap.innerHTML = '<div class="loading">加载中…</div>';
  let d;
  try {
    const r = await api("/api/months/" + state.month.mid);
    d = r.data;
    state.month.detail = d;
  } catch (e) {
    // 原地刷新失败时保留旧内容，只弹提示，别把用户正在看的表换成错误页
    if (inPlace) { toast(e.message, "error"); return; }
    wrap.innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`;
    return;
  }

  const admin = isAdmin();
  const month = d.month;
  const myPlat = myPlatform();
  const locked = month.status === "locked";

  wrap.innerHTML = `
    <div class="card month-head-card">
      <div class="mh-left">
        <h3>${esc(month.name)}</h3>
        <div class="mh-meta">
          <span class="mi-st ${month.status}">${esc(month.status_name)}</span>
          <span>共 <b>${d.total}</b> 项</span>
          ${!locked && d.platforms.some((p) => p.can_edit)
            ? `<span class="mh-hint">💡 编号按住可直接拖到其他类型 / 平台</span>` : ""}
          ${month.exported_at ? `<span>上次导出 ${esc(month.exported_at)}</span>` : ""}
        </div>
      </div>
      <div class="mh-actions">
        ${admin ? `
          <button class="btn btn-ghost btn-sm" id="btnEditMonth">编辑月份</button>
          <button class="btn btn-ghost btn-sm" id="btnLockMonth">${locked ? "解锁填报" : "锁定填报"}</button>
          <button class="btn btn-ghost btn-sm" id="btnDelMonth">删除月份</button>
          <button class="btn btn-primary" id="btnExportMonth">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>
            导出统计表</button>` : ""}
      </div>
    </div>

    <div class="plat-grid">
      ${d.platforms.map((p) => platCard(p, d, locked)).join("")}
    </div>`;
  equalizeCards();
  restoreMonthScroll(inPlace, anchorEid, anchorTop, keepTop);

  if (admin) {
    $("#btnExportMonth").addEventListener("click", () => exportMonth(month));
    $("#btnEditMonth").addEventListener("click", () => editMonthDialog(month));
    $("#btnLockMonth").addEventListener("click", async () => {
      try {
        await api(`/api/months/${month.id}/lock`, { method: "POST", body: { locked: !locked } });
        toast(locked ? "已解锁填报" : "已锁定填报");
        loadMonth({ inPlace: true });
      } catch (e) { toast(e.message, "error"); }
    });
    $("#btnDelMonth").addEventListener("click", async () => {
      if (!confirm(`确定删除「${month.name}」及其全部填报数据？此操作不可恢复。`)) return;
      try {
        await api("/api/months/" + month.id, { method: "DELETE" });
        toast("已删除"); state.month.mid = 0; go("month");
      } catch (e) { toast(e.message, "error"); }
    });
  }

  // 绑定各平台卡片操作
  $$("[data-addplat]").forEach((btn) => {
    btn.addEventListener("click", () => addEntryDialog(btn.dataset.addplat));
  });
  $$("[data-editcodes]").forEach((btn) => {
    btn.addEventListener("click", () => editCodesDialog(btn.dataset.editcodes));
  });
  $$("[data-delentry]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api(`/api/months/${month.id}/entries/${btn.dataset.delentry}`,
          { method: "DELETE" });
        toast("已移除"); loadMonth({ inPlace: true });
      } catch (e) { toast(e.message, "error"); }
    });
  });
  $$("[data-editentry]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      editEntryDialog(Number(btn.dataset.editentry));
    });
  });
}

/* 让所有平台卡片（含跨行）统一到同一高度：
   先清除上次的固定高度，量出各卡片的最大内容高度，再统一设定。
   内容超出时由卡片内部的 .plat-card-body 自行滚动。
   上限 720px：常见填报量（十几条）能完整显示，再多则滚动。 */
function equalizeCards() {
  const cards = $$(".plat-card");
  if (!cards.length) return;
  cards.forEach((c) => { c.style.height = ""; });
  const maxBody = Math.max(...cards.map((c) => {
    const body = c.querySelector(".plat-card-body");
    return body ? body.scrollHeight : 0;
  }));
  const headH = cards[0].querySelector(".plat-card-head")?.offsetHeight || 0;
  const target = Math.min(maxBody + headH + 2, 720);
  cards.forEach((c) => { c.style.height = target + "px"; });
}
window.addEventListener("resize", debounce(equalizeCards, 200));

function platCard(p, d, locked) {
  const canEdit = p.can_edit && !locked;
  const TYPES = [["real", "真实"], ["urgent", "加急"], ["stock", "库存"], ["repair", "返修"]];
  return `
    <div class="card plat-card" data-platform="${p.key}" data-canedit="${canEdit ? 1 : 0}">
      <div class="plat-card-head" data-p="${p.key}">
        <div class="pc-title">
          <span class="pc-dot"></span>${esc(p.label)}
          <span class="pc-count">${p.count} 项</span>
        </div>
        ${canEdit
          ? `<span class="pc-acts">
               <button class="btn btn-ghost btn-sm" data-editcodes="${p.key}"
                       title="把本平台四类编号列出来一起改">编辑编号</button>
               <button class="btn btn-ghost btn-sm" data-addplat="${p.key}">+ 添加编号</button>
             </span>`
          : `<span class="pc-lock">${locked ? "已锁定" : (p.can_edit ? "" : "只读")}</span>`}
      </div>
      <div class="plat-card-body">
        <div class="type-grid">
          ${TYPES.map(([k, l]) => {
            const items = p.items.filter((it) => it.ptype === k);
            return `
            <div class="type-col t-${k}" data-ptype="${k}" data-platform="${p.key}">
              <div class="type-col-head">
                <span class="pill ${TYPE_PILL[k]}">${l}</span>
                <span class="type-col-count">${items.length || ""}</span>
              </div>
              <div class="type-col-list">
                ${items.length ? items.map((it) => `
                  <div class="entry-row" data-eid="${it.id}" data-ptype="${it.ptype}"
                       data-platform="${p.key}"${canEdit ? ' draggable="true"' : ""}
                       title="${canEdit ? "可拖动改归类；右上「编辑编号」可整平台一起改" : ""}">
                    <div class="er-main">
                      <span class="er-code">${esc(it.code)}</span>
                      ${canEdit ? `<span class="er-acts">
                        <button class="er-edit" data-editentry="${it.id}" title="编辑编号 / 类型" draggable="false">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4z"/></svg>
                        </button>
                        <button class="er-del" data-delentry="${it.id}" title="移除" draggable="false">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                      </button>
                      </span>` : ""}
                    </div>
                    <div class="er-name" title="${esc(it.name)}">
                      ${esc(it.name) || '<span class="er-unmatched">库中未找到</span>'}
                    </div>
                    ${it.price && it.ptype !== "repair"
                      ? `<div class="er-foot"><span class="er-price">${money(it.price)}</span></div>` : ""}
                  </div>`).join("")
                  : '<div class="type-empty">—</div>'}
              </div>
            </div>`;
          }).join("")}
        </div>
      </div>
    </div>`;
}

/* ---------------------------------------------------------------- 月末：拖动改归类
   场景：实验员把编号填到了错误的类型（比如本该「加急」填进了「库存」）。
   直接按住编号行拖到目标类型列即可改归类；拖到其他平台卡（有权限时）则改平台。
   统一走 PUT /api/months/<mid>/entries/<eid>，服务端再校验权限与锁定状态。 */
const ENTRY_DRAG = { eid: 0, platform: "", ptype: "" };
let entryDropEl = null;

function entryDropTarget(e) {
  const t = e.target;
  if (!t || !t.closest) return null;
  const col = t.closest(".type-col[data-ptype]");
  if (col && col.closest(".plat-card")?.dataset.canedit === "1") return col;
  const card = t.closest(".plat-card[data-platform]");
  if (card && card.dataset.canedit === "1") return card;
  return null;
}

function setEntryDropHl(el) {
  if (entryDropEl === el) return;
  if (entryDropEl) entryDropEl.classList.remove("drop-hover");
  entryDropEl = el;
  if (el) el.classList.add("drop-hover");
}

document.addEventListener("dragstart", (e) => {
  const row = e.target.closest && e.target.closest('.entry-row[data-eid][draggable="true"]');
  if (!row) return;
  ENTRY_DRAG.eid = Number(row.dataset.eid);
  ENTRY_DRAG.platform = row.dataset.platform;
  ENTRY_DRAG.ptype = row.dataset.ptype;
  row.classList.add("dragging");
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(row.dataset.eid));
  }
});

document.addEventListener("dragend", () => {
  $$(".entry-row.dragging").forEach((r) => r.classList.remove("dragging"));
  setEntryDropHl(null);
  ENTRY_DRAG.eid = 0;
});

document.addEventListener("dragover", (e) => {
  if (!ENTRY_DRAG.eid) return;
  const tgt = entryDropTarget(e);
  if (!tgt) { setEntryDropHl(null); return; }
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
  setEntryDropHl(tgt);
});

document.addEventListener("dragleave", (e) => {
  if (!ENTRY_DRAG.eid) return;
  const to = e.relatedTarget;
  if (!to || !to.closest || !to.closest("#monthMain")) setEntryDropHl(null);
});

document.addEventListener("drop", async (e) => {
  if (!ENTRY_DRAG.eid) return;
  const tgt = entryDropTarget(e);
  setEntryDropHl(null);
  if (!tgt) return;
  e.preventDefault();
  const eid = ENTRY_DRAG.eid;
  const mid = state.month.mid;
  ENTRY_DRAG.eid = 0;
  if (!mid) return;
  const isCol = tgt.classList.contains("type-col");
  const platform = tgt.dataset.platform;
  const ptype = isCol ? tgt.dataset.ptype : "";
  if (platform === ENTRY_DRAG.platform && (!ptype || ptype === ENTRY_DRAG.ptype)) return;
  try {
    const r = await api(`/api/months/${mid}/entries/${eid}`, {
      method: "PUT",
      body: ptype ? { platform, ptype } : { platform },
    });
    const dd = r.data || {};
    if (dd.unchanged) return;
    toast(`已移动到「${dd.platform_label || platform} · ${dd.type_label || ""}」`);
    loadMonth({ inPlace: true, anchorEid: eid });   // 别把页面弹回顶部
  } catch (err) { toast(err.message, "error"); }
});

/* 添加 / 编辑某平台当月填报的编号 —— 同一个弹窗（四类并列），两种模式：
     mode = "add"  空白起手，提交走 POST（只增不改，已存在的自动跳过）
     mode = "edit" **四类现有编号全部回填**，提交走 PUT（整平台同步）：
                   框里是什么就存成什么 —— 删掉的行 = 移除该编号、
                   剪切到别的类型框 = 改归类、新加一行 = 新增。
                   所以不用一条一条点铅笔。
   ⚠️ edit 模式下清空某个类型框 = 移除该类型下所有编号，提交前会二次确认。 */
function entryCodesDialog(platform, mode) {
  const M = state.month;
  const editing = mode === "edit";
  const platLabel = PLATFORM_LABEL[platform] || platform;
  const TYPES = [["real", "真实"], ["urgent", "加急"], ["stock", "库存"], ["repair", "返修"]];

  // 现有填报（edit 模式用于回填与变更比对；add 模式为空）
  const plat = editing
    ? ((state.month.detail?.platforms || []).find((x) => x.key === platform) || { items: [] })
    : { items: [] };
  const items = plat.items || [];
  const codesOf = (k) => items.filter((it) => it.ptype === k).map((it) => it.code);
  const snapshot = (list) => list
    .map((g) => `${g.ptype}|${[...g.codes].map((c) => String(c).toUpperCase()).sort().join(",")}`)
    .sort().join(";");
  const beforeSig = snapshot(TYPES.map(([k]) => ({ ptype: k, codes: codesOf(k) })));

  openModal({
    title: editing
      ? `编辑 ${platLabel} 平台的填报编号`
      : `添加 ${platLabel} 平台完成的编号`,
    wide: true,
    body: `
      <div class="notice info" style="margin-bottom:14px"><span>${editing ? "✏️" : "💡"}</span>
        <div>${editing
          ? `本平台当月四类编号已<b>全部列出</b>，直接在框里改，改完点保存一次到位：
             删掉某行 = 移除该编号；把它剪切到别的类型框 = 改归类；新加一行 = 新增；
             某个类型框清空即表示该类型下没有编号。`
          : `向 <b>${esc(platLabel)}</b> 平台一次性添加当月完成的编号：<b>四类可同时填写</b>，
             哪个类型下有编号就填在对应框里（用不到的留空即可）。`}
          <br>每行一个编号，也可用逗号 / 空格分隔；编号不区分大小写（填 <code>a384</code> 也能匹配到 <code>A384</code>）。
          <br><b>编号必须带字母前缀</b>，如 <code>A539</code>；只写数字（<code>539</code>）保存时会提示补全。</div></div>
      <div class="type-grid">
        ${TYPES.map(([k, l]) => {
          const cur = codesOf(k);
          const rows = Math.min(Math.max(cur.length + 1, 4), 12);
          return `
          <div class="type-col t-${k}">
            <div class="type-col-head">
              <span class="pill ${TYPE_PILL[k]}">${l}</span>
              <span class="type-col-count" data-count="${k}" data-orig="${cur.length}">${editing && cur.length ? `${cur.length} 个` : ""}</span>
            </div>
            <textarea class="textarea code-area type-input" data-ptype="${k}" rows="${rows}"
              placeholder="${l}编号">${editing ? esc(cur.join("\n")) : ""}</textarea>
          </div>`;
        }).join("")}
      </div>`,
    footer: `<span style="flex:1"></span>
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" data-add>${editing ? "保存修改" : `添加到 ${esc(platLabel)} 平台`}</button>`,
    onMount(root) {
      const tas = $$(".type-input", root);
      const refresh = () => {
        let total = 0;
        tas.forEach((t) => {
          const n = splitCodes(t.value).length;
          total += n;
          const badge = $(`[data-count="${t.dataset.ptype}"]`, root);
          if (badge) {
            // 编辑模式下跟「原来的条数」对比一下，改了哪些类型一眼能看出来
            const orig = Number(badge.dataset.orig || 0);
            badge.textContent = n ? `${n} 个` : "";
            if (editing && n !== orig) badge.textContent = `${n} 个（原 ${orig}）`;
          }
        });
        const btn = $("[data-add]", root);
        if (!btn) return;
        if (editing) {
          btn.textContent = total ? `保存修改（共 ${total} 个）` : "保存修改（将清空本平台）";
        } else {
          btn.textContent = total
            ? `添加到 ${platLabel} 平台（共 ${total} 个）`
            : `添加到 ${platLabel} 平台`;
        }
      };
      tas.forEach((ta) => {
        // 编号输入自动转大写（项目库编号均为大写；后端也会再兜底一次）
        ta.addEventListener("input", () => {
          const p = ta.selectionStart;
          ta.value = ta.value.toUpperCase();
          ta.setSelectionRange(p, p);
          const col = ta.closest(".type-col");   // 补全前缀后自动取消标红
          if (col && !codesWithoutPrefix(splitCodes(ta.value)).length) col.classList.remove("has-bad");
          refresh();
        });
      });
      tas[0] && tas[0].focus();
      refresh();

      $("[data-add]", root).addEventListener("click", async () => {
        const cur = TYPES.map(([k]) => ({
          ptype: k,
          codes: splitCodes($(`[data-ptype="${k}"]`, root).value),
        }));
        const groups = cur.filter((g) => g.codes.length);
        if (!editing && !groups.length) return toast("请至少在一个类型下填写编号", "warn");

        // 编号必须带字母前缀（A539）；只写数字（539）就弹窗提醒，补全前不许保存
        const badItems = [];
        cur.forEach((g) => codesWithoutPrefix(g.codes)
          .forEach((c) => badItems.push({ ptype: g.ptype, code: c })));
        if (badItems.length) return prefixAlertDialog(badItems, root);
        $$(".type-col", root).forEach((c) => c.classList.remove("has-bad"));

        if (editing) {
          if (snapshot(cur) === beforeSig) { closeModal(); return toast("编号没有变化"); }
          const after = new Set();
          const dup = [];
          cur.forEach((g) => g.codes.forEach((c) => {
            if (after.has(c)) dup.push(c);
            after.add(c);
          }));
          const removed = items.filter((it) => !after.has(String(it.code).toUpperCase()));
          const msgs = [];
          if (removed.length) {
            msgs.push(`将移除 ${removed.length} 个编号：` +
              removed.slice(0, 10).map((x) => x.code).join("、") + (removed.length > 10 ? " …" : ""));
          }
          if (dup.length) {
            msgs.push(`${[...new Set(dup)].join("、")} 同时出现在多个类型下，会各记一条`);
          }
          if (msgs.length && !confirm(msgs.join("\n") + "\n\n确定保存？")) return;
        }

        const btn = $("[data-add]", root);
        btn.disabled = true;
        try {
          if (editing) {
            const r = await api(`/api/months/${M.mid}/entries`, {
              method: "PUT",
              body: { platform, groups: cur },
            });
            const dd = r.data || {};
            const parts = [];
            if (dd.added) parts.push(`新增 ${dd.added}`);
            if (dd.moved) parts.push(`改归类 ${dd.moved}`);
            if (dd.removed) parts.push(`移除 ${dd.removed}`);
            closeModal();
            toast(parts.length ? `已保存：${parts.join("、")}` : "编号没有变化");
          } else {
            const r = await api(`/api/months/${M.mid}/entries`, {
              method: "POST",
              body: { platform, groups },
            });
            const { added, skipped } = r.data;
            closeModal();
            toast(`已添加 ${added} 个编号` + (skipped && skipped.length ? `，${skipped.length} 个已存在` : ""));
          }
          loadMonth({ inPlace: true });   // 弹窗保存后不跳回顶部
        } catch (e) { btn.disabled = false; toast(e.message, "error"); }
      });
    },
  });
}

function addEntryDialog(platform) { entryCodesDialog(platform, "add"); }
function editCodesDialog(platform) { entryCodesDialog(platform, "edit"); }


/* 编辑一条已填报的编号（编号 / 类型 / 平台都能改），不用删了重加。
   权限与拖动一致：只列得出自己有编辑权限的平台（管理员 = 全部）。 */
function editEntryDialog(entryId) {
  const d = state.month.detail;
  if (!d) return;
  let it = null, platKey = "";
  d.platforms.forEach((p) => p.items.forEach((x) => {
    if (x.id === entryId) { it = x; platKey = p.key; }
  }));
  if (!it) return toast("这条记录已不存在，刷新后再试", "warn");
  const canPlats = d.platforms.filter((p) => p.can_edit);
  if (!canPlats.length) return toast("你没有该平台板块的编辑权限", "error");

  const TYPES = [["real", "真实"], ["urgent", "加急"], ["stock", "库存"], ["repair", "返修"]];
  const platOpts = canPlats.map((p) =>
    `<option value="${p.key}"${p.key === platKey ? " selected" : ""}>${esc(p.label)}</option>`).join("");

  openModal({
    title: "编辑填报编号",
    body: `
      <div class="notice info" style="margin-bottom:14px"><span>✏️</span>
        <div>编号填错了直接改就行，<b>不用删掉重加</b>；类型 / 平台也可以在这里换
          （页面上把编号拖到别的列效果一样）。改完项目名与报价会重新匹配。</div></div>
      <div class="field"><label>项目编号</label>
        <input class="input" id="eeCode" value="${esc(it.code)}" placeholder="如 A384" autocomplete="off"></div>
      <div class="grid2" style="margin-top:14px">
        <div class="field"><label>项目类型</label>
          <select class="select" id="eeType">
            ${TYPES.map(([k, l]) => `<option value="${k}"${k === it.ptype ? " selected" : ""}>${l}</option>`).join("")}
          </select></div>
        <div class="field"><label>所属平台</label>
          <select class="select" id="eePlat"${canPlats.length === 1 ? " disabled" : ""}>${platOpts}</select></div>
      </div>
      <div style="margin-top:10px;font-size:12px;color:var(--text3)">
        当前项目名：${esc(it.name) || '<span class="er-unmatched">库中未找到</span>'}
      </div>`,
    footer: `<span style="flex:1"></span>
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" data-save>保存</button>`,
    onMount(root) {
      const codeEl = $("#eeCode", root);
      codeEl.addEventListener("input", () => {   // 编号统一大写（后端也会兜底）
        const p = codeEl.selectionStart;
        codeEl.value = codeEl.value.toUpperCase();
        codeEl.setSelectionRange(p, p);
      });
      codeEl.focus();
      codeEl.select();

      $("[data-save]", root).addEventListener("click", async () => {
        const code = codeEl.value.trim();
        if (!code) return toast("请输入项目编号", "warn");
        const missing = codesWithoutPrefix([code]);
        if (missing.length) {          // 同样要求带字母前缀（如 A539）
          await alertDialog("请添加项目编号前缀", prefixBadHtml(
            missing.map((c) => ({ ptype: $("#eeType", root).value, code: c }))));
          codeEl.focus();
          codeEl.select();
          return;
        }
        const ptype = $("#eeType", root).value;
        const platform = $("#eePlat", root).value;
        const btn = $("[data-save]", root);
        btn.disabled = true;
        try {
          const r = await api(`/api/months/${state.month.mid}/entries/${entryId}`, {
            method: "PUT", body: { code, ptype, platform },
          });
          closeModal();
          const dd = r.data || {};
          if (dd.unchanged) return;
          toast(`已更新：${(dd.entry || {}).code} · ${dd.type_label || ""}`);
          loadMonth({ inPlace: true, anchorEid: entryId });
        } catch (e) { btn.disabled = false; toast(e.message, "error"); }
      });
    },
  });
}

/* 把编号文本切成去重后的大写数组（换行/逗号/空格/分号/竖线均可分隔） */
function splitCodes(text) {
  return String(text || "")
    .split(/[\s,，;；、|]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
}

/* 项目编号必须带字母前缀（A539 / B12 …）。只写成数字（539）就是不完整。
   规则只要求「以字母开头」，不写死某个字母 —— 以后新增 B/C 系列不用改代码。
   与后端 db.has_code_prefix() 同规则，服务端还有一道兜底。 */
function codesWithoutPrefix(codes) {
  const seen = new Set(), out = [];
  (codes || []).forEach((c) => {
    const s = String(c || "").trim();
    if (!s || /^[A-Za-z]/.test(s)) return;
    const k = s.toUpperCase();
    if (seen.has(k)) return;
    seen.add(k);
    out.push(k);
  });
  return out;
}

/* 弹窗提醒「请添加项目编号前缀」：问题编号逐个列出，对应类型框标红，
   关掉弹窗后光标自动落到第一个问题编号上（选中，直接重打就行）。 */
function prefixBadHtml(badItems) {
  const uniq = [...new Set(badItems.map((b) => b.code))];
  const detail = badItems
    .map((b) => `<span class="code-bad">${esc(b.code)}</span>
        <span style="font-size:12px;color:var(--text3)">（${esc(TYPE_LABEL[b.ptype] || b.ptype)}）</span>`)
    .join("");
  return `
    <div class="notice warn" style="margin-bottom:12px"><span>⚠️</span>
      <div>下面这 ${uniq.length} 个编号<b>缺少字母前缀</b>，不是完整编号，
        补全后才能保存（例如 <code>539</code> 要写成 <code>A539</code>）：</div></div>
    <div class="code-bad-list">${detail}</div>
    <div style="margin-top:12px;font-size:12px;color:var(--text3)">
      项目库里的编号都是「字母 + 数字」格式，如 A539、A1000。</div>`;
}

async function prefixAlertDialog(badItems, root) {
  $$(".type-col", root).forEach((c) => c.classList.remove("has-bad"));
  badItems.forEach((b) => {
    const ta = $(`.type-input[data-ptype="${b.ptype}"]`, root);
    if (ta) ta.closest(".type-col").classList.add("has-bad");
  });
  await alertDialog("请添加项目编号前缀", prefixBadHtml(badItems));
  const first = badItems[0];
  if (!first) return;
  const ta = $(`.type-input[data-ptype="${first.ptype}"]`, root);
  if (!ta) return;
  ta.focus();
  const at = ta.value.toUpperCase().indexOf(first.code);
  if (at >= 0) ta.setSelectionRange(at, at + first.code.length);
}

function newMonthDialog() {
  const now = new Date();
  const y = now.getFullYear(), mo = now.getMonth() + 1;
  openModal({
    title: "新建统计月份",
    body: `
      <div class="notice info" style="margin-bottom:14px"><span>📅</span>
        <div>创建后各平台实验员即可登录填写当月完成的项目编号。</div></div>
      <div class="field"><label>月份名称</label>
        <input class="input" id="mName" value="${y}年${mo}月" placeholder="如 2026年9月"></div>
      <div class="grid2" style="margin-top:14px">
        <div class="field"><label>年份</label>
          <input class="input" id="mYear" type="number" value="${y}"></div>
        <div class="field"><label>月份</label>
          <input class="input" id="mMonth" type="number" min="1" max="12" value="${mo}"></div>
      </div>`,
    footer: `<span style="flex:1"></span>
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" data-create>创建</button>`,
    onMount(root) {
      $("[data-create]", root).addEventListener("click", async () => {
        const name = $("#mName", root).value.trim();
        if (!name) return toast("请输入月份名称", "warn");
        try {
          const r = await api("/api/months", {
            method: "POST",
            body: { name, year: +$("#mYear", root).value || 0, month: +$("#mMonth", root).value || 0 },
          });
          closeModal();
          toast("已创建：" + r.data.month.name);
          state.month.mid = r.data.month.id;
          go("month");
        } catch (e) { toast(e.message, "error"); }
      });
    },
  });
}

/* 月份名称 / 年月改完后：左侧列表与右侧详情一起刷新（年月的改动会影响列表排序） */
async function refreshMonthAll() {
  try {
    const r = await api("/api/months");
    state.month.list = r.data.months || [];
  } catch (e) { /* 列表拉取失败也继续刷新详情 */ }
  renderMonthList();
  await loadMonth({ inPlace: true });
}

/* 编辑统计月份：建错月份（9 月建成 10 月）时直接改回来，已填报的编号全部保留 */
function editMonthDialog(month) {
  const now = new Date();
  const y = month.year || now.getFullYear();
  const mo = month.month || (now.getMonth() + 1);
  openModal({
    title: "编辑统计月份",
    body: `
      <div class="notice info" style="margin-bottom:14px"><span>✏️</span>
        <div>改的是这个月份的<b>名称与年月</b>；各平台<b>已填写的编号一条都不会丢</b>，
          不用删掉整个统计重建。</div></div>
      <div class="field"><label>月份名称</label>
        <input class="input" id="emName" value="${esc(month.name)}" placeholder="如 2026年9月"></div>
      <div class="grid2" style="margin-top:14px">
        <div class="field"><label>年份</label>
          <input class="input" id="emYear" type="number" value="${y}"></div>
        <div class="field"><label>月份</label>
          <input class="input" id="emMonth" type="number" min="1" max="12" value="${mo}"></div>
      </div>`,
    footer: `<span style="flex:1"></span>
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" data-save>保存</button>`,
    onMount(root) {
      const nameEl = $("#emName", root), yEl = $("#emYear", root), mEl = $("#emMonth", root);
      // 名称是标准写法「YYYY年M月」时，改年月就跟着同步（自定义名称不动）
      const AUTO = /^\s*\d{4}\s*年\s*\d{1,2}\s*月\s*$/;
      const syncName = () => {
        if (!AUTO.test(nameEl.value)) return;
        const yy = yEl.value.trim(), mm = mEl.value.trim();
        if (yy && mm) nameEl.value = `${yy}年${Number(mm)}月`;
      };
      yEl.addEventListener("input", syncName);
      mEl.addEventListener("input", syncName);
      nameEl.focus();

      $("[data-save]", root).addEventListener("click", async () => {
        const name = nameEl.value.trim();
        if (!name) return toast("请输入月份名称", "warn");
        const yy = Number(yEl.value) || 0;
        const mm = Number(mEl.value) || 0;
        if (yy && (yy < 1900 || yy > 2200)) return toast("年份需在 1900-2200 之间", "warn");
        if (mm && (mm < 1 || mm > 12)) return toast("月份需在 1-12 之间", "warn");
        const btn = $("[data-save]", root);
        btn.disabled = true;
        try {
          const r = await api("/api/months/" + month.id, {
            method: "PUT", body: { name, year: yy, month: mm },
          });
          closeModal();
          toast("已更新为：" + r.data.month.name);
          await refreshMonthAll();
        } catch (e) { btn.disabled = false; toast(e.message, "error"); }
      });
    },
  });
}

async function exportMonth(month) {
  const target = await askExportTarget("月末统计表");
  if (!target) return;
  const btn = $("#btnExportMonth");
  if (btn) btn.disabled = true;
  try {
    const r = await api(`/api/months/${month.id}/export`, { method: "POST", body: {} });
    const d = r.data;
    if (target === "local") triggerDownload(d.path, d.name);
    toast(target === "local" ? `已导出 ${d.total} 项，正在下载到本机` : `已导出 ${d.total} 项到服务器`);
    showExportDone(d, target);
    loadMonth({ inPlace: true });
  } catch (e) { toast(e.message, "error"); }
  finally { if (btn) btn.disabled = false; }
}

/* ==================================================================== 用户管理 */
/* ------------------------------------------------ 全服通告（管理员） */
/* 管理员发布公告（例如「月末了，请及时填写月末统计」），可多选接收平台：
   命中的普通账户在消息中心收到，并（默认）登录后弹「流光横幅」。
   平台留空 = 全体成员（含运营 / 设计）。已发布的通告可撤回。 */
VIEWS.notice = async (root) => {
  if (!isAdmin()) { root.innerHTML = `<div class="empty"><p>需要管理员权限</p></div>`; return; }
  await loadNoticePage(root);
};

function bcChipList(data) {
  const plats = data.platforms || [];
  const chips = [{ key: "", label: "全体成员", count: data.total || 0 }];
  plats.forEach((p) => chips.push({
    key: p.key,
    label: p.label + (p.readonly ? "（只读）" : ""),
    count: p.count || 0,
  }));
  return chips;
}

function bcAudienceHtml(chips, selected) {
  const pick = selected.size
    ? chips.filter((c) => selected.has(c.key))
    : chips.filter((c) => !c.key);
  const n = pick.reduce((s, c) => s + (c.count || 0), 0);
  const who = pick.length ? pick.map((c) => c.label).join("、") : "全体成员";
  return `将发送给 <b>${n}</b> 名成员 · ${esc(who)}`
    + (n === 0 ? ' <span style="color:var(--red)">（该平台当前没有在用账号）</span>' : "");
}

function bcItemHtml(it) {
  const aud = it.audience || 0;
  const rd = it.read_count || 0;
  const full = aud > 0 && rd >= aud;
  return `<div class="bc-item" data-bid="${it.id}">
    <div class="bc-item-main">
      <div class="bc-item-title">${esc(it.title)}</div>
      ${it.body ? `<div class="bc-item-body">${esc(it.body)}</div>` : ""}
      <div class="bc-item-meta">
        <span class="bc-to">接收：${esc(it.target_platform_label || "全体成员")}</span>
        <span class="bc-rate${full ? " ok" : ""}">已读 ${rd}/${aud}</span>
        ${it.popup ? '<span class="bc-rate">弹窗提醒</span>' : '<span class="bc-rate">仅消息中心</span>'}
        <span>${esc(notifTime(it.created_at))}</span>
      </div>
    </div>
    <div style="flex-shrink:0">
      <button class="btn btn-danger btn-sm" data-bc-del="${it.id}">撤回</button>
    </div>
  </div>`;
}

async function loadNoticePage(root) {
  let data;
  try { data = (await api("/api/broadcasts")).data; }
  catch (e) { root.innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`; return; }
  const items = data.items || [];
  const chips = bcChipList(data);
  const selected = new Set();                     // 空集合 = 全体成员

  root.innerHTML = `
    <div class="bc-grid">
      <div class="card" style="margin-bottom:0">
        <div class="card-head">
          <h3>📢 发布全服通告</h3>
          <span class="bc-hint">发布后勾选平台的成员会立刻收到</span>
        </div>
        <div class="card-body">
          <div class="field">
            <label>标题</label>
            <input class="input" id="bcTitle" maxlength="60"
              placeholder="例如：请及时填写 10 月月末统计">
          </div>
          <div class="field" style="margin-top:14px">
            <label>正文（可选）</label>
            <textarea class="textarea" id="bcBody" maxlength="600"
              placeholder="补充说明，例如：截止到本月 28 日，请在「月末项目统计」里填完本月完成的项目编号。"></textarea>
          </div>
          <div class="field" style="margin-top:16px">
            <label>接收平台</label>
            <div class="bc-chips" id="bcChips">
              ${chips.map((c) => `
                <button type="button" class="bc-chip${c.key ? "" : " all on"}"
                        data-bc-plat="${esc(c.key)}">${esc(c.label)}<b>${c.count}</b></button>`).join("")}
            </div>
            <div class="bc-aud" id="bcAud">${bcAudienceHtml(chips, selected)}</div>
          </div>
          <label class="bc-check">
            <input type="checkbox" id="bcPopup" checked>
            <span><b>登录后弹窗提醒</b>：接收者登录（或正开着页面）时会弹出提醒横幅，
              直到他点开看过为止。不勾选则只进消息中心。</span>
          </label>
          <div style="display:flex;gap:10px;margin-top:16px;flex-wrap:wrap">
            <button class="btn btn-primary" id="bcSend">发布通告</button>
            <button class="btn btn-ghost" id="bcTpl">填入「月末统计提醒」模板</button>
          </div>
          <div class="bc-tip">
            发布平台留空 = 全体成员（含运营 / 设计等只读岗位）；
            通告发出后管理员自己的消息中心不会出现这条（它是发给成员干活的）。
          </div>
        </div>
      </div>
      <div class="card" style="margin-bottom:0">
        <div class="card-head"><h3>已发布通告（${items.length}）</h3>
          <span class="bc-hint">按发布时间倒序</span>
        </div>
        <div class="card-body tight" id="bcList">
          ${items.length ? items.map(bcItemHtml).join("")
            : '<div class="notif-empty">还没有发布过通告</div>'}
        </div>
      </div>
    </div>`;

  // 平台 chip：多选；点「全体成员」会清掉其它选择（反之亦然）
  // ⚠️ selected 为空 = 全体成员，所以「全体」chip 的高亮条件是「没有任何平台被选」，
  //    不能只看 selected.has("")——那样点完全体反而一个都不亮。
  const chipEls = $$("[data-bc-plat]", root);
  const syncChips = () => {
    chipEls.forEach((el) => {
      const key = el.dataset.bcPlat;
      el.classList.toggle("on", key ? selected.has(key) : selected.size === 0);
    });
    $("#bcAud").innerHTML = bcAudienceHtml(chips, selected);
  };
  chipEls.forEach((el) => el.addEventListener("click", () => {
    const key = el.dataset.bcPlat;
    if (!key) { selected.clear(); syncChips(); return; }
    if (selected.has(key)) selected.delete(key); else selected.add(key);
    syncChips();
  }));

  $("#bcTpl").addEventListener("click", () => {
    const t = $("#bcTitle"), b = $("#bcBody");
    if (!t.value.trim()) t.value = "请及时填写本月月末统计";
    if (!b.value.trim()) {
      b.value = "月末已到，请各位及时到「月末项目统计」页填写本月完成（勾 ✅）的项目编号，"
        + "填完记得核对一遍，谢谢配合。";
    }
    t.focus();
  });

  $("#bcSend").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const title = $("#bcTitle").value.trim();
    if (!title) { toast("请填写通告标题", "error"); $("#bcTitle").focus(); return; }
    const payload = {
      title,
      body: $("#bcBody").value.trim(),
      platforms: Array.from(selected),
      popup: !!$("#bcPopup").checked,
    };
    btn.disabled = true;
    let res;
    try { res = (await api("/api/broadcasts", { method: "POST", body: payload })).data; }
    catch (e) { btn.disabled = false; return toast(e.message, "error"); }
    toast(`已发布，${res.audience || 0} 名成员会收到提醒`);
    await loadNoticePage(root);
  });

  $$("[data-bc-del]", root).forEach((b) => b.addEventListener("click", async () => {
    const bid = Number(b.dataset.bcDel);
    if (!confirm("确定撤回这条通告？\n\n撤回后所有接收者的消息中心里这条会消失，"
      + "已读记录一并清除（此操作不可恢复）。")) return;
    b.disabled = true;
    try { await api("/api/broadcasts/" + bid, { method: "DELETE" }); }
    catch (e) { b.disabled = false; return toast(e.message, "error"); }
    toast("通告已撤回");
    await loadNoticePage(root);
  }));
}

VIEWS.users = async (root) => {
  if (!isAdmin()) { root.innerHTML = `<div class="empty"><p>需要管理员权限</p></div>`; return; }
  let users;
  try { users = (await api("/api/users")).data; }
  catch (e) { root.innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`; return; }

  const meId = state.me ? state.me.id : 0;
  const adminCount = users.filter((u) => u.role === "admin" && u.active).length;

  root.innerHTML = `
    <div class="notice info">
      <span>👥</span>
      <div><b>管理员</b>可管理项目库、导入数据、导出文件，并管理账号；
        <b>普通用户</b>只能填写自己所属平台的板块（运营/设计为只读）。
        「<b>开启审核</b>」给某个普通账号加上<b>审核功能</b>：项目结项后他会收到弹窗提醒，
        复核完点「审核完成」，管理员会收到「某某某已审核完成，请复审」。
        ⚠️ 通知里显示的是账号的<b>「姓名」</b>，不是登录用户名；没填姓名的账号会被写成「账号 xxx」，
        建议都补上。自助注册只能创建普通用户，管理员账号请在此处新增。</div>
    </div>
    <div class="card">
      <div class="card-head"><h3>账号列表（${users.length}）</h3>
        <button class="btn btn-primary btn-sm" id="btnNewUser">新增账号</button>
      </div>
      <div class="table-wrap">
        <table class="tbl">
          <thead><tr>
            <th style="width:150px">用户名</th><th style="width:130px">姓名</th>
            <th style="width:110px">角色</th><th style="width:120px">所属平台</th>
            <th style="width:130px">审核功能</th>
            <th style="width:90px">状态</th><th style="width:150px">上次登录</th>
            <th style="width:230px">操作</th>
          </tr></thead>
          <tbody>
            ${users.map((u) => {
              const isBuiltin = u.username === "admin";
              const lastActiveAdmin = u.role === "admin" && u.active && adminCount <= 1;
              const noName = noNameOf(u);
              return `<tr data-uid="${u.id}">
              <td><span class="code-cell">${esc(u.username)}</span>${isBuiltin ? ' <span class="pill" style="font-size:10px">内置</span>' : ""}</td>
              <td>${noName ? `<span style="color:var(--text3)">${esc(u.username)}</span>
                  <span class="pill warn" style="font-size:10px" title="该账号没单独填姓名，通知里会显示成「账号 ${esc(u.username)}」；点「编辑」补上姓名即可">未填姓名</span>`
                : esc(u.display_name)}</td>
              <td>${u.role === "admin"
                ? '<span class="pill admin">管理员</span>'
                : '<span class="pill">普通用户</span>'}</td>
              <td>${esc(u.platform_name || "—")}</td>
              <td>${u.role === "admin"
                ? '<span style="color:var(--text3);font-size:12px">—</span>'
                : `<button class="btn btn-sm ${u.can_audit ? "btn-green" : "btn-ghost"}"
                     data-audit-user="${u.id}" data-on="${u.can_audit ? 1 : 0}"
                     title="${u.can_audit ? "该账号可复核结项项目；点击取消" : "点击开启：项目结项后会收到「请审核」弹窗提醒"}">
                     ${u.can_audit ? "审核员 ✓" : "开启审核"}</button>`}</td>
              <td>${u.active ? '<span class="st done">正常</span>' : '<span class="st undone">停用</span>'}</td>
              <td>${esc(u.last_login || "—")}</td>
              <td>
                <button class="btn btn-ghost btn-sm" data-edit="${u.id}">编辑</button>
                <button class="btn btn-ghost btn-sm" data-reset="${u.id}">改密码</button>
                ${lastActiveAdmin ? "" : `
                  <button class="btn btn-ghost btn-sm" data-toggle="${u.id}" data-active="${u.active ? 1 : 0}">${u.active ? "停用" : "启用"}</button>`}
                ${u.role !== "admin" ? `
                  <button class="btn btn-danger btn-sm" data-deluser="${u.id}">删除</button>` : ""}
              </td>
            </tr>`; }).join("")}
          </tbody>
        </table>
      </div>
    </div>`;

  $("#btnNewUser").addEventListener("click", () => newUserDialog());
  $$("[data-audit-user]").forEach((b) => b.addEventListener("click", async () => {
    const uid = b.dataset.auditUser;
    const on = b.dataset.on === "1";
    const u = users.find((x) => String(x.id) === String(uid)) || {};
    if (!on && !confirm(`给「${personLabel(u)}」开启审核功能？\n\n`
      + `开启后，项目结项时该账号会收到「请审核」弹窗提醒，\n`
      + `复核完点「审核完成」，管理员会收到「某某某已审核完成，请复审」。`)) return;
    try {
      await api("/api/users/" + uid, { method: "PUT", body: { can_audit: !on } });
      toast(on ? "已取消审核功能" : "已开启审核功能");
      go("users");
    } catch (e) { toast(e.message, "error"); }
  }));
  $$("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const u = users.find((x) => x.id === Number(b.dataset.edit));
    editUserDialog(u, u.id === meId);
  }));
  $$("[data-reset]").forEach((b) => b.addEventListener("click", () => {
    const u = users.find((x) => x.id === Number(b.dataset.reset));
    resetPwdDialog(u);
  }));
  $$("[data-toggle]").forEach((b) => b.addEventListener("click", async () => {
    try {
      await api("/api/users/" + b.dataset.toggle, {
        method: "PUT", body: { active: b.dataset.active !== "1" },
      });
      toast("已更新"); go("users");
    } catch (e) { toast(e.message, "error"); }
  }));
  $$("[data-deluser]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("确定删除该用户？")) return;
    try { await api("/api/users/" + b.dataset.deluser, { method: "DELETE" }); toast("已删除"); go("users"); }
    catch (e) { toast(e.message, "error"); }
  }));
};

function rolePlatformFields(role, platform, canAudit) {
  const ro = (k) => ["ops", "design"].includes(k);
  return `
    <div class="field" style="margin-top:12px"><label>角色</label>
      <select class="select" id="u_role">
        <option value="user"${role !== "admin" ? " selected" : ""}>普通用户</option>
        <option value="admin"${role === "admin" ? " selected" : ""}>管理员</option>
      </select></div>
    <div class="field" style="margin-top:12px" id="platField"${role === "admin" ? ' style="display:none"' : ""}>
      <label>所属岗位</label>
      <select class="select" id="u_plat">
        ${Object.entries(PLATFORM_LABEL).map(([k, v]) =>
          `<option value="${k}"${k === platform ? " selected" : ""}>${v}${ro(k) ? "（只读）" : ""}</option>`).join("")}
      </select></div>
    <div class="field" id="auditField" style="margin-top:12px${role === "admin" ? ";display:none" : ""}">
      <label style="display:flex;align-items:flex-start;gap:9px;cursor:pointer">
        <input type="checkbox" id="u_audit"${canAudit ? " checked" : ""} style="width:auto;margin-top:3px">
        <span style="font-size:12.5px;color:var(--text2);line-height:1.55">
          <b>开启审核功能</b>：项目结项后该账号会收到「请审核」弹窗提醒，
          复核完点「审核完成」，管理员会收到「某某某已审核完成，请复审」。<br>
          <span style="color:var(--text3)">（只对普通用户有效，管理员不需要）</span>
        </span>
      </label></div>`;
}

function bindRoleToggle(root) {
  const roleSel = $("#u_role", root);
  const platField = $("#platField", root);
  const auditField = $("#auditField", root);
  if (!roleSel || !platField) return;
  const sync = () => {
    const isAdmin = roleSel.value === "admin";
    platField.style.display = isAdmin ? "none" : "";
    if (auditField) auditField.style.display = isAdmin ? "none" : "";
  };
  roleSel.addEventListener("change", sync); sync();
}

function newUserDialog() {
  openModal({
    title: "新增账号",
    body: `
      <div class="notice info" style="margin-bottom:14px"><span>ℹ️</span>
        <div>可创建<b>普通用户</b>（需选岗位）或<b>管理员</b>（拥有全部权限）。</div></div>
      <div class="field"><label>用户名</label>
        <input class="input" id="u_name" placeholder="至少 3 个字符"></div>
      <div class="field" style="margin-top:12px"><label>姓名 <span style="color:var(--text3);font-weight:400">（通知里显示这个，建议必填）</span></label>
        <input class="input" id="u_display" placeholder="如：张小凡；留空则通知里显示「账号 + 登录名」"></div>
      <div class="field" style="margin-top:12px"><label>初始密码</label>
        <input class="input" id="u_pwd" placeholder="至少 6 位"></div>
      ${rolePlatformFields("user", "cell", false)}`,
    footer: `<span style="flex:1"></span>
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" data-ok>创建</button>`,
    onMount(root) {
      bindRoleToggle(root);
      $("[data-ok]", root).addEventListener("click", async () => {
        try {
          const auditBox = $("#u_audit", root);
          await api("/api/users", {
            method: "POST",
            body: {
              username: $("#u_name", root).value.trim(),
              display_name: $("#u_display", root).value.trim(),
              password: $("#u_pwd", root).value,
              role: $("#u_role", root).value,
              platform: $("#u_plat", root) ? $("#u_plat", root).value : "",
              can_audit: !!(auditBox && auditBox.checked),
            },
          });
          closeModal(); toast("已创建账号"); go("users");
        } catch (e) { toast(e.message, "error"); }
      });
    },
  });
}

function editUserDialog(u, isSelf) {
  openModal({
    title: `编辑「${u.display_name}」`,
    body: `
      ${isSelf ? `<div class="notice warn" style="margin-bottom:14px"><span>⚠️</span>
        <div>这是你当前登录的账号，请勿把自己降级为普通用户或停用，以免失去管理员权限。</div></div>` : ""}
      <div class="field"><label>用户名</label>
        <input class="input" value="${esc(u.username)}" disabled></div>
      <div class="field" style="margin-top:12px"><label>姓名 <span style="color:var(--text3);font-weight:400">（通知 / 审核记录里显示这个）</span></label>
        <input class="input" id="e_display" value="${noNameOf(u) ? "" : esc(u.display_name)}"
          placeholder="如：张小凡；留空则通知里显示「账号 + 登录名」"></div>
      ${rolePlatformFields(u.role, u.platform, !!u.can_audit)}`,
    footer: `<span style="flex:1"></span>
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" data-ok>保存</button>`,
    onMount(root) {
      bindRoleToggle(root);
      $("[data-ok]", root).addEventListener("click", async () => {
        try {
          const auditBox = $("#u_audit", root);
          await api("/api/users/" + u.id, {
            method: "PUT",
            body: {
              display_name: $("#e_display", root).value.trim(),
              role: $("#u_role", root).value,
              platform: $("#u_plat", root) ? $("#u_plat", root).value : "",
              can_audit: !!(auditBox && auditBox.checked),
            },
          });
          closeModal(); toast("已保存"); go("users");
        } catch (e) { toast(e.message, "error"); }
      });
    },
  });
}

function resetPwdDialog(u) {
  openModal({
    title: `重置「${u.display_name}」的密码`,
    body: `<div class="field"><label>新密码（至少 6 位）</label>
      <input class="input" id="r_pwd" placeholder="至少 6 位"></div>`,
    footer: `<span style="flex:1"></span>
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" data-ok>确定</button>`,
    onMount(root) {
      $("[data-ok]", root).addEventListener("click", async () => {
        try {
          await api("/api/users/" + u.id, { method: "PUT", body: { password: $("#r_pwd", root).value } });
          closeModal(); toast("密码已重置");
        } catch (e) { toast(e.message, "error"); }
      });
    },
  });
}

/* ==================================================================== 设置 */
VIEWS.settings = async (root) => {
  const r = await api("/api/settings");
  const s = r.data;
  root.innerHTML = `
    <div class="settings-grid">
      <div class="card" style="margin:0">
        <div class="card-head"><h3>导出设置</h3></div>
        <div class="card-body">
          <div class="field"><label>Excel 导出目录（留空＝数据目录下的「导出」文件夹）</label>
            <input class="input" id="s_dir" value="${esc(s.export_dir || "")}" placeholder="/data/导出"></div>
          <div class="hint">导出的汇总 Excel 会保存在服务器上的这个目录里（形如 <code>/data/导出</code>）。留空则用默认位置。</div>
          <div style="margin-top:16px"><button class="btn btn-primary" id="btnSave">保存设置</button></div>
        </div>
      </div>
      <div class="card" style="margin:0">
        <div class="card-head"><h3>数据维护</h3></div>
        <div class="card-body">
          <div id="sysInfo" class="loading">加载中…</div>
          <div class="divider"></div>
          <div style="display:flex;gap:10px;flex-wrap:wrap">
            <button class="btn btn-ghost" id="btnOpenData">查看数据目录</button>
            <button class="btn btn-ghost" id="btnBackup">备份数据库</button>
            <button class="btn btn-danger" id="btnClearLib">清空项目库</button>
          </div>
          <div class="hint" style="margin-top:10px">数据全部保存在服务器容器内的数据卷中，重启不丢失。</div>
          <div class="hint" style="margin-top:6px">「清空项目库」会删除当前全部项目（<b>不影响</b>用户账号、系统设置与月末统计填报）；程序不内置任何项目数据，请用「导入项目库」自己导入。</div>
        </div>
      </div>
    </div>`;

  $("#btnSave").addEventListener("click", async () => {
    try {
      await api("/api/settings", { method: "PUT", body: { export_dir: $("#s_dir").value.trim() } });
      toast("设置已保存");
    } catch (e) { toast(e.message, "error"); }
  });
  $("#btnOpenData").addEventListener("click", async () => {
    try {
      const r = await api("/api/system/data-dir");
      if (r.data && r.data.no_gui) toast("数据目录：服务器数据卷 /data（详见「设置」页）", "info", 5000);
      else toast("已在服务器上打开数据目录");
    } catch (e) { toast(e.message, "error"); }
  });
  $("#btnBackup").addEventListener("click", async () => {
    try { await api("/api/system/backup", { method: "POST", body: {} });
      toast("数据库已备份到服务器数据目录", "info", 4000); }
    catch (e) { toast(e.message, "error"); }
  });
  $("#btnClearLib").addEventListener("click", async () => {
    if (!confirm("「清空项目库」会删除当前项目库里的全部项目，且不可恢复。\n\n用户账号、系统设置、月末项目统计填报不受影响。\n建议先「备份数据库」。确定继续？")) return;
    try {
      const r = await api("/api/projects/clear", { method: "POST", body: { ptype: "" } });
      const d = r.data || {};
      toast(`已清空项目库：删除 ${d.deleted || 0} 条`, "info", 4000);
      state.dash = null;
      go("settings");
    } catch (e) { toast(e.message, "error"); }
  });

  try {
    const info = await api("/api/system/info");
    const d = info.data;
    $("#sysInfo").innerHTML = `
      <div class="kv"><span class="k">数据目录</span><span style="font-size:12px;font-family:Consolas,monospace">${esc(d.data_dir)}</span></div>
      <div class="kv"><span class="k">数据库大小</span><b>${(d.db_size / 1024).toFixed(1)} KB</b></div>
      <div class="kv"><span class="k">项目记录数</span><b>${d.total}</b></div>
      <div class="kv"><span class="k">上次导入</span><span>${esc(d.last_import || "—")}</span></div>`;
  } catch (e) {
    $("#sysInfo").innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`;
  }
};

/* ================================================== 工作日志（个人私有） */
const WL = { items: [], month: "", keyword: "" };

function wlToday() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function wlMonthLabel(m) {
  const [y, mo] = String(m).split("-");
  return `${y}年${Number(mo)}月`;
}

function weekdayCn(dstr) {
  const d = new Date(dstr + "T00:00:00");
  if (isNaN(d)) return "";
  return "周" + "日一二三四五六"[d.getDay()];
}

VIEWS.worklog = async (root) => {
  root.innerHTML = `
    <div class="wl-layout">
      <div class="card wl-form-card">
        <div class="card-head"><h3>写日志</h3>
          <span class="wl-privacy">仅自己可见</span></div>
        <div class="card-body">
          <div class="field">
            <label>日期</label>
            <input class="input" type="date" id="wlDate" value="${wlToday()}">
          </div>
          <div class="field" style="margin-top:12px">
            <label>标题</label>
            <input class="input" id="wlTitle" placeholder="一句话概括（可不填）" maxlength="80">
          </div>
          <div class="field" style="margin-top:12px">
            <label>内容</label>
            <textarea class="textarea" id="wlContent" rows="8"
              placeholder="今天做了什么、进展、遇到的问题…"></textarea>
          </div>
          <div class="wl-form-foot">
            <button class="btn btn-ghost" id="wlClear">清空</button>
            <button class="btn btn-primary" id="wlSave">保存日志</button>
          </div>
        </div>
      </div>

      <div class="card wl-list-card">
        <div class="card-head">
          <h3>我的日志 <span class="wl-count" id="wlCount"></span></h3>
          <div class="wl-list-actions">
            <select class="select" id="wlMonthSel" style="width:140px"></select>
            <input class="input" id="wlSearch" placeholder="搜索标题 / 内容" style="width:170px">
            <button class="btn btn-primary btn-sm" id="wlStatBtn">统计当月</button>
          </div>
        </div>
        <div class="card-body" id="wlListBody"><div class="loading">加载中…</div></div>
      </div>
    </div>`;

  $("#wlSave").addEventListener("click", saveWorklog);
  $("#wlClear").addEventListener("click", () => {
    $("#wlDate").value = wlToday();
    $("#wlTitle").value = "";
    $("#wlContent").value = "";
  });
  $("#wlMonthSel").addEventListener("change", (e) => {
    WL.month = e.target.value;
    loadWorklogs();
  });
  $("#wlStatBtn").addEventListener("click", () => worklogStatDialog(WL.month));
  let timer = 0;
  $("#wlSearch").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => { WL.keyword = e.target.value.trim(); loadWorklogs(); }, 280);
  });

  await loadWorklogs();
};

async function loadWorklogs() {
  const box = $("#wlListBody");
  if (!box) return;
  box.innerHTML = '<div class="loading">加载中…</div>';
  let d;
  try {
    const qs = new URLSearchParams();
    if (WL.month) qs.set("month", WL.month);
    if (WL.keyword) qs.set("q", WL.keyword);
    const r = await api("/api/worklogs?" + qs.toString());
    d = r.data;
  } catch (e) {
    box.innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`;
    return;
  }
  WL.items = d.items || [];
  const months = d.months || [];

  const sel = $("#wlMonthSel");
  if (sel) {
    const cur = WL.month;
    const opts = ['<option value="">全部月份</option>'].concat(
      months.map((m) => `<option value="${m.month}"${m.month === cur ? " selected" : ""}>${wlMonthLabel(m.month)}（${m.count}）</option>`));
    sel.innerHTML = opts.join("");
  }
  const cnt = $("#wlCount");
  if (cnt) cnt.textContent = WL.items.length ? `· 共 ${WL.items.length} 条` : "";

  if (!WL.items.length) {
    box.innerHTML = `<div class="empty"><p>${WL.keyword || WL.month ? "没有符合条件的日志" : "还没有日志，左边写下第一篇吧"}</p></div>`;
    return;
  }

  const groups = {};
  WL.items.forEach((it) => { (groups[it.date] = groups[it.date] || []).push(it); });
  box.innerHTML = Object.keys(groups).sort().reverse().map((day) => `
    <div class="wl-day">
      <div class="wl-day-head">
        <span class="wl-day-date">${esc(day)}</span>
        <span class="wl-day-week">${esc(weekdayCn(day))}</span>
        <span class="wl-day-n">${groups[day].length} 条</span>
      </div>
      ${groups[day].map((it) => `
        <div class="wl-item" data-id="${it.id}">
          <div class="wl-item-head">
            <span class="wl-item-title">${esc(it.title) || '<span class="wl-untitled">（无标题）</span>'}</span>
            <span class="wl-item-time">${esc((it.updated_at || "").slice(11, 16))}</span>
            <button class="wl-act" data-edit="${it.id}" title="编辑">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
            </button>
            <button class="wl-act wl-act-del" data-del="${it.id}" title="删除">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>
            </button>
          </div>
          ${it.content ? `<div class="wl-item-body">${esc(it.content).replace(/\n/g, "<br>")}</div>` : ""}
        </div>`).join("")}
    </div>`).join("");

  $$("[data-edit]", box).forEach((b) => b.addEventListener("click", () => {
    const it = WL.items.find((x) => x.id === Number(b.dataset.edit));
    if (it) editWorklogDialog(it);
  }));
  $$("[data-del]", box).forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("确定删除这条日志？")) return;
    try {
      await api(`/api/worklogs/${b.dataset.del}`, { method: "DELETE" });
      toast("已删除");
      loadWorklogs();
    } catch (e) { toast(e.message, "error"); }
  }));
}

async function saveWorklog() {
  const date = $("#wlDate").value || wlToday();
  const title = $("#wlTitle").value.trim();
  const content = $("#wlContent").value.trim();
  if (!title && !content) return toast("请填写标题或内容", "warn");
  const btn = $("#wlSave");
  btn.disabled = true;
  try {
    await api("/api/worklogs", { method: "POST", body: { date, title, content } });
    toast("已保存");
    $("#wlDate").value = wlToday();
    $("#wlTitle").value = "";
    $("#wlContent").value = "";
    loadWorklogs();
  } catch (e) { toast(e.message, "error"); }
  finally { btn.disabled = false; }
}

function editWorklogDialog(it) {
  openModal({
    title: "编辑日志",
    wide: true,
    body: `
      <div class="field"><label>日期</label>
        <input class="input" type="date" id="weDate" value="${esc(it.date)}"></div>
      <div class="field" style="margin-top:12px"><label>标题</label>
        <input class="input" id="weTitle" value="${esc(it.title)}"></div>
      <div class="field" style="margin-top:12px"><label>内容</label>
        <textarea class="textarea" id="weContent" rows="8">${esc(it.content)}</textarea></div>`,
    footer: `<span style="flex:1"></span>
      <button class="btn btn-ghost" data-close>取消</button>
      <button class="btn btn-primary" data-save>保存修改</button>`,
    onMount(root) {
      $("[data-save]", root).addEventListener("click", async () => {
        const body = {
          date: $("#weDate", root).value,
          title: $("#weTitle", root).value.trim(),
          content: $("#weContent", root).value.trim(),
        };
        if (!body.title && !body.content) return toast("请填写标题或内容", "warn");
        try {
          await api(`/api/worklogs/${it.id}`, { method: "PUT", body });
          closeModal();
          toast("已更新");
          loadWorklogs();
        } catch (e) { toast(e.message, "error"); }
      });
    },
  });
}

/* 统计某月日志：总条数 + 有记录天数 + 按日分布条形图 */
async function worklogStatDialog(month) {
  const m = month || wlToday().slice(0, 7);
  let d;
  try {
    const r = await api(`/api/worklogs/summary?year=${m.slice(0, 4)}&month=${Number(m.slice(5, 7))}`);
    d = r.data;
  } catch (e) { return toast(e.message, "error"); }

  const max = Math.max(1, ...(d.days || []).map((x) => x.count));
  const bars = (d.days || []).length
    ? d.days.map((x) => `
        <div class="wl-bar-row">
          <span class="wl-bar-day">${esc(x.date.slice(5))}</span>
          <span class="wl-bar-track"><span class="wl-bar-fill" style="width:${(x.count / max) * 100}%"></span></span>
          <span class="wl-bar-n">${x.count} 条</span>
        </div>`).join("")
    : `<div class="empty" style="padding:20px"><p>${esc(d.label)}还没有日志</p></div>`;

  openModal({
    title: `${d.label} 日志统计`,
    wide: true,
    body: `
      <div class="wl-stat-cards">
        <div class="wl-stat-card"><b>${d.total}</b><span>当月日志条数</span></div>
        <div class="wl-stat-card"><b>${d.active_days}</b><span>有记录天数</span></div>
        <div class="wl-stat-card"><b>${d.total ? Math.round(d.active_days / 30 * 100) : 0}%</b><span>月度覆盖度</span></div>
      </div>
      <div class="divider"></div>
      <div class="wl-bars">${bars}</div>`,
    footer: `<span style="flex:1"></span><button class="btn btn-ghost" data-close>关闭</button>`,
  });
}
