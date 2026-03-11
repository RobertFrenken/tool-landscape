"""Migrate seed JSON data into DuckDB."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from landscape.db.schema import create_schema

SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"

# Maps for normalizing JSON values to DuckDB enum values
MATURITY_MAP = {
    "experimental": "experimental",
    "early": "early",
    "growth": "growth",
    "production": "production",
    "archived": "archived",
}
GOVERNANCE_MAP = {
    "community": "community",
    "company_backed": "company_backed",
    "foundation": "foundation",
    "apache_foundation": "apache_foundation",
    "cncf": "cncf",
    "linux_foundation": "linux_foundation",
}


def _safe_enum(value: str, valid: dict[str, str], default: str | None = None) -> str | None:
    """Map a JSON value to a valid DuckDB enum value."""
    if not value:
        return default
    return valid.get(value, default)


def _null_if_unknown(value: str | None) -> str | None:
    """Convert 'unknown' and empty strings to None for DuckDB enum columns."""
    if not value or value == "unknown":
        return None
    return value


def migrate_tools(con: duckdb.DuckDBPyConnection, catalog_path: Path | None = None) -> int:
    """Migrate tools from JSON catalog into the tools table."""
    path = catalog_path or SEED_DIR / "mlops_tools_catalog.json"
    tools = json.loads(path.read_text())
    now = datetime.now(UTC)
    count = 0

    for t in tools:
        # Normalize scale_profile: string -> list
        sp = t.get("scale_profile", "")
        scale_profiles = [sp] if isinstance(sp, str) and sp else sp if isinstance(sp, list) else []

        con.execute(
            """
            INSERT INTO tools (
                name, url, open_source, license, summary,
                maturity, governance,
                hpc_compatible, collaboration_model, migration_cost, lock_in_risk,
                community_momentum, documentation_quality, resource_overhead,
                interoperability, capability_ceiling, migration_likelihood,
                python_native, offline_capable, saas_available, self_hosted_viable,
                composite_tool,
                categories, deployment_model, language_ecosystem,
                integration_targets, pipeline_stages, scale_profiles, used_by,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6::maturity_level, $7::governance_type,
                $8::hpc_compat, $9::collab_model, $10::cost_level, $11::cost_level,
                $12::momentum, $13::doc_quality, $14::overhead,
                $15::tier, $16::tier, $17::cost_level,
                $18, $19, $20, $21,
                $22,
                $23, $24, $25,
                $26, $27, $28, $29,
                $30, $31
            )
            ON CONFLICT (name) DO NOTHING
            """,
            [
                t["name"],
                t.get("url", ""),
                t.get("open_source", False),
                t.get("license", ""),
                t.get("summary", ""),
                # Enums
                _safe_enum(t.get("maturity", ""), MATURITY_MAP),
                _safe_enum(t.get("governance", ""), GOVERNANCE_MAP),
                _null_if_unknown(t.get("hpc_compatible")),
                _null_if_unknown(t.get("collaboration_model")),
                _null_if_unknown(t.get("migration_cost")),
                _null_if_unknown(t.get("lock_in_risk")),
                _null_if_unknown(t.get("community_momentum")),
                _null_if_unknown(t.get("documentation_quality")),
                _null_if_unknown(t.get("resource_overhead")),
                _null_if_unknown(t.get("interoperability")),
                _null_if_unknown(t.get("capability_ceiling")),
                _null_if_unknown(t.get("migration_likelihood")),
                # Booleans
                t.get("python_native", False),
                t.get("offline_capable", False),
                t.get("saas_available", False),
                t.get("self_hosted_viable", False),
                t.get("composite_tool", False),
                # Arrays
                t.get("categories", []),
                t.get("deployment_model", []),
                t.get("language_ecosystem", []),
                t.get("integration_targets", []),
                t.get("pipeline_stage", []),
                scale_profiles,
                t.get("used_by", []),
                # Timestamps
                now,
                now,
            ],
        )
        count += 1

    return count


def migrate_projects(con: duckdb.DuckDBPyConnection, ceilings_path: Path | None = None) -> int:
    """Migrate projects and capabilities from ceilings JSON."""
    path = ceilings_path or SEED_DIR / "project_ceilings.json"
    data = json.loads(path.read_text())
    now = datetime.now(UTC)
    count = 0

    for proj_name, proj in data.get("projects", {}).items():
        env = proj.get("environment", {})
        con.execute(
            """
            INSERT INTO projects (
                name, description, team_size_ceiling,
                env_primary, env_secondary, gpu_required,
                internet_on_compute, shared_filesystem,
                created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (name) DO NOTHING
            """,
            [
                proj_name,
                proj.get("description", ""),
                proj.get("team_size_ceiling"),
                env.get("primary"),
                env.get("secondary", []),
                env.get("gpu_required", False),
                env.get("internet_on_compute", True),
                env.get("shared_filesystem"),
                now,
                now,
            ],
        )

        # Get project_id
        project_id = con.execute(
            "SELECT project_id FROM projects WHERE name = $1", [proj_name]
        ).fetchone()[0]

        # Migrate capabilities
        for cap_name, cap in proj.get("capability_ceilings", {}).items():
            # Look up current tool
            current_tool = cap.get("current_tool", "")
            tool_id = None
            if current_tool:
                # Try exact match first, then first-word match
                row = con.execute(
                    "SELECT tool_id FROM tools WHERE name = $1", [current_tool]
                ).fetchone()
                if not row:
                    first_word = current_tool.split("(")[0].split("+")[0].strip()
                    row = con.execute(
                        "SELECT tool_id FROM tools WHERE name = $1", [first_word]
                    ).fetchone()
                if row:
                    tool_id = row[0]

            con.execute(
                """
                INSERT INTO capabilities (
                    project_id, name, description, current_tool_id,
                    floor_requirements, ceiling_requirements,
                    triggers, notes, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5::JSON, $6::JSON, $7, $8, $9, $10)
                ON CONFLICT (project_id, name) DO NOTHING
                """,
                [
                    project_id,
                    cap_name,
                    cap.get("description", ""),
                    tool_id,
                    json.dumps({}),  # Floor not yet defined
                    json.dumps(cap.get("requirements", {})),
                    cap.get("trigger_to_reevaluate", []),
                    cap.get("notes"),
                    now,
                    now,
                ],
            )
            count += 1

    return count


def derive_edges(con: duckdb.DuckDBPyConnection) -> int:
    """Derive integrates_with edges from integration_targets."""
    count = 0
    tools = con.execute(
        "SELECT tool_id, name, integration_targets FROM tools WHERE len(integration_targets) > 0"
    ).fetchall()

    tool_name_to_id: dict[str, int] = {}
    for row in con.execute("SELECT tool_id, name FROM tools").fetchall():
        tool_name_to_id[row[1].lower()] = row[0]

    for tool_id, _name, targets in tools:
        for target in targets:
            target_id = tool_name_to_id.get(target.lower())
            if target_id and target_id != tool_id:
                try:
                    con.execute(
                        """
                        INSERT INTO edges (source_id, target_id, relation, source_info, evidence)
                        VALUES ($1, $2, 'integrates_with', 'hand_curated',
                            'Derived from integration_targets field')
                        """,
                        [tool_id, target_id],
                    )
                    count += 1
                except duckdb.ConstraintException:
                    pass  # Duplicate edge

    return count


def load_curated_edges(
    con: duckdb.DuckDBPyConnection,
    edges_path: Path | None = None,
) -> int:
    """Load hand-curated edges from JSON into the edges table.

    Reads curated_edges.json, resolves tool names to IDs, and inserts edges
    with source_info='hand_curated'. Skips edges where either tool name is
    not found in the database or the edge already exists.
    """
    path = edges_path or SEED_DIR / "curated_edges.json"
    if not path.exists():
        return 0

    edges = json.loads(path.read_text())

    # Build name→id lookup
    tool_name_to_id: dict[str, int] = {}
    for row in con.execute("SELECT tool_id, name FROM tools").fetchall():
        tool_name_to_id[row[1]] = row[0]

    count = 0
    for edge in edges:
        source_id = tool_name_to_id.get(edge["source"])
        target_id = tool_name_to_id.get(edge["target"])

        if not source_id or not target_id:
            continue

        try:
            con.execute(
                """
                INSERT INTO edges
                    (source_id, target_id, relation, source_info, evidence)
                VALUES ($1, $2, $3::edge_type, 'hand_curated', $4)
                """,
                [source_id, target_id, edge["relation"], edge.get("evidence", "")],
            )
            count += 1
        except duckdb.ConstraintException:
            pass  # Duplicate edge

    return count


def backfill_identifiers(con: duckdb.DuckDBPyConnection) -> int:
    """Backfill github_repo/pypi_package/npm_package from resolved_identifiers.json."""
    ids_path = SEED_DIR / ".." / "resolved_identifiers.json"
    if not ids_path.exists():
        return 0

    identifiers = json.loads(ids_path.read_text())
    count = 0
    for name, ids in identifiers.items():
        gh = ids.get("github_repo")
        pypi = ids.get("pypi_package")
        npm = ids.get("npm_package")
        if gh or pypi or npm:
            con.execute(
                """
                UPDATE tools
                SET github_repo = COALESCE($2, github_repo),
                    pypi_package = COALESCE($3, pypi_package),
                    npm_package = COALESCE($4, npm_package),
                    updated_at = current_timestamp
                WHERE name = $1
                """,
                [name, gh, pypi, npm],
            )
            count += 1
    return count


def run_migration(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Run the full migration pipeline."""
    create_schema(con)

    results = {
        "tools": migrate_tools(con),
        "projects": migrate_projects(con),
        "edges_derived": derive_edges(con),
        "edges_curated": load_curated_edges(con),
        "identifiers": backfill_identifiers(con),
    }

    return results
