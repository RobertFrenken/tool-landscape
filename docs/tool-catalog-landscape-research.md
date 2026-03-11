# Tool Catalog Landscape: Research Report

> Generated 2026-03-10. Research across 20+ existing tool catalog sites, curated lists, and data sources.

## 1. Existing Catalogs

### Tier 1: Large-Scale, Structured, Scrapeable

| Source | Tools | Data Format | API? | Focus |
|--------|-------|-------------|------|-------|
| **CNCF Landscape** (landscape.cncf.io) | 2,000+ | YAML (`landscape.yml`) | Yes | Cloud-native infra |
| **LF AI & Data Landscape** (landscape.lfai.foundation) | 300+ | YAML (same format) | Yes | AI/ML projects |
| **StackShare** (stackshare.io) | 7,000+ | GraphQL API | Yes (paid tiers) | All dev tools, company adoption |
| **Best of JS** (bestofjs.org) | 2,000+ | JSON in GitHub repo | Static JSON | JS/web ecosystem |
| **Ecosyste.ms** (ecosyste.ms) | 11.4M packages | REST API (OpenAPI 3.0) | Yes, 5K/hr | Cross-registry packages |
| **deps.dev** (Google Open Source Insights) | All major registries | REST + gRPC | Yes, no auth | Dependencies + security |

### Tier 2: Curated Catalogs with Some Structure

| Source | Tools | Format | Maintained? |
|--------|-------|--------|-------------|
| **FirstMark MAD Landscape** (mad.firstmark.com) | ~1,150 | Infographic only (no data) | Annual since 2012 |
| **Chip Huyen MLOps Landscape** | 284 | Google Sheets | **Dead** (2020) |
| **EthicalML/awesome-production-ML** | 300+ | Markdown (500-star threshold) | Active |
| **MLOps.toys** (tools.mlops.community) | ~100-150 | JSON in GitHub repo | Low activity |
| **awesome-mlops** (kelvins) | 200+ | Markdown | Active |
| **awesome-selfhosted-data** | 1,000+ | YAML per entry | Active |
| **awesome-scientific-writing** | ~150 | Markdown | Moderate |
| **awesome-quarto/typst/LaTeX** | 100-200 each | Markdown | Active |

### Tier 3: Comparison/Survey Sites

| Source | Tools | Unique Value | Data Access |
|--------|-------|-------------|-------------|
| **State of JS/CSS** (devographics) | ~150/survey | Usage/satisfaction/awareness trends | Open data + GraphQL API |
| **AlternativeTo** (alternativeto.net) | 85,000+ | Alternative relationships, user votes | **No API** (taken down) |
| **npms.io** | All npm (~2M) | Quality/maintenance/popularity scores (0-1) | REST API |
| **AIMultiple** (research.aimultiple.com) | 45+ MLOps | Feature comparison tables | HTML only |
| **Valohai Compared** | ~15 platforms | Deep feature matrices | HTML only |
| **Moiva.io** | On-demand | Multi-source comparison (stars, downloads, bundles) | Open source |
| **LibHunt** (libhunt.com) | Thousands | Social signal tracking (Reddit/HN) | No API |
| **DB-Engines** (db-engines.com) | Databases only | Popularity ranking over time | Viewable only |

## 2. What They Do Well

| Strength | Best At It |
|----------|-----------|
| Massive breadth | StackShare (7K), AlternativeTo (85K), ecosyste.ms (11.4M) |
| Machine-readable YAML/JSON | CNCF/LF AI (`landscape.yml`), awesome-selfhosted-data |
| Company adoption ("who uses what") | StackShare (1.5M companies) |
| Star/download trends over time | Best of JS, DB-Engines, pypistats |
| Developer sentiment (satisfaction/awareness) | State of JS/CSS (annual surveys, open data) |
| Algorithmic quality scoring | npms.io (quality/maintenance/popularity), Libraries.io (SourceRank) |
| Dependency graph data | deps.dev, ecosyste.ms (22B relationships) |
| Security posture | OpenSSF Scorecard (18 checks), deps.dev (bundles OSV), Socket.dev |
| "Alternatives" relationships | AlternativeTo (crowdsourced), StackShare |
| Reusable landscape generator | CNCF `landscape2` tool (build your own from YAML) |

## 3. Gaps Across ALL Existing Sources

