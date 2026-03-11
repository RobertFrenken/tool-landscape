"""DDL: all CREATE TYPE / CREATE TABLE / CREATE INDEX statements."""

from __future__ import annotations

import duckdb

# fmt: off
DDL = """
-- ── Enum types ──────────────────────────────────────────────────────────────

CREATE TYPE IF NOT EXISTS maturity_level AS ENUM (
    'archived', 'experimental', 'early', 'growth', 'production'
);
CREATE TYPE IF NOT EXISTS momentum AS ENUM ('declining', 'stable', 'growing');
CREATE TYPE IF NOT EXISTS tier AS ENUM ('unknown', 'low', 'medium', 'high', 'extensive');
CREATE TYPE IF NOT EXISTS cost_level AS ENUM ('unknown', 'low', 'medium', 'high');
CREATE TYPE IF NOT EXISTS doc_quality AS ENUM ('unknown', 'poor', 'adequate', 'excellent');
CREATE TYPE IF NOT EXISTS overhead AS ENUM ('unknown', 'minimal', 'moderate', 'heavy');
CREATE TYPE IF NOT EXISTS hpc_compat AS ENUM ('unknown', 'cloud_only', 'adaptable', 'native');
CREATE TYPE IF NOT EXISTS collab_model AS ENUM (
    'unknown', 'single_user', 'shared_server', 'multi_tenant'
);
CREATE TYPE IF NOT EXISTS governance_type AS ENUM (
    'community', 'company_backed', 'foundation',
    'apache_foundation', 'cncf', 'linux_foundation'
);
CREATE TYPE IF NOT EXISTS metric_source AS ENUM (
    'hand_curated', 'github_api', 'pypi_stats', 'npm_stats', 'override'
);
CREATE TYPE IF NOT EXISTS edge_type AS ENUM (
    'requires', 'replaces', 'often_paired',
    'wraps', 'feeds_into', 'integrates_with'
);
CREATE TYPE IF NOT EXISTS neighborhood_origin AS ENUM ('computed', 'user_defined', 'hybrid');

-- ── Sequences ───────────────────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS tool_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS metric_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS edge_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS neighborhood_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS project_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS capability_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS fitness_id_seq START 1;
CREATE SEQUENCE IF NOT EXISTS migration_id_seq START 1;

-- ── Core tables ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tools (
    tool_id      INTEGER PRIMARY KEY DEFAULT (nextval('tool_id_seq')),
    name         VARCHAR NOT NULL UNIQUE,
    url          VARCHAR,
    open_source  BOOLEAN,
    license      VARCHAR,
    summary      VARCHAR,

    -- Enum assessments
    maturity            maturity_level,
    governance          governance_type,
    hpc_compatible      hpc_compat,
    collaboration_model collab_model,
    migration_cost      cost_level,
    lock_in_risk        cost_level,
    community_momentum  momentum,
    documentation_quality doc_quality,
    resource_overhead   overhead,
    interoperability    tier,
    capability_ceiling  tier,
    migration_likelihood cost_level,

    -- Boolean flags
    python_native       BOOLEAN DEFAULT false,
    offline_capable     BOOLEAN DEFAULT false,
    saas_available      BOOLEAN DEFAULT false,
    self_hosted_viable  BOOLEAN DEFAULT false,
    composite_tool      BOOLEAN DEFAULT false,

    -- Array fields (DuckDB native LIST)
    categories          VARCHAR[] DEFAULT [],
    deployment_model    VARCHAR[] DEFAULT [],
    language_ecosystem  VARCHAR[] DEFAULT [],
    integration_targets VARCHAR[] DEFAULT [],
    pipeline_stages     VARCHAR[] DEFAULT [],
    scale_profiles      VARCHAR[] DEFAULT [],
    used_by             VARCHAR[] DEFAULT [],

    -- Metadata
    created_at   TIMESTAMP DEFAULT current_timestamp,
    updated_at   TIMESTAMP DEFAULT current_timestamp
);

-- ── Metrics (time-series, EAV) ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tool_metrics (
    metric_id   INTEGER PRIMARY KEY DEFAULT (nextval('metric_id_seq')),
    tool_id     INTEGER NOT NULL REFERENCES tools(tool_id),
    metric_name VARCHAR NOT NULL,
    value       DOUBLE NOT NULL,
    source      metric_source NOT NULL,
    measured_at TIMESTAMP NOT NULL,
    metadata    JSON,
    created_at  TIMESTAMP DEFAULT current_timestamp,

    UNIQUE (tool_id, metric_name, source, measured_at)
);

-- ── Graph edges ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS edges (
    edge_id     INTEGER PRIMARY KEY DEFAULT (nextval('edge_id_seq')),
    source_id   INTEGER NOT NULL REFERENCES tools(tool_id),
    target_id   INTEGER NOT NULL REFERENCES tools(tool_id),
    relation    edge_type NOT NULL,
    weight      DOUBLE DEFAULT 1.0,
    source_info metric_source NOT NULL DEFAULT 'hand_curated',
    evidence    VARCHAR,
    created_at  TIMESTAMP DEFAULT current_timestamp,

    CHECK (source_id != target_id),
    UNIQUE (source_id, target_id, relation)
);

-- ── Neighborhoods ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS neighborhoods (
    neighborhood_id INTEGER PRIMARY KEY DEFAULT (nextval('neighborhood_id_seq')),
    name            VARCHAR NOT NULL UNIQUE,
    description     VARCHAR,
    origin          neighborhood_origin NOT NULL DEFAULT 'computed',
    algorithm       VARCHAR,
    parameters      JSON,
    computed_at     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS neighborhood_members (
    neighborhood_id INTEGER NOT NULL REFERENCES neighborhoods(neighborhood_id),
    tool_id         INTEGER NOT NULL REFERENCES tools(tool_id),
    membership      DOUBLE DEFAULT 1.0,
    pinned          BOOLEAN DEFAULT false,
    PRIMARY KEY (neighborhood_id, tool_id)
);

-- ── Projects ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS projects (
    project_id       INTEGER PRIMARY KEY DEFAULT (nextval('project_id_seq')),
    name             VARCHAR NOT NULL UNIQUE,
    description      VARCHAR,
    team_size_ceiling INTEGER,
    env_primary      VARCHAR,
    env_secondary    VARCHAR[] DEFAULT [],
    gpu_required     BOOLEAN DEFAULT false,
    internet_on_compute BOOLEAN DEFAULT true,
    shared_filesystem   VARCHAR,
    created_at       TIMESTAMP DEFAULT current_timestamp,
    updated_at       TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS capabilities (
    capability_id   INTEGER PRIMARY KEY DEFAULT (nextval('capability_id_seq')),
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    name            VARCHAR NOT NULL,
    description     VARCHAR,
    current_tool_id INTEGER REFERENCES tools(tool_id),
    floor_requirements   JSON NOT NULL DEFAULT '{}',
    ceiling_requirements JSON NOT NULL DEFAULT '{}',
    triggers        VARCHAR[] DEFAULT [],
    notes           VARCHAR,
    UNIQUE (project_id, name),
    created_at      TIMESTAMP DEFAULT current_timestamp,
    updated_at      TIMESTAMP DEFAULT current_timestamp
);

-- ── Fitness ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fitness (
    fitness_id      INTEGER PRIMARY KEY DEFAULT (nextval('fitness_id_seq')),
    tool_id         INTEGER NOT NULL REFERENCES tools(tool_id),
    capability_id   INTEGER NOT NULL REFERENCES capabilities(capability_id),
    floor_coverage  DOUBLE,
    ceiling_coverage DOUBLE,
    overall_fitness  DOUBLE,
    method          VARCHAR NOT NULL DEFAULT 'manual',
    reasoning       VARCHAR,
    assessed_at     TIMESTAMP NOT NULL DEFAULT current_timestamp,
    UNIQUE (tool_id, capability_id, assessed_at)
);

-- ── Migration history ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS migration_history (
    migration_id    INTEGER PRIMARY KEY DEFAULT (nextval('migration_id_seq')),
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    capability_name VARCHAR NOT NULL,
    sequence_num    INTEGER NOT NULL,
    from_tool_id    INTEGER REFERENCES tools(tool_id),
    to_tool_id      INTEGER REFERENCES tools(tool_id),
    migration_date  DATE,
    commits         VARCHAR[] DEFAULT [],
    what_happened   VARCHAR,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

-- ── Indexes ─────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_tools_momentum ON tools (community_momentum);
CREATE INDEX IF NOT EXISTS idx_tools_ceiling ON tools (capability_ceiling);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges (relation);
CREATE INDEX IF NOT EXISTS idx_metrics_tool ON tool_metrics (tool_id, metric_name);
CREATE INDEX IF NOT EXISTS idx_metrics_measured ON tool_metrics (measured_at);
CREATE INDEX IF NOT EXISTS idx_fitness_tool ON fitness (tool_id);
CREATE INDEX IF NOT EXISTS idx_fitness_cap ON fitness (capability_id);
CREATE INDEX IF NOT EXISTS idx_nbr_members_tool ON neighborhood_members (tool_id);
"""
# fmt: on


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Execute all DDL statements to create the schema."""
    for statement in DDL.split(";"):
        stmt = statement.strip()
        if stmt:
            con.execute(stmt)
