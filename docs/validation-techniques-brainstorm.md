# Validation Techniques for Tool-Landscape Catalog Annotations

Brainstorm document — 2026-03-11

The tool-landscape database contains 1157 tools across 9 domain catalogs, each annotated with ~29 dimensions (enum assessments, boolean flags, array fields). All values were hand-curated in seed JSON. This document surveys techniques to verify and improve annotation accuracy.

---

## 1. Internal Consistency Checks (Cross-Field Within a Single Tool)

These require no external data — purely logical rules applied across a tool's own fields.

### 1a. Enum Plausibility Rules

Define conditional rules that flag implausible combinations:

| Rule | Rationale |
|------|-----------|
| `maturity: archived` + `community_momentum: growing` | Dead projects don't grow |
| `maturity: experimental` + `documentation_quality: excellent` | Rare for pre-release tools to have great docs |
| `maturity: production` + `community_momentum: declining` + `capability_ceiling: extensive` | Plausible (legacy tools), but worth review |
| `open_source: false` + `governance: community` | Contradiction: community governance implies open source |
| `open_source: false` + `license` is an OSS license (MIT, Apache-2.0, etc.) | License/open_source mismatch |
| `hpc_compatible: native` + `deployment_model` contains only `saas` | SaaS-only tools aren't HPC-native |
| `saas_available: true` + `offline_capable: true` + `self_hosted_viable: false` | If it's not self-hostable, offline_capable is suspect |
| `resource_overhead: heavy` + `scale_profile` is `single_node` only | Heavy overhead usually implies distributed intent |
| `python_native: true` + `language_ecosystem` doesn't contain `python` | Direct contradiction |
| `lock_in_risk: low` + `migration_cost: high` | Low lock-in usually means easy migration |
| `composite_tool: true` + `categories` has only one entry | Composite tools should span multiple categories |
| `capability_ceiling: low` + `interoperability: extensive` | Extensive integration surface with low ceiling is unusual |

**Implementation:** A single Python script with rule functions that return `(tool_name, rule_name, severity, message)`. Run against the seed JSON directly (no DB needed).

- **Effort:** Low (1-2 hours)
- **Accuracy:** High for catching outright contradictions, medium for "implausible but possible" cases
- **Automatable:** Fully
- **Dependencies:** None (just Python + JSON)

### 1b. Array Field Consistency

- Tools with `language_ecosystem: ["python"]` should have `python_native: true` (or at least a reviewable exception)
- `deployment_model` containing `saas` should align with `saas_available: true`
- `deployment_model` containing `self_hosted` should align with `self_hosted_viable: true`
- `pipeline_stage` should be non-empty for any tool with `categories` in the ML/data domain
- `integration_targets` should be non-empty for tools with `interoperability: high` or `extensive`

**Effort:** Low | **Accuracy:** High | **Automatable:** Fully | **Dependencies:** None

### 1c. Summary vs. Enum Coherence (NLP-lite)

Use keyword extraction on the `summary` field and cross-check against enums:
- Summary mentions "experimental" or "alpha" but `maturity` is `production`
- Summary mentions "cloud" or "SaaS" but `hpc_compatible` is `native`
- Summary mentions "deprecated" or "archived" but `maturity` is not `archived`
- Summary mentions "replaces X" — flag if no `replaces` edge exists to X

**Effort:** Low-Medium (regex/keyword matching, no LLM needed) | **Accuracy:** Medium | **Automatable:** Fully | **Dependencies:** None

---

## 2. Cross-Reference with External APIs

Leverage the existing metrics pipeline (`landscape/analysis/collectors/`) and resolved identifiers.

### 2a. Momentum Validation via Download Trends

**Approach:** Compare `community_momentum` enum against actual download trajectories.

1. Collect PyPI/npm monthly downloads at two time points (or use existing `tool_metrics` if populated)
2. Compute month-over-month growth rate
3. Flag mismatches:
   - `momentum: growing` but downloads flat or declining (< 5% MoM growth)
   - `momentum: declining` but downloads increasing (> 10% MoM growth)
   - `momentum: stable` but downloads exploding (> 50% MoM growth)

