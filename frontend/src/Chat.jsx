import React, { useEffect, useRef, useState } from "react";
import {
  App,
  Button,
  Card,
  Input,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
} from "antd";
import { Bubble, Conversations, Sender } from "@ant-design/x";
import {
  PlusOutlined,
  ArrowRightOutlined,
  BarChartOutlined,
  SearchOutlined,
  MessageOutlined,
  BookOutlined,
  SettingOutlined,
  PaperClipOutlined,
  DatabaseOutlined,
  FileWordOutlined,
  CodeOutlined,
  CloseOutlined,
  CopyOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { api } from "./api";
import { AnswerText } from "./AnswerText";
import { Mark } from "./main";
echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);
function Plot({ r }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    const chart = echarts.init(el);
    const labels = r.rows.map((x) => String(x[0]));
    const n = r.columns.length - 1;
    chart.setOption({
      tooltip: { trigger: "axis" },
      grid: { left: 65, right: 15, bottom: 42, top: 16 },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { hideOverlap: true, interval: labels.length <= 5 ? 0 : "auto" },
      },
      yAxis: { type: "value", splitLine: { lineStyle: { color: "#e7e9f1" } } },
      series: [
        {
          name: r.columns[n],
          type: "bar",
          data: r.rows.map((x) => x[n]),
          itemStyle: { color: "#6457d7", borderRadius: [5, 5, 0, 0] },
          barMaxWidth: 48,
        },
      ],
    });
    const ob = new ResizeObserver(() => chart.resize());
    ob.observe(el);
    return () => {
      ob.disconnect();
      chart.dispose();
    };
  }, [r]);
  return (
    <div
      role="img"
      aria-label={r.columns.at(-1) + "柱状图"}
      ref={ref}
      style={{ height: 225 }}
    />
  );
}
function Grid({ r }) {
  return (
    <Table
      size="small"
      pagination={r.rows.length > 10 ? { pageSize: 10 } : false}
      scroll={{ x: "max-content" }}
      rowKey="key"
      columns={r.columns.map((title, i) => ({ title, dataIndex: String(i) }))}
      dataSource={r.rows.map((v, key) => ({ ...v, key }))}
    />
  );
}
const routeNames = {
  documents: "文档检索",
  analysis: "数据分析",
  analysis_documents: "数据分析与文档检索",
  attachment: "会话附件",
  clarify: "需要补充条件",
};
const timingNames = {
  route: "路由",
  embedding: "向量生成",
  vector_search: "向量检索",
  sql_generation: "SQL 生成",
  sql_validation: "SQL 校验",
  starrocks: "StarRocks 查询",
  query_id: "读取 Query ID",
  answer_generation: "回答生成",
  total: "应用处理合计",
};
const sqlSourceNames = {
  template: "配置模板",
  deepseek: "DeepSeek Flash",
  local_model: "本地模型",
  builtin: "系统预置",
};
function SourceLinks({ base, reference: r }) {
  const path = `${base}/source/${r.version}`;
  const page = r.anchor?.match(/^第 (\d+) 页$/)?.[1];
  const element = r.anchor?.match(/ · 元素 (\d+)$/)?.[1];
  const html = r.asset.toLowerCase().endsWith(".html");
  const pdf = r.asset.toLowerCase().endsWith(".pdf");
  const target = html && element ? `#element-${element}` : pdf && page ? `#page=${page}` : "";
  return (
    <Space wrap>
      {(html || pdf) && <a href={`${path}?view=inline${target}`} target="_blank" rel="noopener noreferrer">查看原文</a>}
      <a href={path}>下载原件</a>
    </Space>
  );
}

