---
title: Project Coverage
---

# Project Coverage

View capability coverage for each project — what tools are in use, what the ceiling requirements are, and when to re-evaluate.

```js
const db = DuckDBClient.of({
  projects: FileAttachment("data/projects.parquet"),
  tools: FileAttachment("data/tools.parquet"),
});
```

```js
const projectNames = await db.query("SELECT DISTINCT project FROM projects ORDER BY project");
const projectList = Array.from(projectNames, d => d.project);
const selectedProject = view(Inputs.select(projectList, {label: "Project"}));
```

```js
const projectInfo = await db.query(`
  SELECT DISTINCT project, description, team_size_ceiling,
         env_primary, gpu_required, internet_on_compute, shared_filesystem
  FROM projects
  WHERE project = '${selectedProject}'
`);
const info = projectInfo.get(0);
```

## ${info.project}

${info.description}

<div class="grid-3">
  <div class="card">
    <div class="metric">${info.team_size_ceiling || "—"}</div>
    <div class="metric-label">Team Size Ceiling</div>
  </div>
  <div class="card">
    <div class="metric">${info.env_primary || "—"}</div>
    <div class="metric-label">Primary Env</div>
  </div>
  <div class="card">
    <div class="metric">${info.gpu_required ? "Yes" : "No"}</div>
    <div class="metric-label">GPU Required</div>
  </div>
</div>

## Capabilities

```js
const capabilities = await db.query(`
  SELECT capability, capability_description, current_tool,
         triggers, notes
  FROM projects
  WHERE project = '${selectedProject}'
  ORDER BY capability
`);
```

```js
Inputs.table(capabilities, {
  columns: ["capability", "current_tool", "capability_description", "notes"],
  header: {
    capability: "Capability",
    current_tool: "Current Tool",
    capability_description: "Description",
    notes: "Notes",
  },
  width: {
    capability: 180,
    current_tool: 150,
    capability_description: 250,
  },
  rows: 20,
})
```

## Re-evaluation Triggers

```js
const triggers = await db.query(`
  SELECT capability, triggers, current_tool
  FROM projects
  WHERE project = '${selectedProject}'
  AND triggers IS NOT NULL AND triggers != ''
  ORDER BY capability
`);
```

```js
Inputs.table(triggers, {
  columns: ["capability", "current_tool", "triggers"],
  header: {
    capability: "Capability",
    current_tool: "Current Tool",
    triggers: "Triggers",
  },
  rows: 20,
})
```
