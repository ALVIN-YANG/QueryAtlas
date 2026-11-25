export async function api(url, method = "GET", body, signal) {
  const opts = { method, headers: { "X-QueryAtlas": "1" }, signal };
  if (body instanceof FormData) opts.body = body;
  else if (body) {
    opts.body = JSON.stringify(body);
    opts.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(url, opts);
  const value = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401) window.dispatchEvent(new Event("qa:expired"));
    throw Error(
      typeof value.detail === "string"
        ? value.detail
        : "请求失败（" + r.status + "）",
    );
  }
  return value;
}
export const esc = encodeURIComponent;
export const stamp = (v) =>
  v
    ? new Date(typeof v === "number" ? v * 1000 : v).toLocaleString("zh-CN", {
        hour12: false,
      })
    : "—";
export const options = (xs) => xs.map((value) => ({ value, label: value }));
export const names = {
  search: "检索片段",
  original: "下载原件",
  ingest: "导入资料",
  jobs: "查看任务",
  monitor: "查看监控",
  ack: "认领告警",
  analysis: "数据汇总",
  detail: "分析明细",
  sql: "查看 SQL",
  publish: "发布分析表",
  templates: "管理 SQL 模板",
  mysql_view: "查看同步记录",
  mysql_manage: "配置 MySQL 数据源",
  mysql_run: "触发 MySQL 同步",
};
