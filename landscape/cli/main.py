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

    seed_path = Path(__file__).resolve().parents[2] / "data" / "seed" / "mlops_tools_catalog.json"
    tools = json.loads(seed_path.read_text())
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
        "--source", choices=["github", "pypi", "npm"], help="Collect from single source"
    )
    p_mc.add_argument("--tool", help="Collect for a single tool name")
    p_mc.set_defaults(func=cmd_metrics_collect)

    p_ms = metrics_sub.add_parser("show", help="Show metrics for a tool")
    p_ms.add_argument("name", help="Tool name")
    p_ms.set_defaults(func=cmd_metrics_show)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
