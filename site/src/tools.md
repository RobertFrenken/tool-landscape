---
title: Tool Table
---

# Tool Table

Browse and filter all ${toolCount} tools.

```js
const db = DuckDBClient.of({
  tools: FileAttachment("data/tools.parquet"),
  edges: FileAttachment("data/edges.parquet"),
});
```

```js
const toolCount = (await db.query("SELECT count(*) as n FROM tools")).get(0).n;
```

```js
const categories = await db.query(`
  SELECT DISTINCT unnest(string_split(categories, ',')) as cat
  FROM tools WHERE categories != ''
  ORDER BY cat
`);
const categoryList = ["All", ...Array.from(categories, d => d.cat)];
const categoryFilter = view(Inputs.select(categoryList, {label: "Category", value: "All"}));
```

```js
const momentumFilter = view(Inputs.select(
  ["All", "growing", "stable", "declining"],
  {label: "Momentum", value: "All"}
));
```

```js
const ceilingFilter = view(Inputs.select(
  ["All", "extensive", "high", "medium", "low"],
  {label: "Ceiling", value: "All"}
));
```

```js
const searchInput = view(Inputs.text({label: "Search", placeholder: "Tool name..."}));
```

```js
const catWhere = categoryFilter === "All" ? "" : `AND categories LIKE '%${categoryFilter}%'`;
const momWhere = momentumFilter === "All" ? "" : `AND community_momentum = '${momentumFilter}'`;
const ceilWhere = ceilingFilter === "All" ? "" : `AND capability_ceiling = '${ceilingFilter}'`;
const searchWhere = searchInput ? `AND lower(name) LIKE '%${searchInput.toLowerCase()}%'` : "";

const filteredTools = await db.query(`
  SELECT name, maturity, community_momentum, capability_ceiling,
         categories, language_ecosystem, open_source, summary
  FROM tools
  WHERE 1=1 ${catWhere} ${momWhere} ${ceilWhere} ${searchWhere}
  ORDER BY name
`);
```

**${filteredTools.numRows} tools** matching filters.

```js
Inputs.table(filteredTools, {
  columns: [
    "name", "maturity", "community_momentum",
    "capability_ceiling", "categories", "open_source",
  ],
  header: {
    name: "Name",
    maturity: "Maturity",
    community_momentum: "Momentum",
    capability_ceiling: "Ceiling",
    categories: "Categories",
    open_source: "OSS",
  },
  width: {
    name: 180,
    categories: 200,
  },
  rows: 30,
})
```
