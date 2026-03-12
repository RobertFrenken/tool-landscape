# Ecosystem Evaluation Results: tool-landscape Frontend

**Date:** 2026-03-11
**Spec:** `specs/tool-landscape-spec.yaml` (v2)
**Database:** 1165 tools, 1553 edges, 58 neighborhoods
**Framework:** Phase C engine (shop_stack, propagate_constraints, migration_roi)

---

## 1. v1 Evaluation: Legacy `shop` Command (Component-Isolated)

The legacy `shop` command evaluates each component independently — no cross-component awareness.

### site_framework (filtered from 1165 → 18 tools)

```
Rank  Tool              Score    Fit   Pref  Coher
1     Astro              70.4   76.0  100.0    0.0
2     SvelteKit          66.3   74.0   91.7    0.0
3     SolidStart         61.7   71.0   83.3    0.0
4     Analog             61.7   71.0   83.3    0.0
5     Remix              60.5   68.0   83.3    0.0
...
10    Zola               54.0   60.0   75.0    0.0
```

Observable Framework does not appear — it scored below threshold. Astro wins on fitness (76.0)
and hits 100% of weighted preferences. No coherence bonus because the v1 engine only checks
edges to a single "reference stack", not to sibling components in the candidate stack.

### visualization

```
Rank  Tool              Score    Fit   Pref  Coher
1     Datasette          73.3   74.0  100.0   18.8
2     Mosaic             70.3   70.0  100.0   11.7
3     Grafana            69.6   74.0  100.0    0.0
4     ECharts            68.8   72.0  100.0    0.0
5     G2                 68.0   70.0  100.0    0.0
```

D3.js (current) does not appear in the top 10. Datasette leads due to high fitness + coherence
edge bonus with other tools in the default reference set. The v1 result here is misleading:
D3.js has `categories: [visualization]` but was pinned in `stack_pins` and treated as
reference, not a candidate. Component isolation makes it hard to see D3 explicitly validated.

### ui_framework (filtered from 1165 → 15 tools)

```
Rank  Tool              Score    Fit   Pref  Coher
1     Svelte             70.4   76.0  100.0    0.0
2     Solid              66.9   74.0   93.3    0.0
3     Qwik               63.2   69.0   83.3   11.2
```

Svelte wins clearly. The key driver: `resource_overhead: minimal` (weight 5) and
`lock_in_risk: low` (weight 4). Svelte compiles to vanilla JS with no runtime — both
preferences are satisfied.

### build_tool (filtered from 1165 → 13 tools)

```
Rank  Tool              Score    Fit   Pref  Coher
1     Vite               72.7   76.0  100.0   11.2
2     Rspack             69.2   73.0  100.0    0.0
```

Vite wins. The coherence bonus (11.2) reflects its edge to Svelte and Astro in the DB.

### graph_analytics, package_management

Both returned 0 survivors after filtering — no tools matched the required categories.
This is a data gap: `graph_analytics` exists as a category label but few tools carry it.

---

## 2. v2 Evaluation: `shop-stack` Command (Ecosystem-Aware)

The v2 engine evaluates all candidate stacks simultaneously across five dimensions:

```
Weights: fitness×0.30  coherence×0.25  friction×0.15  roi×0.15  time_horizon×0.15
```

### Full Results Table

```
Rank  Stack              Score    Fit  Coher   Fric    ROI     TH  Violations
  1   current            0.632  0.516  0.833  0.625  0.500  0.667  none
  2   astro_svelte       0.616  0.740  0.400  0.625  0.500  0.833  none
  3   sveltekit          0.615  0.736  0.400  0.625  0.500  0.833  none
```

### Stack Breakdown

#### current (Observable Framework stack)

| Component      | Tool                 | Fitness |
|----------------|----------------------|---------|
| site_framework | Observable Framework |   0.580 |
| ui_framework   | (none)               |   0.000 |
| build_tool     | Observable Framework |   0.580 |
| visualization  | D3.js                |   0.620 |
| query_engine   | DuckDB-WASM          |   0.800 |

- avg_fitness: **0.516** — dragged down by missing ui_framework (0.000)
- internal_coherence: **0.833** — high, because Observable Framework + D3.js + DuckDB-WASM
  have direct "feeds_into" / "integrates_with" edges and share the same neighborhood
- boundary_friction: **0.625** — medium/high boundary between export and serving
  (file handoff) and the Arrow proxy impedance at the serving/presentation boundary
