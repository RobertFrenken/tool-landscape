# tool-landscape: Developer Tool Evaluation Framework

Map project goals (floor/ceiling) to stable tool neighborhoods. Designed for small HPC research labs that want to stop churning through tech stacks.

## Key Commands

```bash
# Rebuild database from seed JSON
bash scripts/rebuild_db.sh

# CLI — Core
landscape import --seed                     # Migrate seed JSON → DuckDB
landscape stats                             # Row counts, DB size
landscape query --category orchestrator     # Filter tools
landscape query --used-by KD-GAT            # Tools in a project's stack
landscape query --ceiling extensive --momentum growing  # High-ceiling growing tools
landscape inspect <tool-name>               # Full detail + edges + neighborhoods
landscape coverage <project-name>           # Capability coverage report
landscape neighborhoods compute [--resolution 1.0] [--min-size 3]
landscape neighborhoods list                # Show all neighborhoods
landscape neighborhoods show <name>         # Tools in a neighborhood
landscape recommend --tool <name>           # Related tool recommendations
landscape recommend --capability NAME --project NAME
landscape validate                          # Run data quality checks
landscape export [--output DIR]             # Export tables to Parquet

# CLI — Shopping (tool/stack evaluation)
landscape shop specs/tool-landscape-spec.yaml          # v1: per-slot filter+score
landscape shop-stack --spec specs/tool-landscape-spec.yaml  # v2: hand-authored stack comparison
landscape shop-stack --spec SPEC --auto                # v2: auto-generate stacks from v1 results
landscape shop-stack --spec SPEC --auto --explain      # v2: with evidence trails
landscape shop-stack --spec SPEC --auto --top-n 3 --max-stacks 10  # tune generation
landscape spec validate specs/tool-landscape-spec.yaml # validate spec (v1 or v2)
landscape spec templates                               # list available templates

# Frontend (Observable Framework)
cd site && npm run dev                      # Local preview
cd site && npm run build                    # Build static site

# Development
uv pip install -e ".[dev]" --python .venv/bin/python
.venv/bin/pytest
```

## Architecture

### Database (DuckDB)

9 tables in `data/landscape.duckdb` (rebuilt from `data/seed/`):

| Table | Role | Rows |
|-------|------|------|
| `tools` | Tool catalog: identity, enums, arrays, booleans | 1165 |
| `edges` | Typed directed relationships between tools | 1553 |
| `tool_metrics` | Time-series metrics with source tracking (EAV) | 0 (Phase 2) |
| `neighborhoods` | Computed Louvain clusters | 58 |
| `neighborhood_members` | Tool membership in neighborhoods (soft, pinnable) | 1157 |
| `projects` | Project definitions with environment constraints | 2 |
| `capabilities` | Per-project floor/ceiling requirements | 16 |
| `fitness` | Tool × capability fitness scores | 0 (Phase 2) |
| `validation_flags` | Data quality issues detected by validator | 0 (on-demand) |
| `migration_history` | Past tool switches (from git history) | 0 (Phase 1b) |

### Package Structure

