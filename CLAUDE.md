# tool-landscape: Developer Tool Evaluation Framework

Map project goals (floor/ceiling) to stable tool neighborhoods. Designed for small HPC research labs that want to stop churning through tech stacks.

## Key Commands

```bash
# Rebuild database from seed JSON
bash scripts/rebuild_db.sh

# CLI
landscape import --seed                     # Migrate seed JSON → DuckDB
landscape stats                             # Row counts, DB size
landscape query --category orchestrator     # Filter tools
landscape query --used-by KD-GAT            # Tools in a project's stack
landscape query --ceiling extensive --momentum growing  # High-ceiling growing tools
landscape inspect <tool-name>               # Full detail + edges + neighborhoods
landscape coverage <project-name>           # Capability coverage report

# Development
uv pip install -e ".[dev]" --python .venv/bin/python
.venv/bin/pytest
```

## Architecture

### Database (DuckDB)

9 tables in `data/landscape.duckdb` (rebuilt from `data/seed/`):

| Table | Role | Rows |
|-------|------|------|
| `tools` | Tool catalog: identity, enums, arrays, booleans | 1157 |
| `edges` | Typed directed relationships between tools | 1445 |
| `tool_metrics` | Time-series metrics with source tracking (EAV) | 0 (Phase 2) |
| `neighborhoods` | Computed or user-defined tool clusters | 0 (Phase 3) |
| `neighborhood_members` | Tool membership in neighborhoods (soft, pinnable) | 0 (Phase 3) |
| `projects` | Project definitions with environment constraints | 2 |
| `capabilities` | Per-project floor/ceiling requirements | 16 |
| `fitness` | Tool × capability fitness scores | 0 (Phase 2) |
| `migration_history` | Past tool switches (from git history) | 0 (Phase 1b) |

### Package Structure

```
landscape/
  db/
    schema.py       # DDL: all CREATE TYPE / TABLE / INDEX
    connection.py   # DuckDB connection manager
    migrate.py      # JSON → DuckDB migration
  models/
    types.py        # Pydantic v2 models (Phase 2)
  cli/
    main.py         # Entry point + all subcommands
  analysis/
    fitness.py      # Floor/ceiling scoring (Phase 2)
    neighborhoods.py # Graph clustering (Phase 3)
    metrics.py      # GitHub/PyPI metric collection (Phase 2)
```

### Layer Rules

1. `landscape/db/` — schema + connection + migration. No analysis logic.
2. `landscape/analysis/` — reads from DB, writes scores/clusters back. No CLI concerns.
3. `landscape/cli/` — user-facing. Imports from db/ and analysis/.

### Edge Types

| Type | Meaning | Example |
|------|---------|---------|
| `requires` | Hard dependency | Ray Tune → Ray |
| `replaces` | Direct alternative | uv → conda |
| `often_paired` | No dependency, frequently co-adopted | pytest + Ruff |
| `wraps` | Higher-level API over another | Ray Tune → Optuna |
| `feeds_into` | Data flows from A to B | DuckDB → Observable Framework |
| `integrates_with` | Official integration exists | Ray → SLURM |

### Key Design Decisions

- **DuckDB arrays over junction tables** for simple tags (categories, languages). Junction tables only where the relationship carries data (neighborhood_members has membership + pinned).
- **JSON for requirements** in capabilities table — heterogeneous per capability, queryable via DuckDB `json_extract()`.
- **EAV for metrics** — set of metric names grows as API integrations are added. `source` field distinguishes hand_curated vs api-derived.
- **No ORM** — raw parameterized SQL. Pydantic for validation before insert.
- **"unknown" → NULL** — enum columns use NULL for unknown, not an "unknown" enum value.

## Guiding Principle

**"Grow into, don't switch."** The framework exists to end tool churn. Evaluate against ceilings (where you're going), not current needs. Re-evaluate only when a trigger fires, not when a new tool trends.

See `data/seed/project_ceilings.json` → `evaluation_protocol` for when/how to evaluate.

## Phases

| Phase | Status | What |
|-------|--------|------|
| 1 | **Done** | Schema, seed migration, CLI (import/stats/query/inspect/coverage) |
| 1b | **Done** | Hand-curated edges (75), multi-catalog migration, catalog validation |
| 2 | **Done** | Metrics pipeline (GitHub/PyPI/npm/deps.dev), fitness scoring, identifier resolution |
| 3 | Todo | Graph clustering → computed neighborhoods, recommend command |
| 4 | **Done** | Expanded catalogs: 9 catalogs × 29 dimensions (mlops, frontend, document, llm, gamedev, viz, platform, backend) |
| 5 | Todo | Interactive exploration (see `~/plans/tool-landscape-frontend.md`) |

## Seed Data

9 catalogs in `data/seed/` (all `*_catalog*.json`, loaded automatically by `migrate_tools()`):

| Catalog | Tools | Domain |
|---------|-------|--------|
| `mlops_tools_catalog.json` | 506 | ML/data/DevOps tools |
| `frontend_tools_catalog_a.json` | 74 | UI frameworks, build tools, component libraries, state mgmt |
| `frontend_tools_catalog_b.json` | 75 | Testing, SSG, validation, animation, forms, routing |
| `document_tools_catalog.json` | 83 | LaTeX, Markdown, Quarto, Typst, publishing |
| `llm_tools_catalog.json` | 71 | LLM frameworks, agents, RAG, vector DBs |
| `gamedev_tools_catalog.json` | 77 | Game engines, embedded/IoT, robotics, blockchain |
| `viz_tools_catalog.json` | 90 | Charting, graph viz, mapping, diagramming, 3D |
| `platform_tools_catalog.json` | 93 | Desktop/mobile, editors, CLI, WASM, package mgmt |
| `backend_tools_catalog.json` | 88 | Databases, auth, monitoring, service mesh, queues |

- `data/seed/project_ceilings.json` — 2 projects, 16 capabilities, evaluation protocol
- `data/seed/curated_edges.json` — 75 hand-curated typed edges

Validate catalogs: `python3 scripts/validate_catalogs.py`

The database is `.gitignore`d and rebuilt from seed via `scripts/rebuild_db.sh`.