function Diagnostics({ data, dataset }) {
  const source = data.sql_source;
  return (
    <details className="query-diagnostics">
      <summary>
        处理详情 · {routeNames[data.route]}
        {dataset && " · " + dataset.toUpperCase()}
      </summary>
      <dl>
        {source && (
          <>
            <dt>SQL 来源</dt>
            <dd>
              {sqlSourceNames[source.kind]}
              {source.id && " · " + source.id}
            </dd>
          </>
        )}
        {source?.parameters && (
          <>
            <dt>查询参数</dt>
            <dd>{Object.entries(source.parameters).map(([name, value]) => name + "=" + value).join("，")}</dd>
          </>
        )}
        {source?.definition && (
          <>
            <dt>指标口径</dt>
            <dd>{source.definition}</dd>
          </>
        )}
        {Object.entries(timingNames)
          .filter(([key]) => key in data.timings_ms)
          .map(([key, label]) => (
            <React.Fragment key={key}>
              <dt>{label}</dt>
              <dd>{data.timings_ms[key]} ms</dd>
            </React.Fragment>
          ))}
      </dl>
    </details>
  );
}
function Answer({ turn, onRef, again }) {
  const { message } = App.useApp();
  const r = turn.result;
  const chart =
    r?.rows.length > 0 &&
    r.columns.length > 1 &&
    r.rows.every((x) => typeof x.at(-1) === "number");
  return (
    <div className="answer">
      <div className="assistant">
        <Mark />
        QueryAtlas
      </div>
      <div className="prose">
        <AnswerText text={turn.text} />
      </div>
      {turn.metric && (
        <Space wrap className="metric-provenance" aria-label="指标溯源">
          <Tag color="cyan">指标版本 v{turn.metric.version}</Tag>
          <Tag>数据批次 {turn.metric.batch || "未登记"}</Tag>
          {turn.metric.rules_unavailable && <span>部分制度条款当前不可查看</span>}
        </Space>
      )}
      {r && (
        <Card
          className="query-result"
          size="small"
          title={r.truncated ? "查询结果 · 仅展示前 100 行" : "查询结果"}
          extra={<small>{r.ms} ms</small>}
        >
          <Tabs
            key={chart ? "chart" : "table"}
            size="small"
            items={[
              ...(chart
                ? [{ key: "chart", label: "图表", children: <Plot r={r} /> }]
                : []),
              { key: "table", label: "表格", children: <Grid r={r} /> },
            ]}
          />
        </Card>
      )}
      <Space wrap className="references">
        {turn.refs?.map((x, i) => (
          <Button
            key={i}
            size="small"
            type="default"
            icon={
              x.kind === "sql" ? <DatabaseOutlined /> : <FileWordOutlined />
            }
            onClick={() => onRef(i, x)}
          >
            [{i + 1}] {x.rule ? x.anchor : x.asset}
          </Button>
        ))}
      </Space>
      {turn.diagnostics && (
        <Diagnostics data={turn.diagnostics} dataset={turn.data} />
      )}
      <div>
        <Tooltip title="复制回答">
          <Button
            size="small"
            type="text"
            aria-label="复制回答"
            icon={<CopyOutlined />}
            onClick={() =>
              navigator.clipboard
                .writeText(turn.text)
                .then(() => message.success("已复制"))
                .catch(() => message.error("无法访问剪贴板"))
            }
          />
        </Tooltip>
        <Tooltip title="重新提问">
          <Button
            size="small"
            type="text"
            aria-label="重新提问"
            icon={<RedoOutlined />}
            onClick={() => again(turn.q)}
          />
        </Tooltip>
      </div>
    </div>
  );
}
export function Chat({ base, assets, datasets, move, mobile, allowed }) {
  const { message } = App.useApp();
  const [history, setHistory] = useState([]),
    [id, setId] = useState(""),
    [turns, setTurns] = useState([]),
    [q, setQ] = useState(""),
    [filter, setFilter] = useState(""),
    [source, setSource] = useState("auto"),
    [pending, setPending] = useState(""),
    [focus, setFocus] = useState(null),
    [file, setFile] = useState(null);
  const busy = useRef(false),
    controller = useRef(null),
    requestId = useRef(null),
    active = useRef(""),
    picker = useRef(null),
    sender = useRef(null),
    bottom = useRef(null);
  const url = base + "/chats";
  const list = () => api(url).then(setHistory);
  useEffect(() => {
    list().catch((e) => message.error(e.message));
    return () => controller.current?.abort();
  }, [url]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [id, turns.length, pending]);
  async function open(next) {
    if (busy.current) return;
    active.current = next;
    setId(next);
    setFocus(null);
    setFile(null);
    setQ("");
    setTurns([]);
    if (next) {
      const v = await api(url + "/" + next);
      if (active.current === next) setTurns(v);
    }
  }
  async function showReference(index, reference, selected) {
    const cid = active.current;
    try {
      const live = await api(url + "/" + cid);
      if (active.current !== cid) return;
      setTurns(live);
      const turn = live[index];
      const position = selected.rule
        ? turn?.refs?.findIndex((r) => r.rule && r.version === selected.version && r.seq === selected.seq)
        : reference;
      if (turn?.refs?.[position])
        setFocus({ turn, n: position });
      else {
        setFocus(null);
        message.info("权限或资料已变化，请重新提问。");
      }
    } catch (e) {
      setFocus(null);
      message.error(e.message);
    }
  }
  async function ensure() {
    if (active.current) return active.current;
    const r = await api(url, "POST");
    active.current = r.id;
    setId(r.id);
    return r.id;
  }
  async function send(value = q, targetSource = source) {
    value = value.trim();
    if (!value || busy.current) return;
    busy.current = true;
    setPending(value);
    setQ("");
    setFocus(null);
    const c = new AbortController();
    controller.current = c;
    try {
      const cid = await ensure();
      requestId.current = crypto.randomUUID();
      const a = await api(
        url + "/" + cid + "/ask",
        "POST",
        {
          q: value,
          source: targetSource,
          attachment: file?.id,
          request_id: requestId.current,
        },
        c.signal,
      );
      setTurns((t) => [...t, { q: value, ...a }]);
      await list();
      setFile(null);
    } catch (e) {
      if (e.name !== "AbortError") message.error(e.message);
      setQ(value);
    } finally {
      busy.current = false;
      setPending("");
    }
  }
  async function attach(e) {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    try {
      const cid = await ensure();
      let d = new FormData();
      d.set("file", f);
      setFile(await api(url + "/" + cid + "/file", "POST", d));
    } catch (e) {
      message.error(e.message);
    }
  }
  const scopeOptions = [
    { value: "auto", label: "当前空间" },
    ...(assets.actions.includes("search")
      ? [{ value: "docs", label: "文档知识库" }]
      : []),
    ...datasets.map((d) => ({ value: d.name, label: d.name.toUpperCase() })),
  ];
  const compose = (
    <>
      <input ref={picker} hidden type="file" onChange={attach} />
      {file && (
        <Tag closable onClose={() => setFile(null)}>
          {file.name}
        </Tag>
      )}
      <Sender
        ref={sender}
        value={q}
        onChange={setQ}
        onSubmit={(value) => send(value)}
        loading={!!pending}
        onCancel={() => {
          controller.current?.abort();
          if (active.current && requestId.current)
            api(
              url + "/" + active.current + "/cancel/" + requestId.current,
              "POST",
            ).catch((e) => message.error(e.message));
        }}
        placeholder="输入问题，或引用工作空间中的资料…"
        autoSize={{ minRows: 2, maxRows: 6 }}
        prefix={
          <Button
            type="text"
            icon={<PaperClipOutlined />}
            aria-label="会话附件"
            onClick={() => picker.current.click()}
          />
        }
        className="composer"
        footer={
          <Select
            aria-label="资料范围"
            value={source}
            onChange={setSource}
            options={scopeOptions}
            variant="borderless"
          />
        }
      />
    </>
  );
  return (
    <div className={"layout " + (mobile ? "mobile-open" : "")}>
      <aside aria-label="对话导航">
        <Button
          className="new-chat"
          type="primary"
          icon={<PlusOutlined />}
          block
          onClick={() => open("")}
        >
          新建对话
        </Button>
        <Input
          prefix={<SearchOutlined />}
          placeholder="搜索会话"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <small className="nav-label">最近会话</small>
        {!history.length && <span className="nav-empty">暂无会话</span>}
        <Conversations
          activeKey={id}
          onActiveChange={(x) => open(x).catch((e) => message.error(e.message))}
          items={history
            .filter((x) => x.title.includes(filter))
            .map((x) => ({
              key: x.id,
              label: x.title,
              icon: <MessageOutlined />,
            }))}
        />
        <div className="fill" />
        <div className="rail-links">
          <Button type="text" icon={<BookOutlined />} onClick={() => move("assets")}>知识资产</Button>
          {allowed.length > 1 && (
            <Button
              type="text"
              icon={<SettingOutlined />}
              onClick={() => move(allowed.find((x) => x[0] !== "assets")[0])}
            >
              管理控制台
            </Button>
          )}
        </div>
      </aside>
      <main className={"chat " + (!turns.length && !pending ? "empty" : "")}>
        {!turns.length && !pending ? (
          <div className="welcome">
            <div className="welcome-mark"><Mark /></div>
            <h1>想了解什么？</h1>
            <p>问知识库，也可以直接查询已发布的数据。</p>
            {compose}
            <div className="suggest">
              {!file && datasets.some((d) => d.name === "tpch") && (
                <button type="button" onClick={() => {
                  setSource("tpch");
                  send("TPC-H SF1 Q4", "tpch");
                }}>
                  <BarChartOutlined />
                  <span><b>TPC-H SF1 Q4</b><small>按优先级统计订单</small></span>
                  <ArrowRightOutlined />
                </button>
              )}
              {!file && assets.actions.includes("search") && assets.assets?.length > 0 && (
                <button type="button" onClick={() => {
                  setSource("docs");
                  setQ("请根据知识库资料解释：");
                  sender.current?.focus({ cursor: "end" });
                }}>
                  <BookOutlined />
                  <span><b>查找知识库依据</b><small>从已导入的文档中找出处</small></span>
                  <ArrowRightOutlined />
                </button>
              )}
            </div>
          </div>
        ) : (
          <>
            <div className="threadtitle">
              {history.find((x) => x.id === id)?.title || pending || "新对话"}
            </div>
            <div className="scroll">
              {turns.map((t, i) => (
                <section key={i}>
                  <Bubble placement="end" content={t.q} />
                  <Answer
                    turn={t}
                    onRef={(n, ref) => showReference(i, n, ref)}
                    again={send}
                  />
                </section>
              ))}
              {pending && (
                <>
                  <Bubble placement="end" content={pending} />
                  <p role="status">正在核对资料与数据…</p>
                </>
              )}
              <div ref={bottom} />
            </div>
            <div className="bottom">{compose}</div>
          </>
        )}
      </main>
      {focus && (
        <section className="evidence" aria-label="回答依据">
          <div className="between">
            <h2>回答依据</h2>
            <Button
              aria-label="关闭依据"
              icon={<CloseOutlined />}
              type="text"
              onClick={() => setFocus(null)}
            />
          </div>
          {[focus.n, ...focus.turn.refs.map((_, i) => i).filter((i) => i !== focus.n)].map((i) => {
            const r = focus.turn.refs[i];
            return (
            <article className={i === focus.n ? "selected" : ""} key={i}>
              <b>
                {i + 1}　
                {r.kind === "sql" ? <DatabaseOutlined /> : <FileWordOutlined />}{" "}
                {r.asset}
              </b>
              {r.rule && <p>制度条款 · 文档版本 {r.version}</p>}
              <p>{r.anchor || "StarRocks 查询结果"}</p>
              {r.query_ids?.map((id) => (
                <p className="query-id" key={id}>
                  Query ID <code>{id}</code>
                </p>
              ))}
              {r.body && <blockquote>{r.body}</blockquote>}
              {r.original && (
                <SourceLinks base={base} reference={r} />
              )}
              {r.sql && (
                <details open>
                  <summary>
                    <CodeOutlined /> 查看 SQL
                  </summary>
                  <pre>{r.sql}</pre>
                </details>
              )}
            </article>
            );
          })}
        </section>
      )}
    </div>
  );
}
