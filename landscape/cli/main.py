"""CLI entry point for the tool-landscape framework."""

from __future__ import annotations

import argparse
import sys


def cmd_import(args: argparse.Namespace) -> None:
    """Import seed data into DuckDB."""
    from landscape.db.connection import get_db
    from landscape.db.migrate import run_migration

    con = get_db()
    results = run_migration(con)
    con.close()

    print("Migration complete:")
    for table, count in results.items():
        print(f"  {table}: {count} rows")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show database statistics."""
    from landscape.db.connection import DEFAULT_DB_PATH, get_db

    if not DEFAULT_DB_PATH.exists():
        print("Database not found. Run: landscape import --seed")
        sys.exit(1)

    con = get_db(read_only=True)
    tables = [
        "tools",
        "tool_metrics",
        "edges",
        "neighborhoods",
        "neighborhood_members",
        "projects",
        "capabilities",
        "fitness",
        "validation_flags",
        "migration_history",
    ]
    print(f"Database: {DEFAULT_DB_PATH} ({DEFAULT_DB_PATH.stat().st_size / 1024:.0f} KB)\n")
    for table in tables:
        try:
            count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            print(f"  {table}: {count} rows")
        except Exception:
            print(f"  {table}: (not created)")
    con.close()


def cmd_query_tools(args: argparse.Namespace) -> None:
    """Query tools with optional filters."""
    from landscape.db.connection import get_db

    con = get_db(read_only=True)

    conditions = []
    params = []
    param_idx = 1

    if args.category:
        conditions.append(f"list_contains(categories, ${param_idx})")
        params.append(args.category)
        param_idx += 1
    if args.hpc:
        conditions.append(f"hpc_compatible = ${param_idx}::hpc_compat")
        params.append(args.hpc)
        param_idx += 1
    if args.momentum:
        conditions.append(f"community_momentum = ${param_idx}::momentum")
        params.append(args.momentum)
        param_idx += 1
    if args.ceiling:
        conditions.append(f"capability_ceiling = ${param_idx}::tier")
        params.append(args.ceiling)
        param_idx += 1
    if args.used_by:
        conditions.append(f"list_contains(used_by, ${param_idx})")
        params.append(args.used_by)
        param_idx += 1

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT name, categories, capability_ceiling, community_momentum, summary FROM tools{where} ORDER BY name"  # noqa: S608, E501

    rows = con.execute(query, params).fetchall()
    con.close()

    if not rows:
        print("No tools found matching filters.")
        return

    print(f"{'Name':<30} {'Ceiling':<12} {'Momentum':<10} {'Categories'}")
    print("-" * 90)
    for name, cats, ceiling, momentum, _summary in rows:
        cats_str = ", ".join(cats[:3]) if cats else ""
        print(f"{name:<30} {ceiling or 'unknown':<12} {momentum or 'unknown':<10} {cats_str}")
    print(f"\n{len(rows)} tools")


def cmd_inspect(args: argparse.Namespace) -> None:
    """Inspect a single tool: details + edges + neighborhoods."""
    from landscape.db.connection import get_db

    con = get_db(read_only=True)

    # Tool details
    tool = con.execute("SELECT * FROM tools WHERE lower(name) = lower($1)", [args.name]).fetchone()
    if not tool:
        print(f"Tool '{args.name}' not found.")
        con.close()
        sys.exit(1)

    cols = [desc[0] for desc in con.description]
    tool_dict = dict(zip(cols, tool))
    tool_id = tool_dict["tool_id"]

    print(f"=== {tool_dict['name']} ===")
    print(f"URL: {tool_dict['url']}")
    print(f"License: {tool_dict['license']} | Open Source: {tool_dict['open_source']}")
    print(f"Categories: {', '.join(tool_dict['categories'] or [])}")
    print(f"Languages: {', '.join(tool_dict['language_ecosystem'] or [])}")
    print()
    print(f"HPC: {tool_dict['hpc_compatible']}  |  Collab: {tool_dict['collaboration_model']}")
    print(
        f"Ceiling: {tool_dict['capability_ceiling']}  |  "
        f"Migration likelihood: {tool_dict['migration_likelihood']}"
    )
    print(f"Lock-in: {tool_dict['lock_in_risk']}  |  Momentum: {tool_dict['community_momentum']}")
    print(
        f"Docs: {tool_dict['documentation_quality']}  |  Overhead: {tool_dict['resource_overhead']}"
    )
    print(f"Composite: {tool_dict['composite_tool']}  |  Offline: {tool_dict['offline_capable']}")
    if tool_dict.get("used_by"):
        print(f"Used by: {', '.join(tool_dict['used_by'])}")
    if tool_dict.get("summary"):
        print(f"\nSummary: {tool_dict['summary']}")

    # Edges
    edges = con.execute(
        """
        SELECT e.relation, t2.name, e.evidence
        FROM edges e JOIN tools t2 ON e.target_id = t2.tool_id
        WHERE e.source_id = $1
        UNION ALL
        SELECT e.relation, t2.name, e.evidence
        FROM edges e JOIN tools t2 ON e.source_id = t2.tool_id
        WHERE e.target_id = $1
        """,
        [tool_id],
    ).fetchall()

    if edges:
        print(f"\nEdges ({len(edges)}):")
        for relation, other_name, evidence in edges:
            ev = f" — {evidence}" if evidence else ""
            print(f"  [{relation}] {other_name}{ev}")

    con.close()


def cmd_resolve(args: argparse.Namespace) -> None:
    """Resolve tool URLs to registry identifiers."""
    import asyncio
    import json
    from pathlib import Path

    from landscape.analysis.resolve import (
        IDENTIFIERS_PATH,
        load_identifiers,
        resolve_all,
        save_identifiers,
    )

    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seed"
    tools = []
    for catalog in sorted(seed_dir.glob("*_catalog*.json")):
        tools.extend(json.loads(catalog.read_text()))
    existing = load_identifiers()

    results = asyncio.run(resolve_all(tools, existing=existing, skip_resolved=not args.force))
    save_identifiers(results)

    # Summary
    gh = sum(1 for v in results.values() if v.get("github_repo"))
    pypi = sum(1 for v in results.values() if v.get("pypi_package"))
    npm = sum(1 for v in results.values() if v.get("npm_package"))
    print(f"Resolved {len(results)} tools:")
    print(f"  GitHub repos:  {gh}")
    print(f"  PyPI packages: {pypi}")
    print(f"  npm packages:  {npm}")
    print(f"Saved to {IDENTIFIERS_PATH}")


def cmd_metrics_collect(args: argparse.Namespace) -> None:
    """Collect metrics from external APIs."""
    import logging
    import os

    from landscape.analysis.metrics import run_collect
    from landscape.db.connection import get_db

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    con = get_db()
    sources = [args.source] if args.source else None
    tool_names = [args.tool] if args.tool else None
    token = os.environ.get("GITHUB_TOKEN")

    results = run_collect(con, sources=sources, tool_names=tool_names, github_token=token)
    con.close()

    print("Metrics collected:")
    for source, count in results.items():
        print(f"  {source}: {count} rows")


def cmd_metrics_show(args: argparse.Namespace) -> None:
    """Show metrics for a tool."""
    from landscape.db.connection import get_db

    con = get_db(read_only=True)

    rows = con.execute(
        """
        SELECT m.metric_name, m.value, m.source, m.measured_at, m.metadata
        FROM tool_metrics m
        JOIN tools t ON m.tool_id = t.tool_id
        WHERE lower(t.name) = lower($1)
        ORDER BY m.source, m.metric_name, m.measured_at DESC
        """,
        [args.name],
    ).fetchall()
    con.close()

    if not rows:
        print(f"No metrics found for '{args.name}'.")
        return

    print(f"{'Metric':<30} {'Value':>12} {'Source':<15} {'Measured'}")
    print("-" * 80)
    for metric_name, value, source, measured_at, _meta in rows:
        val_str = f"{value:,.0f}" if value == int(value) else f"{value:.2f}"
        date_str = measured_at.strftime("%Y-%m-%d") if measured_at else ""
        print(f"{metric_name:<30} {val_str:>12} {source:<15} {date_str}")


def cmd_coverage(args: argparse.Namespace) -> None:
    """Show coverage report for a project."""
    from landscape.db.connection import get_db

    con = get_db(read_only=True)

    project = con.execute(
        "SELECT project_id, name, team_size_ceiling FROM projects WHERE lower(name) = lower($1)",
        [args.project],
    ).fetchone()
    if not project:
        print(f"Project '{args.project}' not found.")
        con.close()
        sys.exit(1)

    project_id, proj_name, _team_size = project

    caps = con.execute(
        """
        SELECT c.name, c.description, t.name as tool_name,
               c.ceiling_requirements, c.triggers, c.notes
        FROM capabilities c
        LEFT JOIN tools t ON c.current_tool_id = t.tool_id
        WHERE c.project_id = $1
        ORDER BY c.name
        """,
        [project_id],
    ).fetchall()

    print(f"=== Coverage: {proj_name} ===\n")
    for cap_name, desc, tool_name, _reqs, triggers, notes in caps:
        tool_str = tool_name or "(none)"
        print(f"  {cap_name}: {tool_str}")
        if notes:
            print(f"    NOTE: {notes}")

    print(f"\n{len(caps)} capabilities")
    con.close()


def cmd_fitness_score(args: argparse.Namespace) -> None:
    """Score all tools against capabilities for a project."""
    from landscape.analysis.fitness import persist_scores, score_project
    from landscape.db.connection import get_db

    con = get_db()
    try:
        results = score_project(con, args.project, top_n=args.top_n)
    except ValueError as e:
        print(str(e))
        con.close()
        sys.exit(1)

    all_scores = []
    for cap_name, scored in results.items():
        print(f"\n=== {cap_name} (top {len(scored)}) ===")
        print(f"  {'Tool':<30} {'Fitness':>8} {'Ceiling%':>9} {'Floor%':>8}")
        print(f"  {'-' * 30} {'-' * 8} {'-' * 9} {'-' * 8}")
        for s in scored:
            print(
                f"  {s.tool_name:<30} {s.overall_fitness:>7.1f}% "
                f"{s.ceiling_coverage:>8.1f}% {s.floor_coverage:>7.1f}%"
            )
        all_scores.extend(scored)

    if args.persist:
        count = persist_scores(con, all_scores)
        print(f"\nPersisted {count} fitness scores to database.")

    con.close()


def cmd_fitness_show(args: argparse.Namespace) -> None:
    """Show fitness scores for a specific tool across all capabilities."""
    from landscape.analysis.fitness import score_single_tool
    from landscape.db.connection import get_db

    con = get_db(read_only=True)
    try:
        results = score_single_tool(con, args.name)
    except ValueError as e:
        print(str(e))
        con.close()
        sys.exit(1)

    if not results:
        print("No capabilities found to score against.")
        con.close()
        return

    print(f"\n=== Fitness: {results[0].tool_name} ===\n")
    print(f"  {'Capability':<30} {'Fitness':>8} {'Ceiling%':>9} {'Floor%':>8}")
    print(f"  {'-' * 30} {'-' * 8} {'-' * 9} {'-' * 8}")
    for s in results:
        print(
            f"  {s.capability_name:<30} {s.overall_fitness:>7.1f}% "
            f"{s.ceiling_coverage:>8.1f}% {s.floor_coverage:>7.1f}%"
        )

    # Show component breakdown for the top capability
    top = results[0]
    print(f"\nTop match: {top.capability_name}")
    print("  Components: ", end="")
    parts = [f"{k}={v:.2f}" for k, v in sorted(top.components.items(), key=lambda x: -x[1])]
    print(", ".join(parts))
    if top.reasoning:
        print(f"  Reasoning: {top.reasoning}")

    con.close()


def cmd_neighborhoods_compute(args: argparse.Namespace) -> None:
    """Compute neighborhoods via Louvain clustering."""
    from landscape.analysis.neighborhoods import compute_neighborhoods, persist_neighborhoods
    from landscape.db.connection import get_db

    con = get_db()
    results = compute_neighborhoods(con, resolution=args.resolution, min_size=args.min_size)
    count = persist_neighborhoods(con, results, respect_pins=not args.clear)
    con.close()

    print(f"Computed {count} neighborhoods:")
    for r in results:
        print(f"  {r.name} ({r.size} tools)")


def cmd_neighborhoods_list(args: argparse.Namespace) -> None:
    """List all neighborhoods."""
    from landscape.db.connection import get_db

    con = get_db(read_only=True)
    rows = con.execute(
        """
        SELECT n.name, n.description, count(nm.tool_id) as size
        FROM neighborhoods n
        LEFT JOIN neighborhood_members nm ON n.neighborhood_id = nm.neighborhood_id
        GROUP BY n.name, n.description
        ORDER BY size DESC
        """
    ).fetchall()
    con.close()

    if not rows:
        print("No neighborhoods computed. Run: landscape neighborhoods compute")
        return

    print(f"{'Name':<40} {'Size':>5}  Description")
    print("-" * 80)
    for name, desc, size in rows:
        desc_str = (desc[:35] + "...") if desc and len(desc) > 38 else (desc or "")
        print(f"{name:<40} {size:>5}  {desc_str}")
    print(f"\n{len(rows)} neighborhoods")


def cmd_neighborhoods_show(args: argparse.Namespace) -> None:
    """Show tools in a neighborhood."""
    from landscape.analysis.neighborhoods import get_neighborhood_tools
    from landscape.db.connection import get_db

    con = get_db(read_only=True)
    tools = get_neighborhood_tools(con, args.name)
    con.close()

    if not tools:
        print(f"Neighborhood '{args.name}' not found or empty.")
        sys.exit(1)

    print(f"=== {args.name} ({len(tools)} tools) ===\n")
    for t in tools:
        cats = ", ".join(t.get("categories", [])[:3])
        print(f"  {t['name']:<30} {cats}")


def cmd_recommend(args: argparse.Namespace) -> None:
    """Recommend tools."""
    from landscape.db.connection import get_db

    con = get_db(read_only=True)

    if args.capability:
        if not args.project:
            print("--project required with --capability")
            con.close()
            sys.exit(1)
        from landscape.analysis.recommend import recommend_for_capability

        recs = recommend_for_capability(con, args.project, args.capability, top_n=args.top_n)
        print(f"=== Recommendations for {args.capability} ({args.project}) ===\n")
    elif args.tool:
        from landscape.analysis.recommend import recommend_for_tool

        recs = recommend_for_tool(con, args.tool, top_n=args.top_n)
        print(f"=== Recommendations related to {args.tool} ===\n")
    else:
        print("Provide --tool or --capability")
        con.close()
        sys.exit(1)

    if not recs:
        print("No recommendations found.")
    else:
        print(f"  {'Tool':<30} {'Score':>6}  Reason")
        print(f"  {'-' * 30} {'-' * 6}  {'-' * 30}")
        for r in recs:
            print(f"  {r.tool_name:<30} {r.score:>5.1f}  {r.reason}")

    con.close()


def cmd_export(args: argparse.Namespace) -> None:
    """Export DuckDB tables to Parquet for frontend."""
    from pathlib import Path

    from landscape.db.connection import get_db
    from landscape.export import DEFAULT_EXPORT_DIR, export_parquet

    con = get_db(read_only=True)
    out = Path(args.output) if args.output else DEFAULT_EXPORT_DIR
    results = export_parquet(con, output_dir=out)
    con.close()

    print(f"Exported to {out}:")
    for name, count in results.items():
        print(f"  {name}.parquet: {count} rows")


def cmd_validate(args: argparse.Namespace) -> None:
    """Run data quality validation checks."""
    from landscape.analysis.validate import print_validation_report, run_validation
    from landscape.db.connection import get_db

    con = get_db(read_only=True)
    flags = run_validation(con)
    con.close()
    print_validation_report(flags)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="landscape",
        description="Developer tool evaluation framework",
    )
    sub = parser.add_subparsers(dest="command")

    # import
    p_import = sub.add_parser("import", help="Import seed data into DuckDB")
    p_import.add_argument("--seed", action="store_true", help="Import from seed JSON files")
    p_import.set_defaults(func=cmd_import)

    # stats
    p_stats = sub.add_parser("stats", help="Show database statistics")
    p_stats.set_defaults(func=cmd_stats)

    # query
    p_query = sub.add_parser("query", help="Query tools")
    p_query.add_argument("--category", help="Filter by category")
    p_query.add_argument("--hpc", help="Filter by HPC compatibility")
    p_query.add_argument("--momentum", help="Filter by community momentum")
    p_query.add_argument("--ceiling", help="Filter by capability ceiling")
    p_query.add_argument("--used-by", help="Filter by project usage")
    p_query.set_defaults(func=cmd_query_tools)

    # inspect
    p_inspect = sub.add_parser("inspect", help="Inspect a tool (details + edges)")
    p_inspect.add_argument("name", help="Tool name")
    p_inspect.set_defaults(func=cmd_inspect)

    # coverage
    p_coverage = sub.add_parser("coverage", help="Project coverage report")
    p_coverage.add_argument("project", help="Project name")
    p_coverage.set_defaults(func=cmd_coverage)

    # resolve
    p_resolve = sub.add_parser("resolve", help="Resolve tool URLs to registry identifiers")
    p_resolve.add_argument("--force", action="store_true", help="Re-resolve all tools")
    p_resolve.set_defaults(func=cmd_resolve)

    # metrics
    p_metrics = sub.add_parser("metrics", help="Metric collection commands")
    metrics_sub = p_metrics.add_subparsers(dest="metrics_command")

    p_mc = metrics_sub.add_parser("collect", help="Collect metrics from external APIs")
    p_mc.add_argument(
        "--source",
        choices=["github", "pypi", "npm", "deps_dev"],
        help="Collect from single source",
    )
    p_mc.add_argument("--tool", help="Collect for a single tool name")
    p_mc.set_defaults(func=cmd_metrics_collect)

    p_ms = metrics_sub.add_parser("show", help="Show metrics for a tool")
    p_ms.add_argument("name", help="Tool name")
    p_ms.set_defaults(func=cmd_metrics_show)

    # fitness
    p_fitness = sub.add_parser("fitness", help="Fitness scoring")
    fitness_sub = p_fitness.add_subparsers(dest="fitness_command")

    p_fit_score = fitness_sub.add_parser(
        "score",
        help="Score tools against project capabilities",
    )
    p_fit_score.add_argument(
        "--project",
        required=True,
        help="Project name",
    )
    p_fit_score.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Top N tools per capability",
    )
    p_fit_score.add_argument(
        "--persist",
        action="store_true",
        help="Write scores to fitness table",
    )
    p_fit_score.set_defaults(func=cmd_fitness_score)

    p_fit_show = fitness_sub.add_parser(
        "show",
        help="Show fitness for a tool",
    )
    p_fit_show.add_argument("name", help="Tool name")
    p_fit_show.set_defaults(func=cmd_fitness_show)

    # neighborhoods
    p_nbr = sub.add_parser("neighborhoods", help="Neighborhood clustering")
    nbr_sub = p_nbr.add_subparsers(dest="nbr_command")

    p_nbr_compute = nbr_sub.add_parser("compute", help="Compute neighborhoods via Louvain")
    p_nbr_compute.add_argument(
        "--resolution", type=float, default=1.0, help="Louvain resolution (default: 1.0)"
    )
    p_nbr_compute.add_argument(
        "--min-size", type=int, default=3, help="Minimum community size (default: 3)"
    )
    p_nbr_compute.add_argument(
        "--clear", action="store_true", help="Clear pinned memberships before recompute"
    )
    p_nbr_compute.set_defaults(func=cmd_neighborhoods_compute)

    p_nbr_list = nbr_sub.add_parser("list", help="List neighborhoods")
    p_nbr_list.set_defaults(func=cmd_neighborhoods_list)

    p_nbr_show = nbr_sub.add_parser("show", help="Show tools in a neighborhood")
    p_nbr_show.add_argument("name", help="Neighborhood name")
    p_nbr_show.set_defaults(func=cmd_neighborhoods_show)

    # recommend
    p_rec = sub.add_parser("recommend", help="Tool recommendations")
    p_rec.add_argument("--tool", help="Recommend tools related to this tool")
    p_rec.add_argument("--capability", help="Recommend tools for a capability")
    p_rec.add_argument("--project", help="Project name (required with --capability)")
    p_rec.add_argument("--top-n", type=int, default=10, help="Number of recommendations")
    p_rec.set_defaults(func=cmd_recommend)

    # export
    p_export = sub.add_parser("export", help="Export tables to Parquet")
    p_export.add_argument("--output", help="Output directory (default: site/src/data/)")
    p_export.set_defaults(func=cmd_export)

    # validate
    p_val = sub.add_parser("validate", help="Run data quality validation checks")
    p_val.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if not hasattr(args, "func"):
        sub.choices[args.command].print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
