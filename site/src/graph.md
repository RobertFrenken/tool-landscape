---
title: Graph Explorer
---

# Graph Explorer

Interactive visualization of tool relationships. Nodes are tools, edges represent typed relationships (requires, replaces, wraps, often_paired, feeds_into, integrates_with).

```js
const db = DuckDBClient.of({
  tools: FileAttachment("data/tools.parquet"),
  edges: FileAttachment("data/edges.parquet"),
  neighborhoods: FileAttachment("data/neighborhoods.parquet"),
});
```

```js
const edgeTypes = ["All", "requires", "replaces", "wraps", "often_paired", "feeds_into", "integrates_with"];
const edgeFilter = view(Inputs.select(edgeTypes, {label: "Edge type", value: "All"}));
```

```js
const colorBy = view(Inputs.select(
  ["neighborhood", "maturity", "community_momentum", "capability_ceiling"],
  {label: "Color by", value: "neighborhood"}
));
```

```js
const searchTool = view(Inputs.text({label: "Highlight tool", placeholder: "Type tool name..."}));
```

```js
// Load all tools with neighborhood info
const toolRows = await db.query(`
  SELECT t.name, t.maturity, t.community_momentum, t.capability_ceiling,
         t.categories, t.open_source,
         n.neighborhood
  FROM tools t
  LEFT JOIN (
    SELECT tool_name, neighborhood
    FROM neighborhoods
  ) n ON n.tool_name = t.name
`);

const tools = Array.from(toolRows);

// Load edges (filtered)
const edgeWhere = edgeFilter === "All" ? "" : `WHERE relation = '${edgeFilter}'`;
const edgeRows = await db.query(`SELECT source, target, relation FROM edges ${edgeWhere}`);
const edges = Array.from(edgeRows);

// Build connected node set
const connectedNodes = new Set();
for (const e of edges) {
  connectedNodes.add(e.source);
  connectedNodes.add(e.target);
}

// Only show tools that have edges in current filter
const filteredTools = connectedNodes.size > 0
  ? tools.filter(t => connectedNodes.has(t.name))
  : tools;
```

**${filteredTools.length} nodes**, **${edges.length} edges** (${edgeFilter})

```js
// Build color scale
const neighborhoods = [...new Set(filteredTools.map(t => t.neighborhood).filter(Boolean))];
const colorScale = d3.scaleOrdinal(d3.schemeTableau10);

function getColor(tool) {
  const val = tool[colorBy];
  if (!val) return "#ccc";
  if (colorBy === "community_momentum") {
    return {growing: "#2da44e", stable: "#4269d0", declining: "#cf222e"}[val] || "#ccc";
  }
  if (colorBy === "maturity") {
    return {production: "#2da44e", growth: "#4269d0", early: "#f0883e", experimental: "#8b949e", archived: "#cf222e"}[val] || "#ccc";
  }
  if (colorBy === "capability_ceiling") {
    return {extensive: "#2da44e", high: "#4269d0", medium: "#f0883e", low: "#8b949e"}[val] || "#ccc";
  }
  return colorScale(val);
}
```

```js
// Force-directed graph using D3
const width = 900;
const height = 600;

const nodeMap = new Map(filteredTools.map((t, i) => [t.name, i]));
const nodes = filteredTools.map(t => ({
  id: t.name,
  ...t,
  color: getColor(t),
  highlighted: searchTool && t.name.toLowerCase().includes(searchTool.toLowerCase()),
}));
const links = edges
  .filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))
  .map(e => ({source: e.source, target: e.target, relation: e.relation}));

const simulation = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).id(d => d.id).distance(60))
  .force("charge", d3.forceManyBody().strength(-30))
  .force("center", d3.forceCenter(width / 2, height / 2))
  .force("collision", d3.forceCollide(8));

const svg = d3.create("svg")
  .attr("viewBox", [0, 0, width, height])
  .attr("width", width)
  .attr("height", height)
  .style("max-width", "100%")
  .style("border", "1px solid #e1e4e8")
  .style("border-radius", "8px");

// Edge color by type
const edgeColor = {
  requires: "#cf222e",
  replaces: "#f0883e",
  wraps: "#8b949e",
  often_paired: "#4269d0",
  feeds_into: "#2da44e",
  integrates_with: "#d1d5da",
};

const link = svg.append("g")
  .selectAll("line")
  .data(links)
  .join("line")
  .attr("stroke", d => edgeColor[d.relation] || "#d1d5da")
  .attr("stroke-opacity", 0.5)
  .attr("stroke-width", d => d.relation === "integrates_with" ? 0.5 : 1.5);

const node = svg.append("g")
  .selectAll("circle")
  .data(nodes)
  .join("circle")
  .attr("r", d => d.highlighted ? 8 : 4)
  .attr("fill", d => d.color)
  .attr("stroke", d => d.highlighted ? "#000" : "none")
  .attr("stroke-width", d => d.highlighted ? 2 : 0)
  .call(drag(simulation));

node.append("title").text(d => `${d.id}\n${d.categories || ""}\n${d.neighborhood || ""}`);

// Labels for highlighted nodes
const label = svg.append("g")
  .selectAll("text")
  .data(nodes.filter(d => d.highlighted))
  .join("text")
  .text(d => d.id)
  .attr("font-size", 10)
  .attr("dx", 10)
  .attr("dy", 3)
  .attr("fill", "#24292f");

simulation.on("tick", () => {
  link
    .attr("x1", d => d.source.x)
    .attr("y1", d => d.source.y)
    .attr("x2", d => d.target.x)
    .attr("y2", d => d.target.y);
  node
    .attr("cx", d => d.x)
    .attr("cy", d => d.y);
  label
    .attr("x", d => d.x)
    .attr("y", d => d.y);
});

function drag(simulation) {
  return d3.drag()
    .on("start", (event, d) => {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
    .on("end", (event, d) => {
      if (!event.active) simulation.alphaTarget(0);
      d.fx = null; d.fy = null;
    });
}

display(svg.node());
```

### Edge Type Legend

| Color | Type | Meaning |
|-------|------|---------|
| <span style="color:#cf222e">&#9632;</span> | requires | Hard dependency |
| <span style="color:#f0883e">&#9632;</span> | replaces | Direct alternative |
| <span style="color:#8b949e">&#9632;</span> | wraps | Higher-level API |
| <span style="color:#4269d0">&#9632;</span> | often_paired | Frequently co-adopted |
| <span style="color:#2da44e">&#9632;</span> | feeds_into | Data flows A→B |
| <span style="color:#d1d5da">&#9632;</span> | integrates_with | Integration exists |
