---
title: Dashboard
---

# Tool Landscape

Mapping 1,157 developer tools across 9 domains — ML/data, frontend, backend, LLM, visualization, documents, gamedev, and platforms.

```js
const db = DuckDBClient.of({
  tools: FileAttachment("data/tools.parquet"),
  edges: FileAttachment("data/edges.parquet"),
  neighborhoods: FileAttachment("data/neighborhoods.parquet"),
  projects: FileAttachment("data/projects.parquet"),
});
```

```js
const toolCount = (await db.query("SELECT count(*) as n FROM tools")).get(0).n;
const edgeCount = (await db.query("SELECT count(*) as n FROM edges")).get(0).n;
const nbrCount = (await db.query("SELECT count(DISTINCT neighborhood) as n FROM neighborhoods")).get(0).n;
const projectCount = (await db.query("SELECT count(DISTINCT project) as n FROM projects")).get(0).n;
```

<div class="grid-3">
  <div class="card">
    <div class="metric">${toolCount}</div>
    <div class="metric-label">Tools</div>
  </div>
  <div class="card">
    <div class="metric">${edgeCount}</div>
    <div class="metric-label">Edges</div>
  </div>
  <div class="card">
    <div class="metric">${nbrCount}</div>
    <div class="metric-label">Neighborhoods</div>
  </div>
  <div class="card">
    <div class="metric">${projectCount}</div>
    <div class="metric-label">Projects</div>
  </div>
</div>

## Tools by Maturity

```js
const maturityData = await db.query(`
  SELECT maturity, count(*) as count
  FROM tools
  WHERE maturity IS NOT NULL
  GROUP BY maturity
  ORDER BY count DESC
`);
```

```js
Plot.plot({
  marginLeft: 100,
  x: {label: "Count"},
  y: {label: null},
  marks: [
    Plot.barX(maturityData, {
      x: "count",
      y: "maturity",
      fill: "#7c3aed",
      sort: {y: "-x"},
      tip: true,
    }),
    Plot.ruleX([0]),
  ],
})
```

## Community Momentum

```js
const momentumData = await db.query(`
  SELECT community_momentum as momentum, count(*) as count
  FROM tools
  WHERE community_momentum IS NOT NULL
  GROUP BY community_momentum
  ORDER BY count DESC
`);
```

```js
Plot.plot({
  marginLeft: 80,
  x: {label: "Count"},
  y: {label: null},
  color: {
    domain: ["growing", "stable", "declining"],
    range: ["#2da44e", "#4269d0", "#cf222e"],
  },
  marks: [
    Plot.barX(momentumData, {
      x: "count",
      y: "momentum",
      fill: "momentum",
      tip: true,
    }),
    Plot.ruleX([0]),
  ],
})
```

## Capability Ceiling Distribution

```js
const ceilingData = await db.query(`
  SELECT capability_ceiling as ceiling, count(*) as count
  FROM tools
  WHERE capability_ceiling IS NOT NULL AND capability_ceiling != 'unknown'
  GROUP BY capability_ceiling
  ORDER BY count DESC
`);
```

```js
Plot.plot({
  marginLeft: 80,
  x: {label: "Count"},
  y: {label: null},
  marks: [
    Plot.barX(ceilingData, {
      x: "count",
      y: "ceiling",
      fill: "#7c3aed",
      sort: {y: "-x"},
      tip: true,
    }),
    Plot.ruleX([0]),
  ],
})
```

## Edge Types

```js
const edgeTypeData = await db.query(`
  SELECT relation, count(*) as count
  FROM edges
  GROUP BY relation
  ORDER BY count DESC
`);
```

```js
Plot.plot({
  marginLeft: 120,
  x: {label: "Count"},
  y: {label: null},
  marks: [
    Plot.barX(edgeTypeData, {
      x: "count",
      y: "relation",
      fill: "#7c3aed",
      sort: {y: "-x"},
      tip: true,
    }),
    Plot.ruleX([0]),
  ],
})
```

## Top Categories

```js
const catData = await db.query(`
  SELECT unnest(string_split(categories, ',')) as category, count(*) as count
  FROM tools
  WHERE categories != ''
  GROUP BY category
  ORDER BY count DESC
  LIMIT 20
`);
```

```js
Plot.plot({
  marginLeft: 160,
  width: 800,
  x: {label: "Count"},
  y: {label: null},
  marks: [
    Plot.barX(catData, {
      x: "count",
      y: "category",
      fill: "#7c3aed",
      sort: {y: "-x"},
      tip: true,
    }),
    Plot.ruleX([0]),
  ],
})
```