- migration_roi: **0.500** — neutral (the spec's `migration.one_time.site_framework`
  effort = 16 hours, ongoing friction = 1.5 h/week trending increasing, but the
  engine currently returns a constant 0.5 placeholder for stacks with no migration)
- time_horizon_fit: **0.667** — penalized: `site_framework` ceiling is "2026-Q2" and
  the spec's `time_horizon.planned_work` includes high-complexity interactive form
  work in 2026-Q1. Observable Framework's ceiling is not "extensive" for form state.

#### astro_svelte

| Component      | Tool        | Fitness |
|----------------|-------------|---------|
| site_framework | Astro       |   0.760 |
| ui_framework   | Svelte      |   0.760 |
| build_tool     | Vite        |   0.760 |
| visualization  | D3.js       |   0.620 |
| query_engine   | DuckDB-WASM |   0.800 |

- avg_fitness: **0.740** — every component scores well; no missing ui_framework
- internal_coherence: **0.400** — the engine finds fewer direct edges between
  Astro/Svelte/Vite/D3/DuckDB-WASM in the graph. Many "integrates_with" edges were
  not yet populated for Astro at time of evaluation.
- boundary_friction: **0.625** — same as current (spec boundaries unchanged)
- migration_roi: **0.500** — same placeholder (no migration-from-current data)
- time_horizon_fit: **0.833** — higher: Astro + Svelte ceiling = "extensive",
  satisfying the 2026-Q2 deadline requirement

#### sveltekit

Nearly identical to astro_svelte. SvelteKit scores 0.740 vs Astro's 0.760 on
site_framework fitness, accounting for the 0.001 total score gap.

---

## 3. Does the v2 Evaluation Reach a Different Conclusion?

**Yes and no.**

The v2 engine returns "current" as the winner (0.632 vs 0.616), but the margin is
narrow (0.016) and the reasons reveal a split decision:

| Dimension         | Winner     | Why                                                          |
|-------------------|------------|--------------------------------------------------------------|
| avg_fitness       | astro_svelte | 0.740 vs 0.516 — Astro+Svelte hit all weighted preferences |
| internal_coherence| current     | 0.833 vs 0.400 — existing stack has more graph edges       |
| boundary_friction | tie         | 0.625 both — same data_flow spec, same boundaries          |
| migration_roi     | tie         | 0.500 both — placeholder; see §4                           |
| time_horizon_fit  | astro_svelte | 0.833 vs 0.667 — planned high-complexity form work         |

**The v1 engine, evaluated component-by-component, unambiguously recommended Astro +
Svelte + Vite.** Every component independently ranked those tools #1. The v1 result
gives no way to weigh that recommendation against migration cost or stack coherence.

**The v2 engine keeps "current" on top, but only because `coherence×0.25` compensates
for the large fitness gap.** The current stack's coherence advantage is structural: the
DB contains more recorded edges for tools that have been used together in production
(Observable + D3 + DuckDB-WASM) versus a newer stack (Astro + Svelte + Vite + DuckDB-WASM)
where the edges simply haven't been curated yet.

---

## 4. How data_flow, migration_roi, and time_horizon Changed the Recommendation

### data_flow boundaries → boundary_friction score

The spec defines 4 pipeline stages with explicit friction labels:

```
storage → analysis       friction: low     (same Python process)
analysis → export        friction: low     (DuckDB COPY TO)
export → serving         friction: medium  (file handoff)
serving → presentation   friction: high    (Arrow proxy, no execution order)
```

The engine maps `[low, medium, high]` → `[1.0, 0.5, 0.0]` and averages:
`(1.0 + 1.0 + 0.5 + 0.0) / 4 = 0.625`. This is stack-invariant — all three candidate
stacks inherit the same data_flow spec, so boundary_friction cannot distinguish them.

**What changes:** If the astro_svelte stack eliminated the `serving → presentation`
high-friction boundary (Astro + Svelte components have explicit state, no Arrow proxy
impedance), that boundary should be re-rated as `medium` or `low`. That would lift
astro_svelte's boundary_friction to `(1.0+1.0+0.5+0.5)/4 = 0.75` vs current's 0.625
— enough to flip the winner. The spec notes this qualitatively but the boundary
friction values haven't been split by candidate stack yet.

### migration_roi → currently a placeholder

