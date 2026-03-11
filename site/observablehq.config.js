export default {
  title: "Tool Landscape",
  root: "src",
  output: "dist",
  theme: ["dashboard", "slate", "wide"],
  pages: [
    {name: "Dashboard", path: "/"},
    {name: "Graph Explorer", path: "/graph"},
    {name: "Tool Table", path: "/tools"},
    {name: "Project Coverage", path: "/coverage"},
    {name: "Compare", path: "/compare"},
  ],
  head: `<style>
  .card {
    background: var(--theme-background-alt);
    border: 1px solid color-mix(in srgb, var(--theme-foreground) 10%, transparent);
    border-radius: 6px;
    padding: 1rem;
  }
  .metric { font-size: 2rem; font-weight: 700; color: var(--theme-foreground-focus); }
  .metric-label { font-size: 0.8rem; color: var(--theme-foreground-muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
</style>`,
};