| Gap | Who Has It? | tool-landscape? |
|-----|------------|-----------------|
| Floor/ceiling evaluation ("where you're going" vs "where you are") | Nobody | **Yes** - core differentiator |
| HPC-specific dimensions (offline, resource overhead, hpc_compatible) | Nobody | **Yes** - 3 unique fields |
| Migration cost / lock-in modeling | Nobody | **Yes** - migration_cost, lock_in_risk, migration_history |
| Project-capability fitness scoring | Nobody | **Yes** - fitness table |
| Typed graph edges (requires/wraps/replaces/feeds_into) | Nobody | **Yes** - 6 edge types, 381 edges |
| Cross-domain in one DB (ML + frontend + writing + infra) | Nobody | **Yes** - 56 categories |
| Dynamic neighborhood clustering | Nobody | **Planned** (Phase 3) |
| "Grow into, don't switch" philosophy | Nobody | **Yes** - evaluation_protocol |
| Evaluation triggers (when to re-evaluate) | Nobody | **Yes** - in project_ceilings.json |

## 4. Scraping / Enrichment Opportunities

### Highest Value (Phase 2 priority)

| Source | What You Get | Method | Rate Limit | Schema Target |
|--------|-------------|--------|-----------|---------------|
| **GitHub GraphQL API** | Stars, forks, contributors, last commit, releases, license | 1 query/tool | 5,000/hr (PAT) | `tool_metrics` (github_api) |
| **deps.dev** | Dependencies, licenses, OpenSSF scores, vulnerabilities | Batch (5K items/call) | No auth, generous | `tool_metrics` + `edges` (requires) |
| **PyPI JSON API** | Downloads, version, classifiers, dependencies, repo URL | `pypi.org/pypi/{name}/json` | No auth | `tool_metrics` (pypi_stats) |
| **pypistats.org** | Monthly download time series | `pypistats.org/api/packages/{name}/recent` | No auth, daily | `tool_metrics` (pypi_stats) |
| **npm Registry** | Weekly downloads, deps, version | `registry.npmjs.org/{name}` | No auth | `tool_metrics` (npm_stats) |

### Medium Value (Phase 2b)

| Source | What You Get | Method | Effort |
|--------|-------------|--------|--------|
| **CNCF landscape.yml** | ~2K tools with URLs, categories, logos, GitHub links | Clone + parse YAML | Low |
| **LF AI landscape.yml** | ~300 AI/ML tools, same format | Clone + parse YAML | Low |
| **Libraries.io** | SourceRank composite score, cross-platform metadata | REST API + free key | 60/min |
| **npms.io** | Quality/maintenance/popularity scores (0-1) | REST API | No auth |
| **Ecosyste.ms** | Cross-registry metadata, awesome list indexing | REST API | 5K/hr |
| **Best of JS** | JS tool star trends (daily snapshots) | Clone repo, parse JSON | Low |
| **Devographics** | JS/CSS tool satisfaction/awareness scores | GitHub data exports | Medium |

### Lower Value (legal risk or limited utility)

| Source | Issue |
|--------|-------|
| **AlternativeTo** | No API, ToS prohibits scraping |
| **StackShare** | API in beta, FOSSA acquisition uncertainty, paid tiers |
| **Snyk Advisor** | Sunset Jan 2026 |
| **PePy.tech** | Free tier has no API access |

### New `metric_source` enum values needed

Current: `hand_curated`, `github_api`, `pypi_stats`, `npm_stats`, `override`

Add: `deps_dev`, `libraries_io`, `openssf_scorecard`, `npms_io`

## 5. Verdict

**Continue this repo.** Nothing else combines:

1. Floor/ceiling evaluation protocol
2. HPC-aware dimensions (offline_capable, resource_overhead)
3. Typed graph edges (6 relationship types)
4. Project-capability fitness scoring
5. Migration cost modeling
6. Cross-domain coverage in a single queryable DB
7. "Grow into, don't switch" philosophy with evaluation triggers

### Don't replicate (use existing sources)

| Don't Build | Use Instead |
|-------------|-----------|
| Star/download tracking | GitHub API + pypistats + npm registry |
| Company adoption data | StackShare |
| JS ecosystem surveys | State of JS / Best of JS |
| Dependency graphs | deps.dev |
| Security scoring | OpenSSF Scorecard via deps.dev |

### Phase 2 enrichment pipeline

```
                 ┌──────────────┐
                 │ 506 tools    │
                 │ (seed JSON)  │
                 └──────┬───────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
   GitHub GraphQL   deps.dev      PyPI/npm
   (stars,forks,   (deps,OSV,    (downloads,
    commits,        scorecard)    versions)
    releases)
          │             │             │
          └─────────────┼─────────────┘
                        ▼
              ┌─────────────────┐
              │  tool_metrics   │
              │  (EAV table)    │
              │  + new edges    │
              └─────────────────┘
```
