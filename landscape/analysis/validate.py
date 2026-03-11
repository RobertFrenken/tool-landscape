"""Internal consistency validation for tool catalog data."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb


@dataclass
class ValidationFlag:
    """A detected data quality issue."""

    tool_name: str
    rule_name: str
    severity: str  # 'error', 'warning', 'info'
    message: str
    auto_fixable: bool = False


# ── Validation rules ─────────────────────────────────────────────────────────

RULES: list[tuple[str, str, str]] = [
    # (rule_name, SQL condition that flags problems, severity)
    # Each SQL returns (tool_name, message) rows for flagged tools
]


def _check_archived_growing(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """Archived tools should not have growing momentum."""
    rows = con.execute(
        """
        SELECT name FROM tools
        WHERE maturity = 'archived' AND community_momentum = 'growing'
        """
    ).fetchall()
    return [
        ValidationFlag(name, "archived_growing", "error", "archived tool with growing momentum")
        for (name,) in rows
    ]


def _check_opensource_governance(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """Non-open-source tools with community governance is suspicious."""
    rows = con.execute(
        """
        SELECT name FROM tools
        WHERE open_source = false AND governance = 'community'
        """
    ).fetchall()
    return [
        ValidationFlag(
            name, "closed_community", "warning", "closed-source with community governance"
        )
        for (name,) in rows
    ]


def _check_extensive_early(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """Extensive ceiling + early maturity is plausible but rare — flag for review."""
    rows = con.execute(
        """
        SELECT name FROM tools
        WHERE capability_ceiling = 'extensive' AND maturity = 'early'
        """
    ).fetchall()
    return [
        ValidationFlag(
            name, "extensive_early", "info", "extensive ceiling with early maturity (rare)"
        )
        for (name,) in rows
    ]


def _check_partial_coverage(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """Tools with summary but missing key enum fields."""
    rows = con.execute(
        """
        SELECT name FROM tools
        WHERE summary IS NOT NULL AND summary != ''
        AND (community_momentum IS NULL OR capability_ceiling IS NULL)
        """
    ).fetchall()
    return [
        ValidationFlag(
            name, "partial_enums", "warning", "has summary but missing momentum or ceiling"
        )
        for (name,) in rows
    ]


def _check_empty_summary_with_enums(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """Tools with enum fields populated but no summary."""
    rows = con.execute(
        """
        SELECT name FROM tools
        WHERE (summary IS NULL OR summary = '')
        AND community_momentum IS NOT NULL
        AND capability_ceiling IS NOT NULL
        """
    ).fetchall()
    return [
        ValidationFlag(name, "enums_no_summary", "info", "has enum fields but no summary")
        for (name,) in rows
    ]


def _check_declining_high_ceiling(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """Declining momentum + extensive ceiling = possible misrating."""
    rows = con.execute(
        """
        SELECT name FROM tools
        WHERE community_momentum = 'declining' AND capability_ceiling = 'extensive'
        """
    ).fetchall()
    return [
        ValidationFlag(
            name, "declining_extensive", "warning", "declining momentum with extensive ceiling"
        )
        for (name,) in rows
    ]


def _check_cloud_only_offline(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """cloud_only HPC compat + offline_capable is contradictory."""
    rows = con.execute(
        """
        SELECT name FROM tools
        WHERE hpc_compatible = 'cloud_only' AND offline_capable = true
        """
    ).fetchall()
    return [
        ValidationFlag(name, "cloud_offline", "error", "cloud_only but marked offline-capable")
        for (name,) in rows
    ]


def _check_replaces_edge_categories(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """Tools connected by 'replaces' edge should share at least one category."""
    rows = con.execute(
        """
        SELECT t1.name, t2.name
        FROM edges e
        JOIN tools t1 ON e.source_id = t1.tool_id
        JOIN tools t2 ON e.target_id = t2.tool_id
        WHERE e.relation = 'replaces'
        AND NOT EXISTS (
            SELECT 1 FROM unnest(t1.categories) AS c1(cat)
            JOIN unnest(t2.categories) AS c2(cat) ON c1.cat = c2.cat
        )
        """
    ).fetchall()
    return [
        ValidationFlag(
            source,
            "replaces_no_shared_category",
            "warning",
            f"replaces {target} but no shared categories",
        )
        for source, target in rows
    ]


def _check_saas_self_hosted(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """SaaS-only tools (no self-hosted) with 'native' HPC compat is unusual."""
    rows = con.execute(
        """
        SELECT name FROM tools
        WHERE saas_available = true AND self_hosted_viable = false
        AND hpc_compatible = 'native'
        """
    ).fetchall()
    return [
        ValidationFlag(name, "saas_native_hpc", "warning", "SaaS-only but native HPC compat")
        for (name,) in rows
    ]


def _check_null_coverage(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """Report tools missing many key fields (thin data)."""
    rows = con.execute(
        """
        SELECT name, null_count FROM (
            SELECT name,
                (CASE WHEN summary IS NULL OR summary = '' THEN 1 ELSE 0 END
                + CASE WHEN community_momentum IS NULL THEN 1 ELSE 0 END
                + CASE WHEN capability_ceiling IS NULL THEN 1 ELSE 0 END
                + CASE WHEN documentation_quality IS NULL THEN 1 ELSE 0 END
                + CASE WHEN migration_cost IS NULL THEN 1 ELSE 0 END
                + CASE WHEN lock_in_risk IS NULL THEN 1 ELSE 0 END
                + CASE WHEN interoperability IS NULL THEN 1 ELSE 0 END
                + CASE WHEN migration_likelihood IS NULL THEN 1 ELSE 0 END
                ) as null_count
            FROM tools
        ) WHERE null_count >= 5
        ORDER BY null_count DESC
        """
    ).fetchall()
    return [
        ValidationFlag(
            name,
            "thin_data",
            "info",
            f"{null_count}/8 key fields missing",
        )
        for name, null_count in rows
    ]


# ── Main entry point ─────────────────────────────────────────────────────────

ALL_CHECKS = [
    _check_archived_growing,
    _check_opensource_governance,
    _check_extensive_early,
    _check_partial_coverage,
    _check_empty_summary_with_enums,
    _check_declining_high_ceiling,
    _check_cloud_only_offline,
    _check_replaces_edge_categories,
    _check_saas_self_hosted,
    _check_null_coverage,
]


def run_validation(con: duckdb.DuckDBPyConnection) -> list[ValidationFlag]:
    """Run all validation checks and return flags sorted by severity."""
    flags: list[ValidationFlag] = []
    for check in ALL_CHECKS:
        flags.extend(check(con))

    severity_order = {"error": 0, "warning": 1, "info": 2}
    flags.sort(key=lambda f: (severity_order.get(f.severity, 3), f.rule_name, f.tool_name))
    return flags


def print_validation_report(flags: list[ValidationFlag]) -> None:
    """Print a human-readable validation report."""
    if not flags:
        print("No validation issues found.")
        return

    errors = [f for f in flags if f.severity == "error"]
    warnings = [f for f in flags if f.severity == "warning"]
    infos = [f for f in flags if f.severity == "info"]

    for label, items in [("ERRORS", errors), ("WARNINGS", warnings), ("INFO", infos)]:
        if items:
            print(f"\n{label} ({len(items)}):")
            # Group by rule
            by_rule: dict[str, list[ValidationFlag]] = {}
            for f in items:
                by_rule.setdefault(f.rule_name, []).append(f)
            for rule, rule_flags in by_rule.items():
                print(f"  [{rule}] ({len(rule_flags)} tools)")
                for f in rule_flags[:5]:
                    print(f"    {f.tool_name}: {f.message}")
                if len(rule_flags) > 5:
                    print(f"    ... and {len(rule_flags) - 5} more")

    print(f"\nTotal: {len(errors)} errors, {len(warnings)} warnings, {len(infos)} info")