```
landscape/
  db/
    schema.py       # DDL: all CREATE TYPE / TABLE / INDEX
    connection.py   # DuckDB connection manager
    migrate.py      # JSON → DuckDB migration
  models/
    spec.py         # Pydantic v2 models (ProjectSpec, v2: DataFlow, TimeHorizon, etc.)
  cli/
    main.py         # Entry point + all subcommands (incl. shop-stack)
  analysis/
    shop.py          # 2-phase shop (v1) + stack evaluation (v2) + auto-generation
    fitness.py       # Floor/ceiling scoring
    neighborhoods.py # Louvain graph clustering (58 neighborhoods)
    recommend.py     # Tool recommendations
    validate.py      # Data quality validation
    metrics.py       # GitHub/PyPI metric collection
  spec/
    templates.py     # Template loading + extends resolution + v2 field merging
    build.py         # Interactive spec builder
    extract.py       # Draft spec extraction from codebases
  export.py          # DuckDB → Parquet export for frontend

data/templates/        # Archetype templates (data-dashboard, ml-research-hpc, etc.)
specs/                 # Project spec files (v1 or v2 YAML)

site/                  # Observable Framework frontend (Phase 5)
  src/
    index.md           # Dashboard (summary stats, distributions)
    graph.md           # D3 force-directed graph explorer
    tools.md           # Filterable tool table
    coverage.md        # Project capability coverage
    compare.md         # Side-by-side tool comparison
    data/*.parquet     # Exported Parquet files (committed)
  observablehq.config.js
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

### Spec v2 (Ecosystem Evaluation)

v2 specs add 5 optional sections (backward compatible with v1):

| Section | Purpose |
|---------|---------|
| `data_flow` | Pipeline stages + integration boundary friction |
| `time_horizon` | Planned work, ceiling deadlines, Wardley evolution |
| `migration` | One-time effort + ongoing friction per component |
| `candidate_stacks` | Named complete stacks to evaluate as units |
| `stack_boundary_overrides` | Per-stack friction overrides (e.g., Svelte reduces serving→presentation friction) |

**Three evaluation modes:**
1. `landscape shop SPEC` — v1: per-slot filter+score (good for discovering candidates)
2. `landscape shop-stack --spec SPEC` — v2: compare hand-authored stacks
3. `landscape shop-stack --spec SPEC --auto --explain` — v2: auto-generate stacks from v1 results with evidence trails

## Guiding Principle

**"Grow into, don't switch."** The framework exists to end tool churn. Evaluate against ceilings (where you're going), not current needs. Re-evaluate only when a trigger fires, not when a new tool trends.

See `data/seed/project_ceilings.json` → `evaluation_protocol` for when/how to evaluate.

## Phases

| Phase | Status | What |
|-------|--------|------|
| 1 | **Done** | Schema, seed migration, CLI (import/stats/query/inspect/coverage) |
| 1b | **Done** | Hand-curated edges (75), multi-catalog migration, catalog validation |
| 2 | **Done** | Metrics pipeline (GitHub/PyPI/npm/deps.dev), fitness scoring, identifier resolution |
| 3 | **Done** | Graph clustering (Louvain → 58 neighborhoods), recommend command, validate command |
| 4 | **Done** | Expanded catalogs: 9 catalogs × 29 dimensions (mlops, frontend, document, llm, gamedev, viz, platform, backend) |
| 5 | **Done** | Observable Framework frontend: dashboard, graph explorer, tool table, coverage, compare |
| 6 | **Done** | Ecosystem redesign: spec v2 (DataFlow, TimeHorizon, MigrationEconomics), stack evaluation engine, 4 archetype templates, auto-stack generation, explainability layer |

## Seed Data

9 catalogs in `data/seed/` (all `*_catalog*.json`, loaded automatically by `migrate_tools()`):

| Catalog | Tools | Domain |
|---------|-------|--------|
| `mlops_tools_catalog.json` | 509 | ML/data/DevOps tools |
| `frontend_tools_catalog_a.json` | 75 | UI frameworks, build tools, component libraries, state mgmt |
| `frontend_tools_catalog_b.json` | 75 | Testing, SSG, validation, animation, forms, routing |
| `document_tools_catalog.json` | 83 | LaTeX, Markdown, Quarto, Typst, publishing |
| `llm_tools_catalog.json` | 71 | LLM frameworks, agents, RAG, vector DBs |
| `gamedev_tools_catalog.json` | 77 | Game engines, embedded/IoT, robotics, blockchain |
| `viz_tools_catalog.json` | 94 | Charting, graph viz, mapping, diagramming, 3D |
| `platform_tools_catalog.json` | 93 | Desktop/mobile, editors, CLI, WASM, package mgmt |
| `backend_tools_catalog.json` | 88 | Databases, auth, monitoring, service mesh, queues |

- `data/seed/project_ceilings.json` — 2 projects, 16 capabilities, evaluation protocol
- `data/seed/curated_edges.json` — 164 hand-curated typed edges

Validate catalogs: `python3 scripts/validate_catalogs.py`

The database is `.gitignore`d and rebuilt from seed via `scripts/rebuild_db.sh`.
