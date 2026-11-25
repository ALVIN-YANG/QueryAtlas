CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS spaces(name text PRIMARY KEY);
CREATE TABLE IF NOT EXISTS people(name text PRIMARY KEY, digest text UNIQUE NOT NULL, root boolean NOT NULL DEFAULT false, enabled boolean NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS roles(name text PRIMARY KEY, actions text[] NOT NULL);
CREATE TABLE IF NOT EXISTS grants_(person text REFERENCES people, space text REFERENCES spaces, role text REFERENCES roles, asset text NOT NULL DEFAULT '', PRIMARY KEY(person,space,role,asset));
CREATE TABLE IF NOT EXISTS jobs(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, space text NOT NULL REFERENCES spaces, asset text NOT NULL, hash text NOT NULL, actor text REFERENCES people, status text NOT NULL DEFAULT 'queued', note text NOT NULL DEFAULT '', created timestamptz NOT NULL DEFAULT now(), finished timestamptz);
CREATE TABLE IF NOT EXISTS docs(space text REFERENCES spaces, asset text, version bigint REFERENCES jobs, PRIMARY KEY(space,asset));
CREATE TABLE IF NOT EXISTS nodes(version bigint REFERENCES jobs, seq integer, anchor text NOT NULL, method text NOT NULL, body text NOT NULL, embedding vector(512) NOT NULL, PRIMARY KEY(version,seq));
CREATE TABLE IF NOT EXISTS audit(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, ts timestamptz DEFAULT now(), actor text NOT NULL, action text NOT NULL, details text NOT NULL);
CREATE TABLE IF NOT EXISTS heartbeat(name text PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now());
INSERT INTO spaces VALUES ('demo') ON CONFLICT DO NOTHING;
INSERT INTO roles VALUES ('阅读者',ARRAY['search']),('原件查看者',ARRAY['original']),('资料管理员',ARRAY['ingest','search','original','jobs']),('值班员',ARRAY['monitor','ack','jobs']) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS external_users(person text PRIMARY KEY REFERENCES people, tenant text NOT NULL, open_id text NOT NULL, display_name text NOT NULL, UNIQUE(tenant,open_id));
CREATE TABLE IF NOT EXISTS sessions(digest text PRIMARY KEY, person text REFERENCES people NOT NULL, expires timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_states(digest text PRIMARY KEY, expires timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS datasets(space text REFERENCES spaces, name text, physical text NOT NULL, description text NOT NULL, tables jsonb NOT NULL, PRIMARY KEY(space,name));
CREATE TABLE IF NOT EXISTS data_candidates(id uuid PRIMARY KEY, space text REFERENCES spaces, name text NOT NULL, owner_name text REFERENCES people, raw bytea NOT NULL, mapping jsonb, physical text, status text NOT NULL DEFAULT 'draft', created timestamptz DEFAULT now());
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS batch text;
UPDATE datasets d SET batch=c.id::text FROM data_candidates c
WHERE d.batch IS NULL AND d.physical='qa_imports' AND c.status='published'
  AND (c.space,c.name,c.physical)=(d.space,d.name,d.description);
CREATE TABLE IF NOT EXISTS data_limits(person text REFERENCES people,space text,name text,table_name text,fields text[] NOT NULL,filter_col text,filter_value text,PRIMARY KEY(person,space,name,table_name),FOREIGN KEY(space,name) REFERENCES datasets);
CREATE TABLE IF NOT EXISTS chats(id uuid PRIMARY KEY, person text REFERENCES people, space text REFERENCES spaces, title text NOT NULL, updated timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS turns(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, chat uuid REFERENCES chats ON DELETE CASCADE, question text NOT NULL, answer jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS chat_files(id uuid PRIMARY KEY, chat uuid REFERENCES chats ON DELETE CASCADE, name text NOT NULL, passages jsonb NOT NULL);
INSERT INTO roles VALUES ('分析阅读者',ARRAY['analysis']),('分析明细查看者',ARRAY['detail','sql']),('数据发布者',ARRAY['publish']) ON CONFLICT DO NOTHING;
ALTER TABLE turns ADD COLUMN IF NOT EXISTS request_id uuid;
CREATE UNIQUE INDEX IF NOT EXISTS turns_client_request ON turns(chat,request_id);
CREATE TABLE IF NOT EXISTS cancelled_turns(chat uuid REFERENCES chats ON DELETE CASCADE, request_id uuid, ts timestamptz DEFAULT now(), PRIMARY KEY(chat,request_id));
CREATE TABLE IF NOT EXISTS benchmark_packs(name text PRIMARY KEY, counts jsonb NOT NULL, queries jsonb NOT NULL, timings jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS job_progress(job bigint PRIMARY KEY REFERENCES jobs, stage text NOT NULL);
INSERT INTO roles VALUES ('空间试用者',ARRAY['search','analysis','detail','sql']) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS sql_templates (
    space text NOT NULL,
    id text NOT NULL,
    dataset text NOT NULL,
    sql text NOT NULL,
    enabled boolean NOT NULL DEFAULT false,
    revision bigint NOT NULL DEFAULT 1,
    updated timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (space, id),
    UNIQUE (space, id, dataset),
    FOREIGN KEY (space, dataset) REFERENCES datasets(space, name)
);
ALTER TABLE sql_templates ADD COLUMN IF NOT EXISTS parameters jsonb NOT NULL DEFAULT '{}';
ALTER TABLE sql_templates ADD COLUMN IF NOT EXISTS definition text NOT NULL DEFAULT '';
ALTER TABLE sql_templates ADD COLUMN IF NOT EXISTS rule_clauses jsonb NOT NULL DEFAULT '[]';
CREATE TABLE IF NOT EXISTS sql_template_aliases (
    space text NOT NULL,
    template_id text NOT NULL,
    dataset text NOT NULL,
    normalized text NOT NULL,
    alias text NOT NULL,
    position integer NOT NULL,
    PRIMARY KEY (space, dataset, normalized),
    FOREIGN KEY (space, template_id, dataset) REFERENCES sql_templates(space, id, dataset) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS sql_template_alias_lookup ON sql_template_aliases(space, normalized);
INSERT INTO roles VALUES ('SQL 模板管理员',ARRAY['templates']) ON CONFLICT DO NOTHING;

-- Keep vectors generated by different models in separate indexes so cutover does not rewrite active documents.
ALTER TABLE nodes ALTER COLUMN embedding DROP NOT NULL;
CREATE TABLE IF NOT EXISTS node_embeddings (
    version bigint NOT NULL,
    seq integer NOT NULL,
    model text NOT NULL,
    embedding vector(512) NOT NULL,
    PRIMARY KEY(version, seq, model),
    FOREIGN KEY(version, seq) REFERENCES nodes(version, seq) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS mysql_sync_checkpoints(
    space text NOT NULL,
    name text NOT NULL,
    fingerprint text NOT NULL,
    watermark bigint NOT NULL,
    physical text NOT NULL,
    PRIMARY KEY(space,name),
    FOREIGN KEY(space,name) REFERENCES datasets ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS mysql_sources (
    space text NOT NULL REFERENCES spaces,
    dataset text NOT NULL,
    profile text NOT NULL,
    description text NOT NULL,
    tables jsonb NOT NULL,
    PRIMARY KEY (space,dataset)
);
CREATE TABLE IF NOT EXISTS mysql_sync_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    space text NOT NULL,
    dataset text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('full','incremental')),
    actor text NOT NULL REFERENCES people,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','published','unchanged','failed')),
    created timestamptz NOT NULL DEFAULT now(),
    started timestamptz,
    finished timestamptz,
    error text,
    result jsonb,
    FOREIGN KEY (space,dataset) REFERENCES mysql_sources
);
CREATE UNIQUE INDEX IF NOT EXISTS mysql_sync_one_active
    ON mysql_sync_runs(space,dataset) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS mysql_sync_workspace_history ON mysql_sync_runs(space,id DESC);
INSERT INTO roles VALUES
    ('同步查看者',ARRAY['mysql_view']),
    ('同步操作员',ARRAY['mysql_view','mysql_run']),
    ('同步管理员',ARRAY['mysql_view','mysql_manage','mysql_run'])
ON CONFLICT DO NOTHING;
