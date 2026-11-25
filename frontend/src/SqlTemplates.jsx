import React, { useEffect, useState } from "react";
import {
  Alert, App, Button, Drawer, Form, Input, Select, Space, Table, Tag,
} from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { api, esc, stamp } from "./api";
import { RuleClausePicker } from "./RuleClausePicker";

function parseObject(text, label) {
  if (!text?.trim()) return {};
  try {
    const value = JSON.parse(text);
    if (value && typeof value === "object" && !Array.isArray(value)) return value;
  } catch {
    // The field label guides the user to the invalid input.
  }
  throw Error(label + "须为 JSON 对象");
}

function TemplateEditor({ endpoint, item, datasets, close, saved }) {
  const [form] = Form.useForm();
  const [busy, setBusy] = useState("");
  const [preview, setPreview] = useState(null);
  const selected = Form.useWatch("dataset", form);
  const { message } = App.useApp();

  async function run() {
    const values = await form.validateFields(["dataset", "sql", "parameters", "previewValues"]).catch(() => null);
    if (!values) return;
    setBusy("preview");
    setPreview(null);
    try {
      setPreview(await api(endpoint + "/preview", "POST", {
        dataset: values.dataset,
        sql: values.sql,
        parameters: parseObject(values.parameters, "参数定义"),
        values: parseObject(values.previewValues, "试跑参数值"),
      }));
    } catch (e) {
      message.error(e.message);
    } finally {
      setBusy("");
    }
  }

  async function save(values) {
    const { id, dataset, sql, aliases, parameters, definition, rule_clauses } = values;
    setBusy("save");
    try {
      const content = {
        dataset, sql, definition,
        parameters: parseObject(parameters, "参数定义"),
        aliases: aliases.split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
        rule_clauses: (rule_clauses || []).map(({ version, seq }) => ({ version, seq })),
      };
      if (item.id) {
        await api(endpoint + "/" + esc(item.id), "PUT", {
          ...content, revision: item.revision,
        });
      } else {
        await api(endpoint, "POST", { ...content, id });
      }
      message.success(item.enabled ? "已保存并生效" : "已保存");
      saved();
    } catch (e) {
      message.error(e.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <Drawer
      open
      title={item.id ? "编辑 SQL 模板" : "新增 SQL 模板"}
      width="min(720px, 100vw)"
      onClose={() => !busy && close()}
      footer={(
        <Space wrap>
          <Button onClick={close} disabled={!!busy}>取消</Button>
          <Button
            onClick={run}
            loading={busy === "preview"}
            disabled={busy === "save" || !datasets.find((d) => d.name === selected)?.detail}
          >试跑</Button>
          <Button type="primary" onClick={form.submit} loading={busy === "save"} disabled={busy === "preview"}>
            {item.id ? "保存修改" : "保存草稿"}
          </Button>
        </Space>
      )}
    >
      <Form
        form={form}
        layout="vertical"
        disabled={!!busy}
        initialValues={{
          ...item,
          aliases: item.aliases?.join("\n"),
          parameters: Object.keys(item.parameters || {}).length ? JSON.stringify(item.parameters, null, 2) : "",
          dataset: item.dataset || (datasets.length === 1 ? datasets[0].name : undefined),
        }}
        onValuesChange={() => setPreview(null)}
        onFinish={save}
      >
        <Form.Item
          name="id" label="模板编号"
          rules={[{ required: true, pattern: /^[a-zA-Z0-9_-]{1,80}$/, message: "请输入 1–80 位字母、数字、下划线或连字符" }]}
        >
          <Input disabled={!!item.id || !!busy} maxLength={80} />
        </Form.Item>
        <Form.Item name="dataset" label="数据集" rules={[{ required: true, message: "请选择数据集" }]}>
          <Select options={datasets.map((d) => ({ value: d.name, label: d.name.toUpperCase() }))} />
        </Form.Item>
        <Form.Item
          name="aliases" label="触发问句"
          extra="每行一条。参数化问句可用 {as_of} 等占位符。"
          rules={[{ required: true, whitespace: true, message: "请输入触发问句" }]}
        >
          <Input.TextArea rows={3} />
        </Form.Item>
        <Form.Item
          name="sql" label="只读 SQL"
          rules={[{ required: true, whitespace: true, message: "请输入只读 SQL" }]}
        >
          <Input.TextArea rows={6} maxLength={20000} spellCheck={false} style={{ fontFamily: "monospace" }} />
        </Form.Item>
        <Form.Item name="parameters" label="参数定义（JSON，可选）" extra="在 SQL 比较条件中使用 :as_of。支持 date、integer 和带 choices 的 enum。">
          <Input.TextArea rows={3} placeholder={'{"as_of":{"type":"date","label":"截止日期"}}'} spellCheck={false} />
        </Form.Item>
        <Form.Item name="previewValues" label="试跑参数值（JSON，可选）">
          <Input.TextArea rows={2} placeholder={'{"as_of":"2026-10-31"}'} spellCheck={false} />
        </Form.Item>
        <Form.Item name="definition" label="指标口径（可选）">
          <Input.TextArea rows={2} maxLength={650} />
        </Form.Item>
        <Form.Item name="rule_clauses" label="制度条款（可选）" extra="绑定已发布的文档版本和段落。更新文档后可在此切换版本。">
          <RuleClausePicker endpoint={endpoint} />
        </Form.Item>
      </Form>
      {preview && (
        <section aria-label="试跑结果">
          <h3>试跑结果</h3>
          <p>{preview.truncated ? "仅显示前 20 行" : preview.rows.length + " 行"} · {preview.ms} ms</p>
          {preview.query_id && <p className="query-id">Query ID <code>{preview.query_id}</code></p>}
          <Table
            size="small"
            scroll={{ x: "max-content" }}
            pagination={false}
            rowKey="key"
            columns={preview.columns.map((title, index) => ({
              title, dataIndex: String(index),
              render: (value) => value == null ? "—" : String(value),
            }))}
            dataSource={preview.rows.map((row, key) => ({ ...row, key }))}
          />
        </section>
      )}
    </Drawer>
  );
}

export function SqlTemplates({ base }) {
  const endpoint = base + "/sql-templates";
  const { message } = App.useApp();
  const [editor, setEditor] = useState(null);
  const [data, setData] = useState(null);
  const [version, refresh] = useState(0);
  const [changing, setChanging] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api(endpoint, "GET", undefined, controller.signal)
      .then(setData)
      .catch((e) => { if (e.name !== "AbortError") message.error(e.message); });
    return () => controller.abort();
  }, [endpoint, version, message]);
  const datasets = data?.datasets || [];
  const reload = () => refresh((n) => n + 1);

  async function toggle(item) {
    setChanging(item.id);
    try {
      const saved = await api(endpoint + "/" + esc(item.id) + "/status", "POST", {
        enabled: !item.enabled, revision: item.revision,
      });
      setData((old) => ({
        ...old,
        templates: old.templates.map((row) => row.id === item.id ? { ...row, ...saved } : row),
      }));
      message.success(item.enabled ? "模板已停用" : "模板已启用");
    } catch (e) {
      message.error(e.message);
    } finally {
      setChanging("");
    }
  }

  return (
    <>
      <Space wrap style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditor({})} disabled={!datasets.length}>
          新增模板
        </Button>
        <Button icon={<ReloadOutlined />} onClick={reload} disabled={!!changing}>刷新</Button>
      </Space>
      {data && !datasets.length && (
        <Alert title="管理模板需要相应数据集的分析权限和 SQL 权限。" type="info" showIcon />
      )}
      <Table
        rowKey="id"
        loading={!data}
        scroll={{ x: 850 }}
        dataSource={data?.templates || []}
        columns={[
          { title: "模板编号", dataIndex: "id" },
          { title: "数据集", dataIndex: "dataset", render: (v) => v.toUpperCase() },
          { title: "指标版本", dataIndex: "revision", render: (v) => "v" + v },
          { title: "触发问句", dataIndex: "aliases", width: 290, ellipsis: true, render: (v) => v.join(" / ") },
          { title: "状态", dataIndex: "enabled", render: (v) => <Tag color={v ? "green" : "default"}>{v ? "已启用" : "已停用"}</Tag> },
          { title: "更新时间", dataIndex: "updated", render: stamp },
          {
            title: "操作", key: "action",
            render: (_, item) => (
              <Space>
                <Button type="link" onClick={() => setEditor(item)} disabled={!!changing || item.rules_restricted}>编辑</Button>
                {item.rules_restricted && <Tag>部分条款不可查看</Tag>}
                <Button type="link" loading={changing === item.id} disabled={!!changing} onClick={() => toggle(item)}>
                  {item.enabled ? "停用" : "启用"}
                </Button>
              </Space>
            ),
          },
        ]}
      />
      {editor && (
        <TemplateEditor
          endpoint={endpoint} item={editor} datasets={datasets}
          close={() => setEditor(null)}
          saved={() => { setEditor(null); reload(); }}
        />
      )}
    </>
  );
}
