import React, { useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Descriptions,
  Input,
  Popconfirm,
  Select,
  Space,
  Statistic,
  Steps,
  Table,
  Tabs,
  Tag,
  Upload,
} from "antd";
import {
  CloudUploadOutlined,
  DatabaseOutlined,
  FileOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { api, esc, stamp, options } from "./api";
import { MysqlSync } from "./MysqlSync";
const col = (title, dataIndex, render) => ({ title, dataIndex, render });
const states = {
  queued: "排队中",
  ready: "已发布",
  failed: "需复核",
  draft: "待映射",
  validated: "待发布",
  published: "已发布",
};
const label = (s) => (
  <Tag
    color={
      ["ready", "published"].includes(s)
        ? "green"
        : s === "failed"
          ? "orange"
          : "blue"
    }
  >
    {states[s] || s}
  </Tag>
);
function useFeed(fn, deps, interval = 0) {
  const { message } = App.useApp();
  const [data, setData] = useState(null),
    [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true,
      last = 0;
    const load = async () => {
      const seq = ++last;
      try {
        const v = await fn();
        if (live && last === seq) setData(v);
      } catch (e) {
        if (live) message.error(e.message);
      }
    };
    load();
    let timer = interval && setInterval(load, interval);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [...deps, tick]);
  return [data, () => setTick((i) => i + 1)];
}
export function Assets({ assets, datasets, base }) {
  const [show, setShow] = useState(null);
  const { message } = App.useApp();
  return (
    <>
      <Tabs
        items={[
          {
            key: "docs",
            label: "文档知识库",
            children: (
              <Table
                rowKey="version"
                size="middle"
                scroll={{ x: "max-content" }}
                dataSource={assets.assets}
                columns={[
                  col("文件", "asset", (v) => (
                    <>
                      <span className="asset-name"><FileOutlined /> {v}</span>
                    </>
                  )),
                  col("版本", "version"),
                  col("片段", "note"),
                  col("更新时间", "updated", stamp),
                  col("原件", "original", (yes, r) =>
                    yes ? (
                      <a href={base + "/source/" + r.version}>下载原件</a>
                    ) : (
                      "未授权"
                    ),
                  ),
                ]}
              />
            ),
          },
          {
            key: "data",
            label: "分析数据",
            children: (
              <Table
                rowKey="name"
                size="middle"
                scroll={{ x: "max-content" }}
                dataSource={datasets}
                columns={[
                  col("数据集", "name", (v) => <span className="asset-name"><DatabaseOutlined /> {v}</span>),
                  col("说明", "description"),
                  col("数据批次", "batch", (v) => v ? <code>{v}</code> : "—"),
                  col("", "name", (n) => (
                    <Button
                      type="link"
                      onClick={() =>
                        api(base + "/datasets/" + esc(n))
                          .then(setShow)
                          .catch((e) => message.error(e.message))
                      }
                    >
                      查看表与字段
                    </Button>
                  )),
                ]}
              />
            ),
          },
        ]}
      />
      <Drawer open={!!show} onClose={() => setShow(null)} title={show?.name} size="large">
        <Descriptions column={1} size="small" items={[
          { key: "description", label: "说明", children: show?.description },
          { key: "batch", label: "发布批次", children: show?.batch ? <code>{show.batch}</code> : "未登记" },
        ]} />
        {Object.entries(show?.tables || {}).map(([name, fields]) => (
          <section className="catalog-table" key={name}>
            <h3><DatabaseOutlined /> {name}</h3>
            <Space wrap size={[6, 6]}>
              {fields.map((field) => <Tag key={field}><code>{field}</code></Tag>)}
            </Space>
          </section>
        ))}
      </Drawer>
    </>
  );
}
function Docs({ base, perms }) {
  const { message } = App.useApp();
  const [list, reload] = useFeed(
    () => (perms.includes("jobs") ? api(base + "/jobs") : Promise.resolve([])),
    [base],
    5000,
  );
  const [item, setItem] = useState(null),
    [sending, setSending] = useState(false);
  const submit = async (file, replace) => {
    setSending(true);
    try {
      const d = new FormData();
      d.set("file", file, replace || file.name);
      const v = await api(base + "/imports", "POST", d);
      message.success("已提交任务 #" + v.job);
      setItem(null);
      reload();
    } catch (e) {
      message.error(e.message);
    } finally {
      setSending(false);
    }
  };
  return (
    <>
      {perms.includes("ingest") && (
        <Upload.Dragger
          multiple
          accept=".pdf,.docx,.png,.jpg,.jpeg,.html,.xlsx,.csv"
          showUploadList={false}
          disabled={sending}
          beforeUpload={(f) => {
            submit(f);
            return false;
          }}
        >
          <CloudUploadOutlined className="upload-icon" />
          <h3>拖入文件，或选择文件</h3>
          <p>PDF、Word、图片、HTML、CSV、XLSX · 每份最多 12 MiB</p>
        </Upload.Dragger>
      )}
      <Table
        rowKey="id"
        dataSource={list || []}
        columns={[
          col("文件", "asset"),
          col("任务", "id"),
          col("状态", "status", label),
          col("处理结果", "note"),
          col("提交时间", "created", stamp),
          col("", "id", (_, r) => (
            <Button type="link" onClick={() => setItem(r)}>
              查看
            </Button>
          )),
        ]}
      />
      <Drawer title={item?.asset} open={!!item} onClose={() => setItem(null)}>
        {item && (
          <>
            <Tag>{states[item.status]}</Tag>
            <p>{item.note || "等待导入进程处理"}</p>
            {item.status === "failed" && (
              <Alert
                type="warning"
                title="本次导入失败，已有发布版本仍然有效。"
              />
            )}
            {perms.includes("ingest") && (
              <Upload
                showUploadList={false}
                beforeUpload={(f) => {
                  submit(f, item.asset);
                  return false;
                }}
              >
                <Button>选择替换文件并提交</Button>
              </Upload>
            )}
          </>
        )}
      </Drawer>
    </>
  );
}
function DataImport({ base, perms, refresh }) {
  const { message } = App.useApp();
  const [list, reload] = useFeed(() => api(base + "/candidates"), [base]);
  const [name, setName] = useState("orders_demo"),
    [candidate, setCandidate] = useState(null),
    [map, setMap] = useState([]),
    [busy, setBusy] = useState(false);
  async function work(f) {
    setBusy(true);
    try {
      await f();
    } catch (e) {
      message.error(e.message);
    } finally {
      setBusy(false);
    }
  }
  const input = (file) =>
    work(async () => {
      const d = new FormData();
      d.set("name", name);
      d.set("file", file);
      const r = await api(base + "/candidates", "POST", d);
      setCandidate({ ...r, file: file.name });
      setMap(
        r.header.map((col, i) => ({
          name: /^[a-zA-Z_]\w*$/.test(col) ? col : "field_" + (i + 1),
          type: /^\d+$/.test(r.sample[0]?.[i])
            ? "BIGINT"
            : /^\d{4}-\d{2}-\d{2}$/.test(r.sample[0]?.[i])
              ? "DATE"
              : "VARCHAR",
        })),
      );
      reload();
    });
  const validate = () =>
    work(async () => {
      await api(base + "/candidates/" + candidate.id + "/validate", "POST", {
        columns: map,
      });
      setCandidate({ ...candidate, valid: true });
      reload();
      message.success("完整数据已通过校验");
    });
  const publish = (id) =>
    work(async () => {
      const r = await api(base + "/candidates/" + id + "/publish", "POST");
      message.success(
        "已发布到 StarRocks" + (r.rows ? " · " + r.rows + " 行" : ""),
      );
      setCandidate(null);
      reload();
      refresh();
    });
  const change = (i, obj) => {
    setMap((old) => old.map((v, j) => (j === i ? { ...v, ...obj } : v)));
    setCandidate({ ...candidate, valid: false });
  };
  return (
    <>
      <Steps
        size="small"
        current={candidate ? (candidate.valid ? 2 : 1) : 0}
        items={["选择文件", "字段映射", "校验发布"].map((title) => ({ title }))}
      />
      {perms.includes("ingest") && (
        <Card>
          <Space wrap>
            目标表{" "}
            <Input
              aria-label="目标表"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={!!candidate}
            />
            <Upload
              beforeUpload={(f) => {
                input(f);
                return false;
              }}
              accept=".csv"
              showUploadList={false}
            >
              <Button
                icon={<CloudUploadOutlined />}
                disabled={busy || !!candidate}
              >
                选择 CSV 文件
              </Button>
            </Upload>
          </Space>
          <p className="muted">
            UTF-8 CSV，最多 4 MiB、50,000 行。选择新名称可发布另一张表。
          </p>
        </Card>
      )}
      {candidate && (
        <Card
          title={"核对字段映射 · " + candidate.file}
          extra={candidate.count + " 行"}
        >
          <Table
            pagination={false}
            rowKey="i"
            dataSource={candidate.header.map((h, i) => ({ i, h }))}
            columns={[
              col("源字段", "h"),
              col("目标字段", "i", (i) => (
                <Input
                  value={map[i].name}
                  onChange={(e) => change(i, { name: e.target.value })}
                />
              )),
              col("类型", "i", (i) => (
                <Select
                  value={map[i].type}
                  options={options(["BIGINT", "VARCHAR", "DECIMAL", "DATE"])}
                  onChange={(type) => change(i, { type })}
                />
              )),
              col("样例值", "i", (i) => candidate.sample[0]?.[i]),
            ]}
          />
          <Space>
            <Button onClick={() => setCandidate(null)}>返回选择</Button>
            <Button type="primary" loading={busy} onClick={validate}>
              运行校验
            </Button>
          </Space>
        </Card>
      )}
      <Table
        rowKey="id"
        dataSource={list || []}
        columns={[
          col("目标表", "name"),
          col("提交人", "owner_name"),
          col("状态", "status", label),
          col("候选时间", "created", stamp),
          col("操作", "status", (v, r) =>
            v === "validated" && perms.includes("publish") ? (
              <Popconfirm
                title="发布这个版本？"
                onConfirm={() => publish(r.id)}
              >
                <Button loading={busy}>确认发布</Button>
              </Popconfirm>
            ) : null,
          ),
        ]}
      />
    </>
  );
}
function Official({ base, refresh }) {
  const { message } = App.useApp();
  const [packs] = useFeed(() => api(base + "/benchmarks"), [base]);
  const [busy, setBusy] = useState("");
  async function attach(n) {
    setBusy(n);
    try {
      await api(base + "/benchmarks/" + n, "POST");
      message.success("行数已核对，基准数据集已接入本空间");
      refresh();
    } catch (e) {
      message.error(e.message);
    } finally {
      setBusy("");
    }
  }
  return (
    <>
      <p className="muted">
        三个 SF1 快照已由装载进程写入
        StarRocks。接入时逐表核对行数，多个空间可以复用同一份只读数据。
      </p>
      <Table
        rowKey="name"
        pagination={false}
        dataSource={packs || []}
        expandable={{
          expandedRowRender: (r) => (
            <p>
              {Object.entries(r.counts)
                .map(([t, n]) => t + " · " + n.toLocaleString())
                .join("　 /　 ")}
            </p>
          ),
        }}
        columns={[
          col("基准", "name", (v) => v.toUpperCase()),
          col("查询数", "nquery"),
          col("实测 P95", "p95", (v) => v + " ms"),
          col("操作", "name", (n) => (
            <Button loading={busy === n} onClick={() => attach(n)}>
              校验并接入
            </Button>
          )),
        ]}
      />
    </>
  );
}
export function Imports(props) {
  return (
    <Tabs
      items={[
        ...(props.perms.some((x) => ["ingest", "jobs"].includes(x))
          ? [{ key: "docs", label: "文档知识库", children: <Docs {...props} /> }]
          : []),
        ...(props.perms.includes("mysql_view")
          ? [{ key: "mysql", label: "MySQL 同步", children: <MysqlSync {...props} />, destroyOnHidden: true }]
          : []),
        ...(props.perms.includes("publish")
          ? [
              {
                key: "benchmarks",
                label: "官方基准",
                children: <Official {...props} />,
              },
            ]
          : []),
        ...(props.perms.some((x) => ["ingest", "publish"].includes(x))
          ? [
              {
                key: "data",
                label: "分析数据",
                children: <DataImport {...props} />,
              },
            ]
          : []),
      ]}
    />
  );
}
export function Monitor({ base, perms }) {
  const { message } = App.useApp();
  const [m, reload] = useFeed(() => api(base + "/monitor"), [base], 15000);
  if (!m) return null;
  return (
    <>
      <div className="stats">
        {[
          ["24 小时导入", m.jobs24.total],
          ["失败任务", m.jobs24.failed],
          ["活动告警", m.active],
          ["通知渠道", m.channel],
          ...(m.http5
            ? [
                ["近 5 分钟请求", m.http5.samples],
                ["5xx", m.http5.errors],
                ["P95 上界（秒）", m.http5.p95_upper_s ?? "—"],
              ]
            : []),
        ].map(([title, value]) => (
          <Card key={title}>
            <Statistic title={title} value={value} />
          </Card>
        ))}
      </div>
      <Card
        title="依赖状态"
        extra={
          <Button icon={<ReloadOutlined />} onClick={reload}>
            刷新
          </Button>
        }
      >
        <Space wrap>
          {Object.entries(m.components).map(([k, v]) => (
            <Tag key={k}>
              {k} · {v}
            </Tag>
          ))}
        </Space>
        <p>
          最近探测　{stamp(m.probe)}{" "}
          {Date.now() / 1000 - m.probe > 45 ? " · 探测延迟" : ""}
        </p>
      </Card>
      <Table
        rowKey="id"
        dataSource={m.events}
        columns={[
          col("事件", "rule"),
          col("对象", "target"),
          col("发生时间", "opened", stamp),
          col("状态", "closed", (v, r) =>
            v ? "已恢复" : r.ack_by ? "已认领" : "待处理",
          ),
          col("通知", "delivery"),
          col("操作", "id", (id, r) =>
            !r.closed && !r.ack_by && perms.includes("ack") ? (
              <Button
                onClick={() =>
                  api(base + "/alerts/" + id + "/ack", "POST")
                    .then(reload)
                    .catch((e) => message.error(e.message))
                }
              >
                认领
              </Button>
            ) : (
              r.ack_by
            ),
          ),
        ]}
      />
    </>
  );
}
