export default {
  title: "Tool Landscape",
  root: "src",
  output: "dist",
  theme: ["dashboard", "alt"],
  pages: [
    {name: "Dashboard", path: "/"},
    {name: "Graph Explorer", path: "/graph"},
    {name: "Tool Table", path: "/tools"},
    {name: "Project Coverage", path: "/coverage"},
    {name: "Compare", path: "/compare"},
  ],
  head: `<style>
  :root {
    --accent: #4269d0;
    --accent-light: #97bbf5;
    --bg-card: #fafbfc;
  }
  .card { background: var(--bg-card); border: 1px solid #e1e4e8; border-radius: 8px; padding: 1rem; }
  .metric { font-size: 2rem; font-weight: 700; color: var(--accent); }
  .metric-label { font-size: 0.85rem; color: #586069; text-transform: uppercase; }
  .grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
</style>`,
};
