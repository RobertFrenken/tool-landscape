"""Tests for data quality validation checks."""

from __future__ import annotations

import duckdb
import pytest

from landscape.analysis.validate import (
    _check_archived_growing,
    _check_cloud_only_offline,
    _check_declining_high_ceiling,
    _check_null_coverage,
    _check_opensource_governance,
    _check_partial_coverage,
    run_validation,
)
from landscape.db.schema import create_schema


@pytest.fixture
def con():
    """In-memory DuckDB with schema and diverse test tools."""
    db = duckdb.connect(":memory:")
    create_schema(db)
    yield db
    db.close()


class TestArchivedGrowing:
    def test_flags_archived_growing(self, con):
        con.execute(
            "INSERT INTO tools (name, maturity, community_momentum) "
            "VALUES ('BadTool', 'archived', 'growing')"
        )
        flags = _check_archived_growing(con)
        assert len(flags) == 1
        assert flags[0].severity == "error"

    def test_no_flag_archived_declining(self, con):
        con.execute(
            "INSERT INTO tools (name, maturity, community_momentum) "
            "VALUES ('OkTool', 'archived', 'declining')"
        )
        flags = _check_archived_growing(con)
        assert len(flags) == 0


class TestOpensourceGovernance:
    def test_flags_closed_community(self, con):
        con.execute(
            "INSERT INTO tools (name, open_source, governance) "
            "VALUES ('ClosedCommunity', false, 'community')"
        )
        flags = _check_opensource_governance(con)
        assert len(flags) == 1
        assert flags[0].severity == "warning"


class TestCloudOffline:
    def test_flags_contradiction(self, con):
        con.execute(
            "INSERT INTO tools (name, hpc_compatible, offline_capable) "
            "VALUES ('CloudOffline', 'cloud_only', true)"
        )
        flags = _check_cloud_only_offline(con)
        assert len(flags) == 1
        assert flags[0].severity == "error"


class TestDecliningHighCeiling:
    def test_flags_declining_extensive(self, con):
        con.execute(
            "INSERT INTO tools (name, community_momentum, capability_ceiling) "
            "VALUES ('Legacy', 'declining', 'extensive')"
        )
        flags = _check_declining_high_ceiling(con)
        assert len(flags) == 1


class TestPartialCoverage:
    def test_flags_summary_without_enums(self, con):
        con.execute(
            "INSERT INTO tools (name, summary, community_momentum, capability_ceiling) "
            "VALUES ('Partial', 'A tool', NULL, 'high')"
        )
        flags = _check_partial_coverage(con)
        assert len(flags) == 1


class TestNullCoverage:
    def test_flags_thin_tools(self, con):
        # Tool with all nulls
        con.execute("INSERT INTO tools (name) VALUES ('EmptyTool')")
        flags = _check_null_coverage(con)
        assert len(flags) == 1
        assert "missing" in flags[0].message


class TestRunValidation:
    def test_returns_sorted_flags(self, con):
        con.execute(
            "INSERT INTO tools (name, maturity, community_momentum) "
            "VALUES ('Bad', 'archived', 'growing')"
        )
        con.execute(
            "INSERT INTO tools (name, open_source, governance) VALUES ('Weird', false, 'community')"
        )
        flags = run_validation(con)
        assert len(flags) >= 2

        # Errors should come before warnings
        severities = [f.severity for f in flags]
        if "error" in severities and "warning" in severities:
            assert severities.index("error") < severities.index("warning")

    def test_clean_data_no_flags(self, con):
        con.execute(
            """
            INSERT INTO tools (name, summary, maturity, community_momentum,
                capability_ceiling, documentation_quality, migration_cost,
                lock_in_risk, interoperability, migration_likelihood,
                open_source, governance, hpc_compatible, offline_capable)
            VALUES ('GoodTool', 'A great tool', 'production', 'growing',
                'extensive', 'excellent', 'low', 'low', 'high', 'low',
                true, 'community', 'native', true)
            """
        )
        flags = run_validation(con)
        # Should have no errors or warnings for this well-formed tool
        error_flags = [f for f in flags if f.tool_name == "GoodTool" and f.severity == "error"]
        assert len(error_flags) == 0
