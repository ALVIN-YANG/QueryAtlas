import React, { useEffect, useState } from "react";
import {
  App,
  Alert,
  Button,
  Checkbox,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
} from "antd";
import { api, esc, names, options, stamp } from "./api";
const col = (title, dataIndex, render) => ({ title, dataIndex, render });
export function Members({ space, page, base, assets, datasets, reload }) {
  const { message } = App.useApp();
  const [a, setA] = useState(null),
    [person, setPerson] = useState(null),
    [q, setQ] = useState(""),
    [type, setType] = useState("account"),
    [label, setLabel] = useState(""),
    [actions, setActions] = useState([]),
    [newKey, setNewKey] = useState(""),
    [role, setRole] = useState("阅读者"),
    [resource, setResource] = useState(""),
    [dn, setDn] = useState(),
    [schema, setSchema] = useState({}),
    [limits, setLimits] = useState([]),
    [table, setTable] = useState(),
    [fields, setFields] = useState([]),
    [fc, setFc] = useState(null),
    [fv, setFv] = useState("");
  const load = () => api("/api/admin").then(setA);
  useEffect(() => {
    load().catch((e) => message.error(e.message));
  }, [space]);
  async function change(b) {
    try {
      const r = await api("/api/admin", "POST", b);
      setNewKey(r.token || "");
      await load();
      if (b.kind === "space") await reload();
      message.success("已保存");
      return true;
    } catch (e) {
      message.error(e.message);
      return false;
    }
  }
  const edit = (p) => {
    setPerson(p);
    setResource("");
    setDn(null);
    setTable(null);
    setSchema({});
    setLimits([]);
  };
  async function pickDataset(n) {
    setDn(n);
    setTable(null);
    const [s, l] = await Promise.all([
      api(base + "/datasets/" + esc(n)),
      api("/api/admin/limits/" + esc(space) + "/" + esc(n)),
    ]);
    setSchema(s.tables);
    setLimits(l);
  }
  function pickTable(t) {
    setTable(t);
    const v = limits.find(
      (x) => x.person === person.name && x.table_name === t,
    );
    setFields(v?.fields || schema[t] || []);
    setFc(v?.filter_col || null);
    setFv(v?.filter_value || "");
  }
  async function saveLimit(remove) {
    try {
      await api("/api/admin/limits/" + esc(space) + "/" + esc(dn), "POST", {
        person: person.name,
        table,
        fields,
        filter_col: fc || null,
        filter_value: fv,
        remove,
      });
      await pickDataset(dn);
      message.success("访问范围已更新");
    } catch (e) {
      message.error(e.message);
    }
  }
  if (!a) return null;
  const audit = (
    <Table
      rowKey="id"
      dataSource={a.audit}
      columns={[
        col("变更时间", "ts", stamp),
        col("操作人", "actor"),
        col("动作", "action"),
        col("变更内容", "details"),
      ]}
    />
  );
  if (page === "audit") return audit;
  const grants = a.grants.filter(
    (g) => g.space === space && g.person === person?.name,
  );
  const effective = new Map();
  for (const g of grants.filter((g) => !g.asset || g.asset === resource)) {
    for (const x of a.roles.find((r) => r.name === g.role)?.actions || [])
      effective.set(x, [...(effective.get(x) || []), g.role]);
  }
  function revokeHint(grant) {
    const other = grants.filter(
      (g) => g !== grant && (!g.asset || g.asset === grant.asset),
    );
    const kept = (
      a.roles.find((r) => r.name === grant.role)?.actions || []
    ).filter((x) =>
      other.some((g) =>
        a.roles.find((r) => r.name === g.role)?.actions.includes(x),
      ),
    );
    return kept.length
      ? "其他角色仍授予：" + kept.map((x) => names[x]).join("、")
      : "此范围内没有其他角色提供相同操作。";
  }
  return (
    <>
      <Space wrap>
        <Input.Search
          placeholder="搜索成员或账号"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Select
          value={type}
          onChange={setType}
          options={[
            { value: "account", label: "人员 / 服务账号" },
            { value: "space", label: "工作空间" },
            { value: "role", label: "自定义角色" },
          ]}
        />
        <Input
          placeholder="新名称"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        {type === "role" && (
          <Select
            mode="multiple"
            placeholder="允许的操作"
            style={{ minWidth: 180 }}
            value={actions}
            onChange={setActions}
            options={a.actions.map((value) => ({ value, label: names[value] }))}
          />
        )}
        <Button
          onClick={async () => {
            if (await change({ kind: type, name: label, actions }))
              setLabel("");
          }}
        >
          创建
        </Button>
      </Space>
      {newKey && (
        <Alert
          title="新密钥仅显示一次"
          description={<code>{newKey}</code>}
          closable
        />
      )}
      <Tabs
        items={[
          {
            key: "members",
            label: "成员与账号",
            children: (
              <Table
                rowKey="name"
                dataSource={a.people.filter((p) =>
                  (p.name + (p.display_name || "")).includes(q),
                )}
                columns={[
                  col("成员", "name", (v, r) => (
                    <Button type="link" onClick={() => edit(r)}>
                      {r.display_name || v}
                    </Button>
                  )),
                  col("身份来源", "origin"),
                  col("空间角色", "name", (v, r) =>
                    r.root
                      ? "平台管理员"
                      : [
                          ...new Set(
                            a.grants
                              .filter(
                                (g) => g.person === v && g.space === space,
                              )
                              .map((g) => g.role),
                          ),
                        ].join("、") || "尚未授权",
                  ),
                  col("状态", "enabled", (v) => (
                    <Tag color={v ? "green" : "default"}>
                      {v ? "正常" : "已停用"}
                    </Tag>
                  )),
                  col("操作", "name", (v, r) =>
                    r.enabled && !r.root ? (
                      <Popconfirm
                        title="停用此账号？"
                        onConfirm={() => change({ kind: "disable", name: v })}
                      >
                        <Button danger type="text">
                          停用
                        </Button>
                      </Popconfirm>
                    ) : null,
                  ),
                ]}
              />
            ),
          },
          {
            key: "roles",
            label: "角色",
            children: (
              <Table
                rowKey="name"
                dataSource={a.roles}
                columns={[
                  col("角色", "name"),
                  col("允许操作", "actions", (xs) =>
                    xs.map((x) => names[x]).join("、"),
                  ),
                ]}
              />
            ),
          },
          { key: "audit", label: "审计记录", children: audit },
        ]}
      />
      <Drawer
        open={!!person}
        onClose={() => setPerson(null)}
        title={(person?.display_name || person?.name || "") + "的有效权限"}
        size="large"
      >
        {person && (
          <>
            <p>所属空间　{space}</p>
            <Select
              style={{ width: "100%" }}
              value={resource}
              onChange={setResource}
              showSearch
              options={[
                { value: "", label: "整个空间" },
                ...options(assets.assets.map((x) => x.asset)),
                ...datasets.map((d) => ({
                  value: "@data/" + d.name,
                  label: "数据集 · " + d.name,
                })),
              ]}
            />
            <Table
              pagination={false}
              size="small"
              rowKey="action"
              dataSource={a.actions.map((action) => ({ action }))}
              columns={[
                col("操作", "action", (v) => names[v]),
                col("权限", "action", (v) =>
                  person.root || effective.has(v) ? "允许" : "未授予",
                ),
                col("来源", "action", (v) =>
                  person.root
                    ? "平台管理员"
                    : effective.get(v)?.join("、") || "—",
                ),
              ]}
            />
            {!person.root && (
              <>
                <h3>授权与撤销</h3>
                <Space>
                  <Select
                    value={role}
                    onChange={setRole}
                    options={options(a.roles.map((r) => r.name))}
                  />
                  <Button
                    type="primary"
                    onClick={() =>
                      change({
                        kind: "grant",
                        name: person.name,
                        space,
                        role,
                        asset: resource,
                      })
                    }
                  >
                    授予角色
                  </Button>
                </Space>
                {grants.map((g) => (
                  <p key={g.role + ":" + g.asset}>
                    {g.role} · {g.asset || "整个空间"}　
                    <Popconfirm
                      title="撤销这项授权？"
                      description={revokeHint(g)}
                      onConfirm={() =>
                        change({
                          kind: "revoke",
                          name: g.person,
                          space,
                          role: g.role,
                          asset: g.asset,
                        })
                      }
                    >
                      <Button type="link" danger>
                        撤销
                      </Button>
                    </Popconfirm>
                  </p>
                ))}
                <h3>分析字段与行范围</h3>
                <Space wrap>
                  <Select
                    placeholder="数据集"
                    value={dn}
                    options={options(datasets.map((d) => d.name))}
                    onChange={(n) =>
                      pickDataset(n).catch((e) => message.error(e.message))
                    }
                  />
                  <Select
                    placeholder="选择表"
                    value={table}
                    options={options(Object.keys(schema))}
                    onChange={pickTable}
                  />
                </Space>
                {table && (
                  <>
                    <p>允许查看的字段</p>
                    <Checkbox.Group
                      options={schema[table]}
                      value={fields}
                      onChange={setFields}
                    />
                    <p>行过滤（等于）</p>
                    <Space wrap>
                      <Select
                        allowClear
                        placeholder="无行过滤"
                        value={fc}
                        options={options(schema[table])}
                        onChange={setFc}
                      />
                      <Input
                        placeholder="字段值"
                        value={fv}
                        onChange={(e) => setFv(e.target.value)}
                      />
                      <Button onClick={() => saveLimit(false)}>保存限制</Button>
                      <Popconfirm
                        title="解除此表的行列限制？"
                        onConfirm={() => saveLimit(true)}
                      >
                        <Button>解除限制</Button>
                      </Popconfirm>
                    </Space>
                  </>
                )}
              </>
            )}
          </>
        )}
      </Drawer>
    </>
  );
}
