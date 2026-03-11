---
title: Compare
---

# Compare Tools

Select two tools to compare across all dimensions.

```js
const db = DuckDBClient.of({
  tools: FileAttachment("data/tools.parquet"),
  edges: FileAttachment("data/edges.parquet"),
  neighborhoods: FileAttachment("data/neighborhoods.parquet"),
});
```

```js
const toolNames = await db.query("SELECT name FROM tools ORDER BY name");
const nameList = Array.from(toolNames, d => d.name);
const toolA = view(Inputs.select(nameList, {label: "Tool A", value: nameList[0]}));
const toolB = view(Inputs.select(nameList, {label: "Tool B", value: nameList[1]}));
```

```js
const comparison = await db.query(`
  SELECT * FROM tools WHERE name IN ('${toolA}', '${toolB}')
`);
const rows = Array.from(comparison);
const a = rows.find(r => r.name === toolA) || {};
const b = rows.find(r => r.name === toolB) || {};
```

```js
const nbrA = await db.query(`SELECT neighborhood FROM neighborhoods WHERE tool_name = '${toolA}'`);
const nbrB = await db.query(`SELECT neighborhood FROM neighborhoods WHERE tool_name = '${toolB}'`);
```

## Side-by-Side

| Dimension | ${toolA} | ${toolB} |
|-----------|----------|----------|
| **Open Source** | ${a.open_source ? "Yes" : "No"} | ${b.open_source ? "Yes" : "No"} |
| **License** | ${a.license || "—"} | ${b.license || "—"} |
| **Maturity** | ${a.maturity || "—"} | ${b.maturity || "—"} |
| **Governance** | ${a.governance || "—"} | ${b.governance || "—"} |
| **Momentum** | ${a.community_momentum || "—"} | ${b.community_momentum || "—"} |
| **Ceiling** | ${a.capability_ceiling || "—"} | ${b.capability_ceiling || "—"} |
| **HPC** | ${a.hpc_compatible || "—"} | ${b.hpc_compatible || "—"} |
| **Migration Cost** | ${a.migration_cost || "—"} | ${b.migration_cost || "—"} |
| **Lock-in Risk** | ${a.lock_in_risk || "—"} | ${b.lock_in_risk || "—"} |
| **Docs Quality** | ${a.documentation_quality || "—"} | ${b.documentation_quality || "—"} |
| **Interoperability** | ${a.interoperability || "—"} | ${b.interoperability || "—"} |
| **Python Native** | ${a.python_native ? "Yes" : "No"} | ${b.python_native ? "Yes" : "No"} |
| **Offline Capable** | ${a.offline_capable ? "Yes" : "No"} | ${b.offline_capable ? "Yes" : "No"} |
| **Categories** | ${a.categories || "—"} | ${b.categories || "—"} |
| **Languages** | ${a.language_ecosystem || "—"} | ${b.language_ecosystem || "—"} |
| **Neighborhood** | ${nbrA.numRows > 0 ? nbrA.get(0).neighborhood : "—"} | ${nbrB.numRows > 0 ? nbrB.get(0).neighborhood : "—"} |

## Shared Edges

```js
const sharedEdges = await db.query(`
  SELECT source, target, relation, evidence
  FROM edges
  WHERE (source = '${toolA}' AND target = '${toolB}')
     OR (source = '${toolB}' AND target = '${toolA}')
`);
```

```js
if (sharedEdges.numRows > 0) {
  display(Inputs.table(sharedEdges, {
    columns: ["source", "target", "relation", "evidence"],
    rows: 10,
  }));
} else {
  display(html`<p><em>No direct edges between ${toolA} and ${toolB}.</em></p>`);
}
```

## ${toolA} — Summary

${a.summary || "No summary available."}

## ${toolB} — Summary

${b.summary || "No summary available."}