The spec provides `migration.one_time.site_framework.effort_hours = 16` and
`migration.ongoing_friction.site_framework.hours_per_week = 1.5 (increasing)`. The
engine's `migration_roi()` function returns **0.5 for all stacks** because the
"from current" cost calculation isn't yet wired to the spec's migration section when
the stack being evaluated IS the current stack (ROI = 0 migration needed = neutral 0.5).

A meaningful ROI calculation would be:
- current: ROI = undefined (no migration) → normalize to 0.5
- astro_svelte: ROI = ongoing_friction_savings / migration_cost
  = (1.5 h/wk × 52 wk × $X/hr) / (16 hrs × $X/hr) ≈ break-even in ~10 weeks

At 10-week break-even with an "increasing" friction trend, astro_svelte should score
significantly above 0.5 on ROI. This is a known gap in Phase C implementation.

### time_horizon_fit → did change the result

The time_horizon dimension is the only one that materially differentiated the stacks:
0.833 for astro_svelte/sveltekit vs 0.667 for current. The planned work includes:

- "Interactive shop page with nested form state" (2026-Q1, complexity: high)
- "Stack comparison wizard" (2026-Q2, complexity: medium)

Both affect `site_framework` and `ui_framework`. The spec's `ceiling_timeline` sets
`site_framework: "2026-Q2"` — meaning the ceiling must be reached by then. Observable
Framework's `capability_ceiling = medium` scores lower against the `extensive` preference
(weight 4) than Astro's `capability_ceiling = extensive`.

---

## 5. Lessons Learned / What the Framework Reveals

### 1. Coherence scores reflect data density, not truth

The current stack wins on coherence (0.833 vs 0.400) not because Observable + D3 +
DuckDB-WASM are architecturally superior together, but because more "integrates_with"
and "feeds_into" edges have been curated for mature tool combinations. Newer tool stacks
(Astro + Svelte + Vite) are underrepresented in the edge catalog. **Edge curation is a
first-class data quality problem.** The validator should flag tool combinations that
appear in candidate_stacks but have zero coherence edges.

### 2. The v1 component-isolated signal is useful, not wrong

v1 correctly identifies the best tool per slot. v2 correctly identifies whether a
complete stack holds together. They answer different questions. The workflow should be:
v1 first (to generate the candidate stacks), then v2 (to evaluate them as units).
The tool-landscape spec already follows this workflow.

### 3. boundary_friction needs per-stack values, not per-spec

The serving → presentation boundary friction is stack-dependent. Observable Framework
imposes Arrow proxy impedance and implicit reactivity. Astro + Svelte components have
explicit state and standard DOM APIs. Keeping one global `data_flow.boundaries` block
forces all candidates to share the same friction score, masking the key differentiator.

**Fix:** Extend the spec schema with `candidate_stacks[name].boundary_overrides`:
```yaml
candidate_stacks:
  astro_svelte:
    boundary_overrides:
      - between: [serving, presentation]
        friction: medium
        notes: "Svelte components have explicit reactive declarations, no Arrow proxy"
```

### 4. migration_roi needs to be wired

The Phase C engine has the ROI formula and spec schema but doesn't yet compute
`savings / cost` from `migration.ongoing_friction` and `migration.one_time`. Until it
does, the ROI dimension is noise (constant 0.5). This is the highest-priority Phase F
task if the engine is to give actionable migration decisions.

### 5. The framework correctly surfaces the tradeoff

Even with its current limitations, shop-stack makes the decision structure explicit:

> "The new stack fits your requirements better (fitness +0.224) and fits your roadmap
> better (time_horizon +0.166), but the data says these tools are less proven together
> (coherence -0.433). You're betting on a stack the graph doesn't yet confirm."

That's an honest framing of a real decision. The recommendation to "stay put" (current
wins by 0.016) is fragile — adding one curated edge between Astro and DuckDB-WASM, or
correcting the boundary_friction for the presentation layer, would flip it. The narrow
margin is itself informative: this is a genuine close call, not a clear "don't switch."

---

## Summary Verdict

| Question | Answer |
|----------|--------|
| Does v2 flip v1's recommendation? | No — both lean toward Astro+Svelte |
| Does v2 narrow the confidence? | Yes — from "obvious winner" to "close call" |
| Why does current still win in v2? | Coherence scoring favors mature edge data |
| Is the current stack actually better? | Probably not — fitness and time_horizon both favor Astro+Svelte |
| What one change would flip the v2 result? | Curate 2-3 edges for Astro↔DuckDB-WASM and Svelte↔D3.js |
| What one engine change matters most? | Wire migration_roi to spec's ongoing_friction economics |
