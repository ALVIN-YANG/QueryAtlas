import React, { useEffect, useState } from "react";
import { Alert, Select, Space, Tag } from "antd";
import { api } from "./api";

export function RuleClausePicker({ endpoint, value = [], onChange, id }) {
  const [documents, setDocuments] = useState(null);
  const [version, setVersion] = useState(null);
  const [clauses, setClauses] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = new AbortController();
    api(endpoint + "/rule-documents", "GET", undefined, load.signal)
      .then(setDocuments)
      .catch((e) => { if (e.name !== "AbortError") setError(e.message); });
    return () => load.abort();
  }, [endpoint]);

  useEffect(() => {
    if (!version) return;
    const load = new AbortController();
    api(`${endpoint}/rule-documents/${version}/clauses`, "GET", undefined, load.signal)
      .then(setClauses)
      .catch((e) => { if (e.name !== "AbortError") setError(e.message); });
    return () => load.abort();
  }, [endpoint, version]);

  const document = documents?.find((row) => row.version === version);
  const options = clauses?.filter((row) => !value.some((r) => r.version === row.version && r.seq === row.seq)) || [];

  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      {error && <Alert type="error" title={error} showIcon />}
      <Select
        id={id}
        aria-label="选择制度文档"
        placeholder="选择已发布的制度文档"
        loading={!documents && !error}
        value={version}
        allowClear
        showSearch={{ optionFilterProp: "label" }}
        notFoundContent="没有可查看的已发布文档"
        options={documents?.map((row) => ({ value: row.version, label: `${row.asset} · 文档版本 ${row.version}` }))}
        onChange={(next) => { setClauses(null); setError(""); setVersion(next); }}
        style={{ width: "100%" }}
      />
      {document && (
        <Select
          aria-label="选择制度段落"
          placeholder={value.length >= 5 ? "最多绑定 5 条" : "选择要绑定的段落"}
          value={null}
          loading={!clauses && !error}
          disabled={value.length >= 5}
          showSearch={{ optionFilterProp: "label" }}
          options={options.map((row) => ({ value: row.seq, label: `${row.anchor} · ${row.excerpt}` }))}
          onChange={(seq) => {
            const row = options.find((candidate) => candidate.seq === seq);
            if (row) onChange([...value, { version, seq, asset: document.asset, anchor: row.anchor }]);
          }}
          style={{ width: "100%" }}
        />
      )}
      {value.map((row) => (
        <Tag key={`${row.version}/${row.seq}`} closable onClose={(event) => {
          event.preventDefault();
          onChange(value.filter((r) => r.version !== row.version || r.seq !== row.seq));
        }}>
          {row.asset} · 文档版本 {row.version} · {row.anchor}
        </Tag>
      ))}
    </Space>
  );
}
