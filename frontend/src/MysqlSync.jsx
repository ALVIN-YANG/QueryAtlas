import React, { useEffect, useState } from "react";
import { Alert, App, Button, Form, Input, Modal, Select, Space, Table, Tag } from "antd";
import { api, esc, stamp } from "./api";

const status = {
  queued: ["排队中", "default"],
  running: ["同步中", "processing"],
  published: ["已发布", "success"],
  unchanged: ["无变更", "default"],
  failed: ["失败", "error"],
};
const modes = { full: "全量", incremental: "增量" };

export function MysqlSync({ base, perms, refresh }) {
  const url = base + "/mysql-sync";
  const [view, setView] = useState(null);
  const [error, setError] = useState("");
  const [editor, setEditor] = useState(null);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const manage = perms.includes("mysql_manage");
  const run = perms.includes("mysql_run");

  async function reload(signal) {
    try {
      const next = await api(url, "GET", null, signal);
      if (signal?.aborted) return;
      setView(next);
      setError("");
    } catch (err) {
      if (!signal?.aborted) setError(err.message);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    setView(null);
    setEditor(null);
    async function poll() {
      await reload(controller.signal);
      if (!controller.signal.aborted) timer = setTimeout(poll, 3000);
    }
    let timer;
    poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [url]);

  const published = view?.sources.map((item) => item.batch).join("|");
  useEffect(() => {
    if (published) refresh();
  }, [published]);

  function edit(source) {
    form.setFieldsValue({
      dataset: source?.dataset || "",
      description: source?.description || "",
      profile: source?.profile,
      mapping: source ? JSON.stringify(source.tables, null, 2) : "",
    });
    setEditor(source || {});
  }

  async function save(values) {
    let tables;
    try {
      tables = JSON.parse(values.mapping);
    } catch {
      form.setFields([{ name: "mapping", errors: ["请输入有效的 JSON 表映射"] }]);
      return;
    }
    setBusy(true);
    try {
      await api(url + "/sources/" + esc(values.dataset), "PUT", {
        profile: values.profile, description: values.description, tables,
      });
      setEditor(null);
      message.success("数据源已保存");
      await reload();
    } catch (err) {
      message.error(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function trigger(source, mode) {
    setBusy(true);
    try {
      const response = await api(url + "/sources/" + esc(source.dataset) + "/runs", "POST", { mode });
      message.success(`已提交${modes[mode]}同步 #${response.id}`);
      await reload();
    } catch (err) {
      message.error(err.message);
    } finally {
      setBusy(false);
    }
  }

  const sources = view?.sources || [];
  const runs = view?.runs || [];
  return (
    <>
      {error && <Alert type="error" title={error} showIcon />}
      <div className="between">
        <h2>MySQL 数据源</h2>
        {manage && <Button type="primary" onClick={() => edit(null)}>添加数据源</Button>}
      </div>
      <Table
        rowKey="dataset"
        size="middle"
        loading={!view && !error}
        scroll={{ x: "max-content" }}
        dataSource={sources}
        pagination={false}
        columns={[
          { title: "数据集", dataIndex: "dataset" },
          { title: "说明", dataIndex: "description" },
          { title: "当前发布批次", dataIndex: "batch", render: (v) => v || "尚未发布" },
          ...(manage || run ? [{
            title: "操作",
            render: (_, source) => {
              const active = runs.some((r) => r.dataset === source.dataset && ["queued", "running"].includes(r.status));
              return <Space wrap>
                {manage && <Button disabled={active} onClick={() => edit(source)}>编辑</Button>}
                {run && <Button disabled={active || busy} onClick={() => trigger(source, "full")}>全量同步</Button>}
                {run && <Button disabled={active || busy} onClick={() => trigger(source, "incremental")}>增量同步</Button>}
              </Space>;
            },
          }] : []),
        ]}
      />
      <h2>同步记录</h2>
      <Table
        rowKey="id"
        size="middle"
        scroll={{ x: "max-content" }}
        dataSource={runs}
        columns={[
          { title: "任务", dataIndex: "id", render: (v) => "#" + v },
          { title: "数据集", dataIndex: "dataset" },
          { title: "方式", dataIndex: "mode", render: (v) => modes[v] },
          { title: "状态", dataIndex: "status", render: (v) => <Tag color={status[v][1]}>{status[v][0]}</Tag> },
          { title: "批次", dataIndex: "result", render: (v) => v?.batch || "—" },
          { title: "操作人", dataIndex: "actor" },
          { title: "提交时间", dataIndex: "created", render: stamp },
          { title: "耗时", dataIndex: "result", key: "seconds", render: (v) => v?.seconds == null ? "—" : `${v.seconds} 秒` },
          { title: "失败原因", dataIndex: "error", render: (v) => v || "—" },
        ]}
        expandable={{
          rowExpandable: (r) => Boolean(r.result?.tables),
          expandedRowRender: (r) => (
            <ul>
              {Object.entries(r.result.tables).map(([table, count]) => (
                <li key={table}>{table} · 发布 {count.rows.toLocaleString()} 行 · 读取 {count.read.toLocaleString()} 行 · 删除键 {count.delete_keys.toLocaleString()} 个</li>
              ))}
            </ul>
          ),
        }}
      />
      <Modal
        title={editor?.dataset ? "编辑 MySQL 数据源" : "添加 MySQL 数据源"}
        open={Boolean(editor)}
        onCancel={() => setEditor(null)}
        onOk={() => form.submit()}
        confirmLoading={busy}
        okText="保存数据源"
        forceRender
      >
        <Form layout="vertical" form={form} onFinish={save}>
          <Form.Item label="数据集名称" name="dataset" rules={[{ required: true, pattern: /^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/, message: "请输入英文数据集名称，例如 sales" }]}>
            <Input disabled={Boolean(editor?.dataset)} />
          </Form.Item>
          <Form.Item label="说明" name="description" rules={[{ required: true }]}>
            <Input maxLength={160} />
          </Form.Item>
          <Form.Item label="MySQL 连接" name="profile" rules={[{ required: true }]}>
            <Select options={view?.profiles || []} placeholder="选择已授权的连接" />
          </Form.Item>
          {view && !view.profiles.length && <Alert type="info" title="请让平台管理员为本空间登记 MySQL 连接。" showIcon />}
          <Form.Item label="表映射（JSON）" name="mapping" rules={[{ required: true }]}>
            <Input.TextArea rows={8} placeholder={'{"orders":{"key":["id"],"columns":{"id":"BIGINT","amount":"DECIMAL(15,2)"}}}'} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
