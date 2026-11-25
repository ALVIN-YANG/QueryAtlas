"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const opt = (xs) => xs.map((x) => `<option>${esc(x)}</option>`).join("");
const stamp = (x) =>
  x
    ? new Date(typeof x === "number" ? x * 1000 : x).toLocaleString("zh-CN")
    : "—";
const card = (title, html) =>
  `<section class="card"><h2>${title}</h2>${html}</section>`;
const table = (headers, rows) =>
  rows.length
    ? `<table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((x) => `<td>${x}</td>`).join("")}</tr>`).join("")}</tbody></table>`
    : '<p class="muted">暂无记录</p>';
let me,
  assets,
  admin,
  tab = "assets",
  viewId = 0,
  timer;
const labels = {
  assets: "知识资产",
  jobs: "导入记录",
  monitor: "监控告警",
  admin: "权限管理",
};
const actions = {
  search: "检索片段",
  original: "下载原件",
  ingest: "提交导入",
  jobs: "查看任务",
  monitor: "查看监控",
  ack: "认领告警",
};
const can = (a) => assets?.actions.includes(a);
const base = () => "/api/spaces/" + encodeURIComponent($("#space").value);
function msg(s) {
  clearTimeout(timer);
  $("#message").textContent = s;
  $("#message").hidden = false;
  timer = setTimeout(() => ($("#message").hidden = true), 5000);
}
async function api(url, method = "GET", body) {
  const headers = { "X-QueryAtlas": "1" };
  let data = body;
  if (body && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    data = JSON.stringify(body);
  }
  const r = await fetch(url, { method, headers, body: data });
  const j = await r.json();
  if (!r.ok) {
    if (r.status === 401) loginView();
    throw Error(typeof j.detail === "string" ? j.detail : "请求参数无效");
  }
  return j;
}
function loginView() {
  me = null;
  $("#shell").hidden = true;
  $("#logout").hidden = true;
  $("#login").hidden = false;
  $("#identity").textContent = "";
}
async function start() {
  me = await api("/api/me");
  $("#identity").textContent = me.name;
  $("#space").innerHTML = opt(me.spaces);
  $("#login").hidden = true;
  $("#shell").hidden = false;
  $("#logout").hidden = false;
  await refresh();
}
async function refresh() {
  const seq = ++viewId;
  const space = $("#space").value;
  if (!space) {
    $("#title").textContent = "尚未分配空间";
    $("#view").textContent = "请联系管理员授予访问权限。";
    $("#nav").replaceChildren();
    return;
  }
  const data = await api(base() + "/assets");
  if (seq !== viewId) return;
  assets = data;
  const pages = [
    "assets",
    ...(can("jobs") ? ["jobs"] : []),
    ...(can("monitor") ? ["monitor"] : []),
    ...(me.root ? ["admin"] : []),
  ];
  if (!pages.includes(tab)) tab = "assets";
  $("#nav").innerHTML = pages
    .map(
      (p) =>
        `<button data-tab="${p}" class="${tab === p ? "active" : ""}">${labels[p]}</button>`,
    )
    .join("");
  $("#title").textContent = labels[tab];
  $("#view").replaceChildren();
  let html = "";
  if (tab === "assets") html = assetView();
  if (tab === "jobs") {
    const j = await api(base() + "/jobs");
    html = card(
      "最近导入",
      table(
        ["编号", "文件", "结果", "处理说明", "提交人", "提交时间"],
        j.map((r) => [
          r.id,
          esc(r.asset),
          statusName(r.status),
          esc(r.note),
          esc(r.actor),
          stamp(r.created),
        ]),
      ),
    );
  }
  if (tab === "monitor") {
    const m = await api(base() + "/monitor");
    html = monitorView(m);
  }
  if (tab === "admin") {
    const a = await api("/api/admin");
    if (seq !== viewId) return;
    admin = a;
    html = adminView();
  }
  if (seq === viewId) $("#view").innerHTML = html;
}
function statusName(s) {
  return { ready: "已发布", queued: "等待处理", failed: "失败" }[s] || esc(s);
}
function assetView() {
  let h = can("search")
    ? card(
        "查找文档依据",
        '<form id="search" class="fields"><label class="wide">检索问题<input name="q" maxlength="500" placeholder="输入想核对的问题" required></label><button class="primary">检索</button></form><div id="hits"></div>',
      )
    : "";
  if (can("ingest"))
    h += card(
      "导入到当前空间",
      '<form id="upload" class="fields"><label>选择文件<input type="file" name="file" accept=".pdf,.docx,.png,.jpg,.jpeg,.xlsx,.csv,.html" required></label><button>提交导入</button></form><small>每份最多 12 MiB。扫描 PDF 和图片自动识别中英文文字。</small>',
    );
  return (
    h +
    card(
      "已发布文档",
      table(
        ["名称", "版本", "索引", "更新时间", "原件"],
        assets.assets.map((a) => [
          esc(a.asset),
          a.version,
          esc(a.note),
          stamp(a.updated),
          a.original ? `<a href="${base()}/source/${a.version}">下载</a>` : "—",
        ]),
      ),
    )
  );
}
function monitorView(m) {
  const metrics = [
    ["24 小时导入", m.jobs24.total],
    ["失败", m.jobs24.failed],
    ["待处理", m.jobs24.queued],
    ["活动告警", m.active],
    ["通知渠道", m.channel],
  ];
  if (m.http5)
    metrics.push(
      ["5 分钟 API 请求", m.http5.samples],
      ["5xx", m.http5.errors],
      ["P95 上界（秒）", m.http5.p95_upper_s ?? "—"],
    );
  let h =
    '<div class="metrics">' +
    metrics
      .map(
        ([k, v]) =>
          `<div class="metric"><small>${k}</small><b>${esc(v)}</b></div>`,
      )
      .join("") +
    "</div>";
  h += card(
    "监测状态",
    table(
      ["监测项", "当前结果"],
      Object.entries(m.components).map(([k, v]) => [esc(k), esc(v)]),
    ) +
      `<small>最近探测　${stamp(m.probe)} · ${m.probe && Date.now() / 1000 - m.probe < 45 ? "运行中" : "探测延迟"}</small>`,
  );
  return (
    h +
    card(
      "事件记录",
      table(
        ["事件", "影响范围", "状态", "触发 / 恢复", "通知", "操作"],
        m.events.map((e) => [
          esc(e.rule),
          esc(e.target),
          e.closed ? "已恢复" : e.ack_by ? "已认领" : "待处理",
          stamp(e.opened) + "<br>" + stamp(e.closed),
          m.channel === "站内告警" ? "站内" : esc(e.delivery),
          !e.closed && !e.ack_by && can("ack")
            ? `<button data-ack="${e.id}">认领</button>`
            : esc(e.ack_by || "—"),
        ]),
      ),
    )
  );
}
const field = (label, name, values) =>
  `<label>${label}<select name="${name}">${opt(values)}</select></label>`;
function adminView() {
  const people = admin.people
      .filter((p) => p.enabled && !p.root)
      .map((p) => p.name),
    roles = admin.roles.map((r) => r.name);
  let h = card(
    "账号与空间",
    '<form id="create" class="fields"><label>类型<select name="kind"><option value="account">人员 / 服务账号</option><option value="space">工作空间</option></select></label><label>名称<input name="name" required></label><button>创建</button></form><div id="newkey"></div>',
  );
  h += card(
    "角色",
    table(
      ["角色", "允许的操作"],
      admin.roles.map((r) => [
        esc(r.name),
        r.actions.map((a) => actions[a]).join("、"),
      ]),
    ) +
      `<form id="role" class="fields"><label>新角色名称<input name="name" required></label><div class="actions">${admin.actions.map((a) => `<label><input type="checkbox" name="action" value="${a}">${actions[a]}</label>`).join("")}</div><button>创建角色</button></form>`,
  );
  h += card(
    "授权范围",
    `<form id="grant" class="fields">${field("账号", "name", people)}${field("空间", "space", admin.spaces)}${field("角色", "role", roles)}<label>文件范围<input name="asset" placeholder="留空代表整个空间"></label><button>授予</button></form>` +
      table(
        ["账号", "空间", "角色", "文件", "操作"],
        admin.grants.map((g, i) => [
          esc(g.person),
          esc(g.space),
          esc(g.role),
          esc(g.asset || "全部"),
          `<button data-revoke="${i}">撤销</button>`,
        ]),
      ),
  );
  h += card(
    "账号状态",
    table(
      ["账号", "状态", "操作"],
      admin.people.map((p) => [
        esc(p.name),
        p.enabled ? "可用" : "已停用",
        p.enabled && !p.root
          ? `<button data-disable="${esc(p.name)}">停用</button>`
          : "—",
      ]),
    ),
  );
  return (
    h +
    card(
      "管理操作记录",
      table(
        ["操作人", "动作", "变更", "时间"],
        admin.audit.map((r) => [
          esc(r.actor),
          esc(r.action),
          esc(r.details),
          stamp(r.ts),
        ]),
      ),
    )
  );
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  try {
    if (b.dataset.tab) {
      tab = b.dataset.tab;
      await refresh();
    } else if (b.id === "refresh") await refresh();
    else if (b.id === "logout") {
      await api("/api/logout", "POST");
      loginView();
    } else if (b.dataset.ack) {
      await api(base() + "/alerts/" + b.dataset.ack + "/ack", "POST");
      await refresh();
    } else if (b.dataset.revoke !== undefined) {
      const g = admin.grants[Number(b.dataset.revoke)];
      if (!confirm("撤销此项授权？")) return;
      await api("/api/admin", "POST", {
        kind: "revoke",
        name: g.person,
        space: g.space,
        role: g.role,
        asset: g.asset,
      });
      await refresh();
    } else if (b.dataset.disable) {
      if (!confirm("停用此账号及其密钥？")) return;
      await api("/api/admin", "POST", {
        kind: "disable",
        name: b.dataset.disable,
      });
      await refresh();
    }
  } catch (err) {
    msg(err.message);
  }
});
document.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target,
    data = new FormData(f),
    d = Object.fromEntries(data);
  try {
    if (f.id === "loginForm") {
      await api("/api/login", "POST", d);
      f.reset();
      await start();
      return;
    }
    if (f.id === "search") {
      const seq = viewId;
      const r = await api(base() + "/search", "POST", d);
      if (seq !== viewId) return;
      $("#hits").innerHTML =
        r.hits
          .map(
            (h) =>
              `<div class="hit"><b>${esc(h.asset)}</b> · ${esc(h.anchor)} · v${h.version}<p>${esc(h.body)}</p></div>`,
          )
          .join("") || "没有可检索片段";
      return;
    }
    if (f.id === "upload") {
      const r = await api(base() + "/imports", "POST", data);
      f.reset();
      msg("已提交任务 #" + r.job);
      if (can("jobs")) {
        tab = "jobs";
        await refresh();
      }
      return;
    }
    if (f.id === "role") {
      d.kind = "role";
      d.actions = data.getAll("action");
      delete d.action;
    }
    if (f.id === "grant") d.kind = "grant";
    const r = await api("/api/admin", "POST", d);
    if (d.kind === "space") {
      await start();
    } else await refresh();
    if (r.token) {
      $("#newkey").innerHTML =
        '<p>请保存访问密钥，本页只显示一次。</p><code class="key"></code>';
      $("#newkey code").textContent = r.token;
    } else msg("已保存");
  } catch (err) {
    msg(err.message);
  }
});
$("#space").onchange = () => refresh().catch((e) => msg(e.message));
setInterval(() => {
  if (me && tab === "monitor" && !document.hidden)
    refresh().catch((e) => msg(e.message));
}, 15000);
start().catch((e) => {
  if (e.message !== "请使用有效访问密钥登录") msg(e.message);
});