**Data sources:**
- PyPI: `pypistats.org/api` (already in `collectors/pypi.py`)
- npm: `api.npmjs.org/downloads/range` (already in `collectors/npm.py`)
- GitHub: stars-over-time via star-history API or GH Archive

**Effort:** Medium (need two data points separated by time, or use BigQuery/GH Archive for historical)
**Accuracy:** High for Python/JS tools, N/A for tools without package registries
**Automatable:** Fully (extend existing collectors to store time-series)
**Dependencies:** API access (no auth needed for PyPI/npm), GitHub token for stars

### 2b. Maturity Validation via Release History

**Approach:** Cross-check `maturity` enum against release patterns:

| Signal | Implies |
|--------|---------|
| No release in 2+ years | `archived` or `stable` (not `growth`) |
| Pre-1.0 semver + frequent releases | `early` or `growth` |
| 1.0+ semver + regular releases | `production` |
| Explicit "archived" badge on GitHub | `archived` |
| Last commit > 3 years ago | Likely `archived` |

**Data sources:**
- GitHub Releases API (latest release date, tag names)
- PyPI release history (version numbers + dates)
- GitHub repo metadata (archived flag, last push date)

**Effort:** Medium (parse semver, compute release cadence)
**Accuracy:** High
**Automatable:** Fully
**Dependencies:** GitHub token, existing resolve.py identifiers

### 2c. Governance Validation

**Approach:** Cross-check `governance` enum against actual project ownership:
- `governance: cncf` — verify against CNCF landscape API (`landscape.cncf.io/data/items.json`)
- `governance: linux_foundation` — verify against LF project list
- `governance: apache_foundation` — verify against `projects.apache.org/projects.json`
- `governance: company_backed` — check GitHub org membership (e.g., `facebook/`, `google/`, `microsoft/`)
- `governance: community` — verify no single corporate owner dominates commits

