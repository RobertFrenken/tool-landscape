"""Export DuckDB tables to Parquet for frontend consumption."""

from __future__ import annotations

from pathlib import Path

import duckdb

DEFAULT_EXPORT_DIR = Path(__file__).resolve().parents[1] / "site" / "src" / "data"

# Queries that denormalize data for frontend use.
# Each produces a flat, join-free table suitable for DuckDB-WASM.
EXPORTS: dict[str, str] = {
    "tools": """
        SELECT
            tool_id, name, url, open_source, license, summary,
            CAST(maturity AS VARCHAR) AS maturity,
            CAST(governance AS VARCHAR) AS governance,
            CAST(hpc_compatible AS VARCHAR) AS hpc_compatible,
            CAST(collaboration_model AS VARCHAR) AS collaboration_model,
            CAST(migration_cost AS VARCHAR) AS migration_cost,
            CAST(lock_in_risk AS VARCHAR) AS lock_in_risk,
            CAST(community_momentum AS VARCHAR) AS community_momentum,
            CAST(documentation_quality AS VARCHAR) AS documentation_quality,
            CAST(resource_overhead AS VARCHAR) AS resource_overhead,
            CAST(interoperability AS VARCHAR) AS interoperability,
            CAST(capability_ceiling AS VARCHAR) AS capability_ceiling,
            CAST(migration_likelihood AS VARCHAR) AS migration_likelihood,
            python_native, offline_capable, saas_available,
            self_hosted_viable, composite_tool,
            array_to_string(categories, ',') AS categories,
            array_to_string(deployment_model, ',') AS deployment_model,
            array_to_string(language_ecosystem, ',') AS language_ecosystem,
            array_to_string(integration_targets, ',') AS integration_targets,
            array_to_string(pipeline_stages, ',') AS pipeline_stages,
            array_to_string(scale_profiles, ',') AS scale_profiles,
            array_to_string(used_by, ',') AS used_by,
            github_repo, pypi_package, npm_package
        FROM tools
        ORDER BY name
    """,
    "edges": """
        SELECT
            e.edge_id,
            s.name AS source,
            t.name AS target,
            CAST(e.relation AS VARCHAR) AS relation,
            e.weight,
            e.evidence
        FROM edges e
        JOIN tools s ON e.source_id = s.tool_id
        JOIN tools t ON e.target_id = t.tool_id
        ORDER BY e.relation, s.name
    """,
    "neighborhoods": """
        SELECT
            n.name AS neighborhood,
            n.description,
            t.name AS tool_name,
            nm.membership,
            nm.pinned
        FROM neighborhood_members nm
        JOIN neighborhoods n ON n.neighborhood_id = nm.neighborhood_id
        JOIN tools t ON t.tool_id = nm.tool_id
        ORDER BY n.name, t.name
    """,
    "projects": """
        SELECT
            p.name AS project,
            p.description,
            p.team_size_ceiling,
            p.env_primary,
            p.gpu_required,
            p.internet_on_compute,
            p.shared_filesystem,
            c.name AS capability,
            c.description AS capability_description,
            ct.name AS current_tool,
            c.ceiling_requirements,
            array_to_string(c.triggers, ',') AS triggers,
            c.notes
        FROM projects p
        JOIN capabilities c ON c.project_id = p.project_id
        LEFT JOIN tools ct ON ct.tool_id = c.current_tool_id
        ORDER BY p.name, c.name
    """,
}


def export_parquet(
    con: duckdb.DuckDBPyConnection,
    output_dir: Path | None = None,
) -> dict[str, int]:
    """Export all tables to Parquet files.

    Returns dict of {filename: row_count}.
    """
    out = output_dir or DEFAULT_EXPORT_DIR
    out.mkdir(parents=True, exist_ok=True)

    results: dict[str, int] = {}

    for name, query in EXPORTS.items():
        parquet_path = out / f"{name}.parquet"
        # Use DuckDB's native COPY for Parquet (no pyarrow needed)
        con.execute(f"COPY ({query}) TO '{parquet_path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        # Count rows for reporting
        count = con.execute(f"SELECT count(*) FROM '{parquet_path}'").fetchone()[0]
        results[name] = count

    return results
