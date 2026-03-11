---
title: Graph Explorer
---

# Graph Explorer

```js
const db = DuckDBClient.of({
  tools: FileAttachment("data/tools.parquet"),
  edges: FileAttachment("data/edges.parquet"),
  neighborhoods: FileAttachment("data/neighborhoods.parquet"),
});
```

```js
const edgeTypes = ["All", "requires", "replaces", "wraps", "often_paired", "feeds_into", "integrates_with"];
const edgeFilter = view(Inputs.select(edgeTypes, {label: "Edge type", value: "requires"}));
const colorBy = view(Inputs.select(
  ["neighborhood", "maturity", "community_momentum", "capability_ceiling"],
  {label: "Color by", value: "neighborhood"}
));
const searchTool = view(Inputs.text({label: "Highlight", placeholder: "Type tool name..."}));
```

```js
const toolRows = await db.query(`
  SELECT t.name, t.maturity, t.community_momentum, t.capability_ceiling,
         t.categories, n.neighborhood
  FROM tools t
  LEFT JOIN neighborhoods n ON n.tool_name = t.name
`);
const tools = Array.from(toolRows);

const edgeWhere = edgeFilter === "All" ? "" : `WHERE relation = '${edgeFilter}'`;
const edgeRows = await db.query(`SELECT source, target, relation FROM edges ${edgeWhere}`);
const edges = Array.from(edgeRows);

const connectedNodes = new Set();
for (const e of edges) { connectedNodes.add(e.source); connectedNodes.add(e.target); }

const filteredTools = connectedNodes.size > 0
  ? tools.filter(t => connectedNodes.has(t.name))
  : tools;
```

**${filteredTools.length} nodes**, **${edges.length} edges** (${edgeFilter})

```js
// Color helpers
const neighborhoods = [...new Set(filteredTools.map(t => t.neighborhood).filter(Boolean))];
const nbrColorScale = d3.scaleOrdinal(d3.schemeTableau10).domain(neighborhoods);

const momentumColors = {growing: "#2da44e", stable: "#4269d0", declining: "#cf222e"};
const maturityColors = {production: "#2da44e", growth: "#4269d0", early: "#f0883e", experimental: "#8b949e", archived: "#cf222e"};
const ceilingColors = {extensive: "#2da44e", high: "#4269d0", medium: "#f0883e", low: "#8b949e"};

function getColor(t) {
  const val = t[colorBy];
  if (!val) return "#ddd";
  if (colorBy === "community_momentum") return momentumColors[val] || "#ddd";
  if (colorBy === "maturity") return maturityColors[val] || "#ddd";
  if (colorBy === "capability_ceiling") return ceilingColors[val] || "#ddd";
  return nbrColorScale(val);
}

const edgeColor = {
  requires: "#cf222e", replaces: "#f0883e", wraps: "#8b949e",
  often_paired: "#4269d0", feeds_into: "#2da44e", integrates_with: "#c8ccd1",
};
```

```js
// Canvas-based force graph for performance
const width = document.querySelector("main").offsetWidth || 1100;
const height = Math.max(600, window.innerHeight - 300);
const dpr = window.devicePixelRatio || 1;

const canvas = document.createElement("canvas");
canvas.width = width * dpr;
canvas.height = height * dpr;
canvas.style.width = `${width}px`;
canvas.style.height = `${height}px`;
canvas.style.borderRadius = "4px";
const ctx = canvas.getContext("2d");
ctx.scale(dpr, dpr);

const nodeMap = new Map(filteredTools.map((t, i) => [t.name, i]));
const nodes = filteredTools.map(t => ({
  id: t.name, ...t,
  color: getColor(t),
  highlighted: searchTool && t.name.toLowerCase().includes(searchTool.toLowerCase()),
}));
const links = edges
  .filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))
  .map(e => ({source: e.source, target: e.target, relation: e.relation}));

const simulation = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).id(d => d.id).distance(50))
  .force("charge", d3.forceManyBody().strength(-40).distanceMax(300))
  .force("center", d3.forceCenter(width / 2, height / 2))
  .force("collision", d3.forceCollide(6))
  .alphaDecay(0.03);

function draw() {
  ctx.clearRect(0, 0, width, height);

  // Draw edges
  ctx.globalAlpha = 0.3;
  for (const l of links) {
    ctx.beginPath();
    ctx.moveTo(l.source.x, l.source.y);
    ctx.lineTo(l.target.x, l.target.y);
    ctx.strokeStyle = edgeColor[l.relation] || "#c8ccd1";
    ctx.lineWidth = l.relation === "integrates_with" ? 0.3 : 0.8;
    ctx.stroke();
  }

  // Draw nodes
  ctx.globalAlpha = 1;
  for (const n of nodes) {
    const r = n.highlighted ? 7 : 3;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = n.color;
    ctx.fill();
    if (n.highlighted) {
      ctx.strokeStyle = "#000";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }

  // Labels for highlighted nodes
  ctx.fillStyle = "#24292f";
  ctx.font = "11px sans-serif";
  for (const n of nodes) {
    if (n.highlighted) {
      ctx.fillText(n.id, n.x + 9, n.y + 4);
    }
  }
}

simulation.on("tick", draw);

// Tooltip on hover
let tooltip = null;
canvas.addEventListener("mousemove", (event) => {
  const rect = canvas.getBoundingClientRect();
  const mx = event.clientX - rect.left;
  const my = event.clientY - rect.top;
  let found = null;
  for (const n of nodes) {
    const dx = n.x - mx, dy = n.y - my;
    if (dx * dx + dy * dy < 64) { found = n; break; }
  }
  if (found) {
    canvas.title = `${found.id}\n${found.categories || ""}\n${found.neighborhood || ""}`;
    canvas.style.cursor = "pointer";
  } else {
    canvas.title = "";
    canvas.style.cursor = "default";
  }
});

// Drag interaction
d3.select(canvas).call(d3.drag()
  .subject((event) => {
    const rect = canvas.getBoundingClientRect();
    const mx = event.x, my = event.y;
    for (const n of nodes) {
      const dx = n.x - mx, dy = n.y - my;
      if (dx * dx + dy * dy < 64) return n;
    }
    return null;
  })
  .on("start", (event, d) => {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x; d.fy = d.y;
  })
  .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
  .on("end", (event, d) => {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null; d.fy = null;
  })
);

display(canvas);
```

| Color | Type | Meaning |
|-------|------|---------|
| <span style="color:#cf222e">&#9632;</span> | requires | Hard dependency |
| <span style="color:#f0883e">&#9632;</span> | replaces | Direct alternative |
| <span style="color:#8b949e">&#9632;</span> | wraps | Higher-level API |
| <span style="color:#4269d0">&#9632;</span> | often_paired | Frequently co-adopted |
| <span style="color:#2da44e">&#9632;</span> | feeds_into | Data flows A→B |
| <span style="color:#c8ccd1">&#9632;</span> | integrates_with | Integration exists |