**Effort:** Medium-High (multiple APIs, some don't have clean endpoints)
**Accuracy:** High for foundation membership, medium for community vs. company_backed
**Automatable:** Mostly (foundation lists are static JSON; commit analysis is heavier)
**Dependencies:** CNCF/ASF/LF public APIs

### 2d. License Validation

**Approach:** Cross-check `license` field against actual license metadata:
- GitHub API returns `license.spdx_id`
- PyPI metadata includes `license` and `classifiers`
- npm `package.json` has `license` field

Flag: `license` in seed doesn't match any registry source.

**Effort:** Low (data already partially available from resolve step)
**Accuracy:** Very high
**Automatable:** Fully
**Dependencies:** GitHub/PyPI/npm APIs (already integrated)

### 2e. Stars/Downloads → Capability Ceiling Sanity Check

Very popular tools (top 1% by stars or downloads) with `capability_ceiling: low` deserve review. Similarly, obscure tools (< 100 stars, < 1K downloads/month) with `capability_ceiling: extensive` should be flagged — high ceiling with no adoption is suspicious (or the tool is very new).

**Effort:** Low (one SQL query after metrics collection)
**Accuracy:** Medium (popularity ≠ capability, but strong correlation)
**Automatable:** Fully
**Dependencies:** Populated `tool_metrics` table

---

## 3. Inter-Tool Consistency (Edge Validation)

Validate that relationships between tools are internally coherent.

### 3a. `replaces` Edge Rules

For every `A replaces B`:
- A and B should share at least one `category`
- A's `capability_ceiling` should be >= B's ceiling (or equal)
- A's `community_momentum` should generally be >= B's momentum
- A's `maturity` should generally be >= B's maturity (exception: new tools replacing legacy ones can be `growth` replacing `production`)
- Both should share at least one `language_ecosystem` OR one `pipeline_stage`

**Example violations to catch:**
- "uv replaces conda" — do both have `python` in `language_ecosystem`? Yes. Does uv's ceiling >= conda's? Check.
- "Ruff replaces mypy" — these don't fully overlap in category; the evidence note already hedges ("not full type checking"). Flag for review.

### 3b. `wraps` Edge Rules

For every `A wraps B`:
- A should have B (or B's ecosystem) in its `integration_targets`
- A's `resource_overhead` should be >= B's (wrappers add overhead)
- A should have at least one `language_ecosystem` in common with B
- A's `capability_ceiling` should be >= B's (wrappers usually add functionality)

### 3c. `feeds_into` Edge Rules

For every `A feeds_into B`:
- A's `pipeline_stage` should precede B's `pipeline_stage` in a logical ordering (data_collection → preprocessing → training → evaluation → deployment → monitoring)
- They should share at least one `deployment_model` or `integration_targets` overlap

### 3d. `often_paired` Symmetry

For every `A often_paired B`, check if the reverse edge exists or if it should be symmetric. Currently the edge table allows directed pairs — verify no "A often_paired B" exists without "B often_paired A" being at least implied.

### 3e. Missing Edges

Flag tools that have `integration_targets` listing another tool's name but no edge exists between them.

**Effort:** Medium (implement rule engine over edge+tool data)
**Accuracy:** High for structural rules, medium for ordinal comparisons
**Automatable:** Fully
**Dependencies:** Loaded DB with both tools and edges tables

---

## 4. Coverage Validation (Data Completeness)

### 4a. NULL / Empty Field Audit

Run a SQL audit across all columns:

```sql
SELECT
    'maturity' as field,
    COUNT(*) FILTER (WHERE maturity IS NULL) as null_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE maturity IS NULL) / COUNT(*), 1) as null_pct
FROM tools
UNION ALL
SELECT 'community_momentum', COUNT(*) FILTER (WHERE community_momentum IS NULL),
    ROUND(100.0 * COUNT(*) FILTER (WHERE community_momentum IS NULL) / COUNT(*), 1)
FROM tools
-- ... repeat for all enum columns
```

Similarly for array fields:
```sql
SELECT 'categories' as field, COUNT(*) FILTER (WHERE len(categories) = 0) as empty_count FROM tools
```

### 4b. "Thin" Tool Detection

Score each tool by completeness: count non-NULL enum fields + non-empty array fields. Tools in the bottom 10% are candidates for enrichment.

### 4c. Cross-Catalog Consistency

Compare field distributions across catalogs. If the `mlops` catalog has 80% of tools with `documentation_quality` filled but `gamedev` catalog has only 30%, that signals uneven curation effort.

### 4d. Identifier Resolution Coverage

Check what percentage of tools have resolved `github_repo`, `pypi_package`, or `npm_package`. Tools without any identifier can't participate in automated metrics collection.

```sql
SELECT
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE github_repo IS NOT NULL) as has_github,
    COUNT(*) FILTER (WHERE pypi_package IS NOT NULL) as has_pypi,
    COUNT(*) FILTER (WHERE npm_package IS NOT NULL) as has_npm,
    COUNT(*) FILTER (WHERE github_repo IS NULL AND pypi_package IS NULL AND npm_package IS NULL) as no_identifiers
FROM tools;
```

**Effort:** Low (pure SQL)
**Accuracy:** N/A (descriptive, not predictive)
**Automatable:** Fully
**Dependencies:** Loaded DB

---

## 5. LLM-Assisted Validation

### 5a. README-vs-Annotations Review

**Approach:** For each tool, fetch its GitHub README (via API or raw URL), then prompt an LLM:

```
Given this tool's README:
{readme_content}

And these annotations:
- maturity: {maturity}
- community_momentum: {momentum}
- capability_ceiling: {ceiling}
- categories: {categories}
- summary: {summary}
...

Rate each annotation as: CORRECT, PLAUSIBLE, QUESTIONABLE, or WRONG.
Provide a one-line justification for any QUESTIONABLE or WRONG rating.
```

**Batch strategy:** Process in batches of 50-100 tools. Use Claude API with structured output (JSON mode). Cost estimate: ~1157 tools x ~2K tokens input + ~500 tokens output = ~2.9M input tokens + ~580K output tokens. At Sonnet pricing, approximately $12-15 total.

**Effort:** Medium (build the pipeline, handle rate limits, parse results)
**Accuracy:** High for objective fields (license, open_source, language_ecosystem), medium for subjective fields (ceiling, momentum)
**Automatable:** Fully (batch pipeline)
**Dependencies:** Claude API key, GitHub token for README fetching, ~$15 API cost

### 5b. Summary Quality Check

Prompt an LLM to evaluate whether each `summary` field accurately describes what the tool does, compared to the tool's actual documentation. Flag summaries that are:
- Factually incorrect
- Too vague to be useful
- Missing key differentiators
- Containing outdated information

**Effort:** Low-Medium (simpler prompt than 5a)
**Accuracy:** Medium-High
**Automatable:** Fully
**Dependencies:** Claude API, ~$5-8 API cost

### 5c. Category Suggestion

For tools where `categories` seems thin (1 entry) or potentially wrong, prompt an LLM with the tool's README and ask it to suggest categories from the valid set. Compare against existing annotations.

**Effort:** Medium
**Accuracy:** Medium (LLMs can hallucinate categories)
**Automatable:** Fully
**Dependencies:** Claude API, defined category vocabulary

### 5d. Edge Discovery via LLM

For tools with no edges, prompt: "What tools does {tool_name} integrate with, replace, or wrap?" Compare responses against the existing tool catalog to discover missing edges.

**Effort:** Medium-High (need to ground LLM responses against the actual tool list)
**Accuracy:** Medium (LLMs know popular tool relationships well, obscure ones less so)
**Automatable:** Mostly
**Dependencies:** Claude API

---

## 6. Statistical Outlier Detection

### 6a. Enum Distribution Anomalies per Category

For each `category`, compute the distribution of each enum field. Flag tools whose enum values are rare for their category:

```python
# Pseudocode
for category in all_categories:
    tools_in_cat = [t for t in tools if category in t['categories']]
    for field in enum_fields:
        distribution = Counter(t[field] for t in tools_in_cat)
        for tool in tools_in_cat:
            if distribution[tool[field]] / len(tools_in_cat) < 0.05:
                flag(tool, field, "rare value for category")
```

Example: If 95% of `ui_framework` tools have `hpc_compatible: unknown` or `cloud_only`, but one has `native`, that's worth checking.

### 6b. Boolean Flag Clusters

Cluster tools by their boolean flags (`python_native`, `offline_capable`, `saas_available`, `self_hosted_viable`, `composite_tool`) and identify outliers — tools whose boolean profile doesn't match any common cluster.

### 6c. Embedding-Based Outlier Detection

1. Encode each tool as a feature vector from its enum/boolean/array fields (one-hot for enums, multi-hot for arrays)
2. Run UMAP or t-SNE for dimensionality reduction
3. Identify tools that are far from their category centroid
4. These are either genuinely unique or miscategorized

**Effort:** Medium (feature engineering + basic ML)
**Accuracy:** Medium (finds anomalies, doesn't confirm errors)
**Automatable:** Fully
**Dependencies:** scikit-learn, umap-learn (lightweight)

### 6d. Ordinal Consistency Scoring

For ordinal enums (`maturity`, `momentum`, `capability_ceiling`, etc.), check if a tool's ordinal values are internally consistent. Define an expected "maturity profile":

| maturity | Expected momentum | Expected docs | Expected ceiling |
|----------|------------------|---------------|-----------------|
| archived | declining | any | any |
| experimental | growing | unknown/poor | low/medium |
| early | growing | adequate | medium |
| growth | growing | adequate/excellent | medium/high |
| production | stable/growing | adequate/excellent | high/extensive |

Score each tool against these profiles. Tools with low profile-match scores are review candidates.

**Effort:** Low-Medium
**Accuracy:** Medium (profiles are heuristics, many valid exceptions exist)
**Automatable:** Fully
**Dependencies:** None

---

## 7. Community Signal Validation

### 7a. deps.dev Data (Already Integrated)

The existing `collectors/deps_dev.py` collects OpenSSF Scorecard data. Extend to also pull:
- **Dependent count:** Number of packages that depend on this tool. Cross-check against `interoperability` tier.
- **Advisory count:** Security advisories. Many advisories + `maturity: production` may warrant review.
- **Scorecard checks:** Specific checks like `Maintained`, `Branch-Protection` correlate with governance quality.

**Effort:** Low (extend existing collector)
**Accuracy:** High
**Automatable:** Fully
**Dependencies:** deps.dev API (no auth needed)

### 7b. StackOverflow Tag Activity

**Approach:** Use StackExchange API to check if a tool has an active SO tag:
- Tag existence and question count → validates `community_momentum`
- Questions/month trend → growing/declining signal
- Unanswered rate → correlates with `documentation_quality` (poor docs = more unanswered questions)

**Effort:** Medium (new API integration)
**Accuracy:** Medium (not all tools have SO tags; bias toward popular tools)
**Automatable:** Fully
**Dependencies:** StackExchange API (quota: 300 requests/day without key, 10K with)

### 7c. CNCF/LF/ASF Landscape Cross-Reference

The CNCF publishes a machine-readable landscape (`landscape.cncf.io`). Cross-reference:
- Tools claiming `governance: cncf` should appear in the CNCF landscape
- CNCF maturity levels (sandbox/incubating/graduated) map to our `maturity` enum
- CNCF category assignments can validate our `categories` array

Similarly for Apache projects (`projects.apache.org`) and Linux Foundation.

**Effort:** Low-Medium (CNCF has clean JSON, ASF/LF less so)
**Accuracy:** Very high for governance/maturity of foundation projects
**Automatable:** Fully
**Dependencies:** HTTP access to landscape data files

### 7d. GitHub "Archived" Flag

GitHub API returns `archived: true` for archived repos. Any tool with a non-archived repo but `maturity: archived` (or vice versa) is a mismatch.

**Effort:** Very low (single API field, already fetching repo metadata)
**Accuracy:** Very high
**Automatable:** Fully
**Dependencies:** GitHub token (already in pipeline)

### 7e. Awesome-List Cross-Reference

Many domains have "awesome-X" lists on GitHub (awesome-python, awesome-machine-learning, etc.). Tools appearing in these curated lists have community endorsement — compare their categorization and descriptions against ours.

**Effort:** Medium-High (parsing markdown lists is messy)
**Accuracy:** Low-Medium (awesome lists have their own biases)
**Automatable:** Partially
**Dependencies:** GitHub raw content access

---

## Recommended Implementation Order

Priority based on effort-to-value ratio:

| Priority | Technique | Section | Effort | Value |
|----------|-----------|---------|--------|-------|
| 1 | Internal consistency rules | 1a, 1b | Low | High |
| 2 | NULL/coverage audit | 4a, 4b, 4d | Low | High |
| 3 | GitHub archived flag check | 7d | Very Low | High |
| 4 | License cross-validation | 2d | Low | High |
| 5 | Edge consistency rules | 3a-3e | Medium | High |
| 6 | Momentum via download trends | 2a | Medium | High |
| 7 | Maturity via release history | 2b | Medium | High |
| 8 | Enum distribution outliers | 6a, 6d | Low-Med | Medium |
| 9 | CNCF/ASF governance check | 7c | Low-Med | Medium |
| 10 | LLM README review (batch) | 5a | Medium | High |
| 11 | deps.dev dependent count | 7a | Low | Medium |
| 12 | StackOverflow signals | 7b | Medium | Medium |
| 13 | Embedding-based outliers | 6c | Medium | Low-Med |
| 14 | Awesome-list cross-ref | 7e | Med-High | Low |

---

## Architecture Suggestion

All validation techniques could feed into a unified **validation report table**:

```sql
CREATE TABLE IF NOT EXISTS validation_flags (
    flag_id     INTEGER PRIMARY KEY,
    tool_id     INTEGER REFERENCES tools(tool_id),
    edge_id     INTEGER REFERENCES edges(edge_id),  -- NULL if tool-level flag
    field_name  VARCHAR NOT NULL,                    -- Which field is suspect
    rule_name   VARCHAR NOT NULL,                    -- Which validation rule fired
    severity    VARCHAR NOT NULL,                    -- 'error', 'warning', 'info'
    message     VARCHAR NOT NULL,
    suggested_value VARCHAR,                         -- What the validator thinks it should be
    source      VARCHAR NOT NULL,                    -- 'internal_rules', 'api_crossref', 'llm_review', 'statistical'
    resolved    BOOLEAN DEFAULT false,
    resolved_at TIMESTAMP,
    created_at  TIMESTAMP DEFAULT current_timestamp
);
```

A CLI command like `landscape validate` could run all enabled checks and populate this table. A `landscape validate --report` command could summarize outstanding flags by severity and source.

This keeps the seed JSON as the source of truth while providing a reviewable queue of potential corrections.
