import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  App,
  Avatar,
  Button,
  ConfigProvider,
  Dropdown,
  Empty,
  Input,
  Menu,
  Select,
  Spin,
} from "antd";
import zhCN from "antd/locale/zh_CN";
import {
  BellOutlined,
  BookOutlined,
  CloudUploadOutlined,
  CodeOutlined,
  TeamOutlined,
  AuditOutlined,
  MessageOutlined,
  MenuOutlined,
  LogoutOutlined,
  LockOutlined,
  ArrowRightOutlined,
} from "@ant-design/icons";
import { api, esc, options } from "./api";
import { Chat } from "./Chat";
import { Imports, Assets, Monitor } from "./Console";
import { Members } from "./Members";
import { SqlTemplates } from "./SqlTemplates";
import "./style.css";
export function Mark() {
  return (
    <svg
      aria-hidden="true"
      width="31"
      height="31"
      viewBox="0 0 32 32"
      fill="none"
    >
      <path
        d="m3 7 8-4 10 4 8-4v22l-8 4-10-4-8 4V7Zm8-4v22m10-18v22"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function Brand() {
  return (
    <span className="brand">
      <Mark />
      QueryAtlas
    </span>
  );
}
function Login({ refresh }) {
  const [other, setOther] = useState(false),
    [key, setKey] = useState(""),
    [ready, setReady] = useState(null),
    [error, setError] = useState(
      new URLSearchParams(location.search).has("login_error")
        ? "飞书认证未完成，请重试。"
        : "",
    );
  useEffect(() => {
    api("/api/auth/options")
      .then(setReady)
      .catch((e) => setError(e.message));
  }, []);
  return (
    <>
      <header className="topbar">
        <Brand />
      </header>
      <div className="login">
        <div className="login-map" aria-hidden="true">
          <div className="atlas-grid" />
          <div className="map-pins"><i /><span><Mark /></span><i /></div>
          <div className="map-label"><BookOutlined /> 知识库 <span /> <CodeOutlined /> 数据集</div>
        </div>
        <section>
          <h1>登录 QueryAtlas</h1>
          <p>
            {ready?.feishu
              ? "使用企业飞书账号进入工作空间。"
              : "输入访问密钥，进入你的工作空间。"}
          </p>
          {ready?.feishu && (
            <>
              <Button
                size="large"
                type="primary"
                disabled={!ready?.feishu}
                block
                onClick={() => location.assign("/auth/feishu/start")}
              >
                使用飞书登录 <ArrowRightOutlined />
              </Button>
              <p className="muted">
                <LockOutlined /> 由飞书验证身份，空间权限由管理员分配。
              </p>
              <Button type="link" onClick={() => setOther(!other)}>
                其他登录方式
              </Button>
            </>
          )}
          {(!ready?.feishu || other) && (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  await api("/api/login", "POST", { token: key });
                  await refresh();
                } catch (e) {
                  setError(e.message);
                }
              }}
            >
              <Input.Password
                aria-label="访问密钥"
                placeholder="访问密钥"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
              <Button type="primary" htmlType="submit">
                登录
              </Button>
            </form>
          )}
          {error && <p role="alert">{error}</p>}
        </section>
      </div>
    </>
  );
}
const pages = [
  ["assets", "知识资产", BookOutlined],
  ["imports", "数据导入", CloudUploadOutlined],
  ["templates", "SQL 模板", CodeOutlined],
  ["members", "成员与权限", TeamOutlined],
  ["monitor", "监控告警", BellOutlined],
  ["audit", "审计记录", AuditOutlined],
];
function Workspace({ me, reload }) {
  const [space, changeSpace] = useState(() =>
      me.spaces.includes(localStorage.getItem("qa-space"))
        ? localStorage.getItem("qa-space")
        : me.spaces[0],
    ),
    [assets, setAssets] = useState(null),
    [page, setPage] = useState("chat"),
    [mobile, setMobile] = useState(false),
    [datasets, setDatasets] = useState([]),
    [refresh, nudge] = useState(0);
  const { message } = App.useApp();
  const base = space ? "/api/spaces/" + esc(space) : "";
  useEffect(() => {
    if (!base) return;
    let active = true;
    Promise.all([api(base + "/assets"), api(base + "/datasets")])
      .then(([a, d]) => {
        if (active) {
          setAssets(a);
          setDatasets(d);
        }
      })
      .catch((e) => message.error(e.message));
    return () => {
      active = false;
    };
  }, [base, refresh]);
  const perms = assets?.actions || [];
  const visiblePages = {
    assets: true,
    imports: perms.some((a) => ["ingest", "jobs", "publish", "mysql_view"].includes(a)),
    templates: perms.includes("templates"),
    members: me.root,
    monitor: perms.includes("monitor"),
    audit: me.root,
  };
  const allowed = pages.filter(([id]) => visiblePages[id]);
  const move = (p) => {
    setPage(p);
    setMobile(false);
  };
  return (
    <>
      <header className="topbar">
        <Button
          className="hamb"
          type="text"
          icon={<MenuOutlined />}
          aria-label="导航"
          aria-expanded={mobile}
          onClick={() => setMobile(!mobile)}
        />
        <Brand />
        {space && (
          <Select
            aria-label="工作空间"
            variant="borderless"
            value={space}
            options={options(me.spaces)}
            onChange={(v) => {
              setPage("chat");
              setAssets(null);
              setDatasets([]);
              changeSpace(v);
              localStorage.setItem("qa-space", v);
            }}
          />
        )}
        <span className="header-view">{page === "chat" ? "问答工作台" : "数据控制台"}</span>
        <div className="fill" />
        {perms.includes("monitor") && (
          <Button
            type="text"
            aria-label="监控告警"
            icon={<BellOutlined />}
            onClick={() => move("monitor")}
          />
        )}
        <Dropdown
          menu={{
            items: [
              { key: "logout", label: "退出登录", icon: <LogoutOutlined /> },
            ],
            onClick: async () => {
              await api("/api/logout", "POST");
              await reload();
            },
          }}
        >
          <Button type="text">
            <Avatar size={28}>
              {(me.display_name || me.name).slice(0, 1)}
            </Avatar>
            <span className="fullName">{me.display_name || me.name}</span>
          </Button>
        </Dropdown>
      </header>
      {!space ? (
        <Empty description="尚未分配工作空间，请联系管理员为此账号授权。" />
      ) : !assets ? (
        <Spin fullscreen />
      ) : (
        <>
          <div className="qa-view" hidden={page !== "chat"}>
            <Chat key={space} {...{ base, assets, datasets, move, mobile, allowed }} />
          </div>
          {page !== "chat" && (
            <div className={"layout " + (mobile ? "mobile-open" : "")}>
              <aside aria-label="控制台导航">
                <Button
                  type="text"
                  className="nav-return"
                  icon={<MessageOutlined />}
                  onClick={() => move("chat")}
                >
                  返回对话
                </Button>
                <Menu
                  selectedKeys={[page]}
                  items={allowed.map(([key, label, Icon]) => ({ key, label, icon: <Icon /> }))}
                  onClick={({ key }) => move(key)}
                />
              </aside>
              <main className="console">
                <div className="console-inner">
                  <div className="console-heading">
                    <h1>{pages.find((p) => p[0] === page)?.[1]}</h1>
                  </div>
                  {page === "assets" && <Assets {...{ assets, datasets, base }} />}
                  {page === "imports" && (
                    <Imports {...{ base, perms }} refresh={() => nudge((n) => n + 1)} />
                  )}
                  {["members", "audit"].includes(page) && (
                    <Members key={page} {...{ space, page, base, assets, datasets }} reload={reload} />
                  )}
                  {page === "monitor" && <Monitor {...{ base, perms }} />}
                  {page === "templates" && <SqlTemplates key={space} base={base} />}
                </div>
              </main>
            </div>
          )}
        </>
      )}
    </>
  );
}
function Shell() {
  const [me, setMe] = useState(undefined);
  async function reload() {
    try {
      setMe(await api("/api/me"));
    } catch {
      setMe(null);
    }
  }
  useEffect(() => {
    reload();
    const expire = () => setMe(null);
    window.addEventListener("qa:expired", expire);
    return () => window.removeEventListener("qa:expired", expire);
  }, []);
  return me === undefined ? (
    <Spin fullscreen />
  ) : me ? (
    <Workspace key={me.name} me={me} reload={reload} />
  ) : (
    <Login refresh={reload} />
  );
}
createRoot(document.getElementById("root")).render(
  <ConfigProvider
    locale={zhCN}
    theme={{
      token: {
        colorPrimary: "#5145c6",
        colorInfo: "#5145c6",
        colorText: "#202238",
        colorTextSecondary: "#60677b",
        colorTextPlaceholder: "#70778a",
        colorBorder: "#dbdfea",
        colorBorderSecondary: "#e7e9f1",
        borderRadius: 9,
        fontFamily: 'system-ui, "PingFang SC", "Microsoft YaHei", sans-serif',
      },
      components: {
        Menu: { itemBg: "transparent", itemSelectedBg: "#ebe9fb", itemHeight: 40 },
        Table: { headerBg: "#f6f7fb", headerColor: "#515870", borderColor: "#e7e9f1" },
        Card: { headerBg: "#fcfcfe" },
      },
    }}
  >
    <App>
      <Shell />
    </App>
  </ConfigProvider>,
);
