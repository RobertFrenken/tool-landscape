"""Tests for CLI commands using in-memory DuckDB."""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

import duckdb
import pytest

from landscape.db.schema import create_schema


@pytest.fixture
def con():
    """In-memory DuckDB with schema, tools, edges, project, and capability."""
    db = duckdb.connect(":memory:")
    create_schema(db)

    db.execute(
        """
        INSERT INTO tools (name, url, open_source, license, summary,
            maturity, governance, hpc_compatible, community_momentum,
            capability_ceiling, categories, language_ecosystem,
            integration_targets, used_by) VALUES
        ('PyTorch', 'https://pytorch.org', true, 'BSD-3', 'ML framework',
            'production', 'community', 'adaptable', 'growing',
            'extensive', ['ml_framework'], ['python'], ['CUDA Toolkit'], ['KD-GAT']),
        ('TensorFlow', 'https://tensorflow.org', true, 'Apache-2.0', 'ML framework',
            'production', 'company_backed', 'adaptable', 'stable',
            'extensive', ['ml_framework'], ['python'], [], ['KD-GAT']),
        ('React', 'https://react.dev', true, 'MIT', 'UI framework',
            'production', 'company_backed', 'cloud_only', 'growing',
            'high', ['frontend'], ['javascript'], [], []),
        ('FastAPI', 'https://fastapi.tiangolo.com', true, 'MIT', 'API framework',
            'production', 'community', 'adaptable', 'growing',
            'high', ['backend', 'api_framework'], ['python'], ['Pydantic'], []),
        ('OldTool', 'https://old.dev', true, 'MIT', 'Legacy tool',
            'archived', 'community', NULL, 'declining',
            'low', ['testing'], ['python'], [], [])
        """
    )

    # Edges
    ids = {}
    for row in db.execute("SELECT tool_id, name FROM tools").fetchall():
        ids[row[1]] = row[0]

    db.execute(
        """
        INSERT INTO edges (source_id, target_id, relation, source_info, evidence) VALUES
            ($1, $2, 'replaces', 'hand_curated', 'PyTorch replacing TF'),
            ($3, $4, 'integrates_with', 'hand_curated', 'FastAPI uses Pydantic')
        """,
        [ids["PyTorch"], ids["TensorFlow"], ids["FastAPI"], ids["React"]],
    )

    # Project and capability
    db.execute(
        """
        INSERT INTO projects (name, description, team_size_ceiling)
        VALUES ('TestProject', 'Test project', 5)
        """
    )
    proj_id = db.execute("SELECT project_id FROM projects WHERE name = 'TestProject'").fetchone()[0]

    db.execute(
        """
        INSERT INTO capabilities (project_id, name, description, current_tool_id,
            floor_requirements, ceiling_requirements, triggers)
        VALUES ($1, 'ml_training', 'Model training', $2, '{}'::JSON, '{}'::JSON, [])
        """,
        [proj_id, ids["PyTorch"]],
    )

    yield db
    db.close()


def _capture_output(func, args_namespace):
    """Capture stdout from a CLI command function."""
    buf = StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        func(args_namespace)
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


class TestCmdStats:
    def test_stats_prints_table_counts(self, con, tmp_path):
        """stats command prints row counts for all tables."""
        import argparse

        from landscape.cli.main import cmd_stats

        db_path = tmp_path / "test.duckdb"
        # Write data to file-based DB
        file_con = duckdb.connect(str(db_path))
        create_schema(file_con)
        file_con.execute(
            """
            INSERT INTO tools (name, summary, categories) VALUES
                ('ToolA', 'A', ['testing']),
                ('ToolB', 'B', ['frontend'])
            """
        )
        file_con.close()

        with patch("landscape.db.connection.DEFAULT_DB_PATH", db_path):
            args = argparse.Namespace()
            output = _capture_output(cmd_stats, args)

        assert "tools: 2 rows" in output
        assert "Database:" in output


class TestCmdQueryTools:
    def test_query_no_filters(self, con):
        """Query without filters returns all tools."""
        import argparse

        from landscape.cli.main import cmd_query_tools

        args = argparse.Namespace(
            category=None, hpc=None, momentum=None, ceiling=None, used_by=None
        )
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_query_tools, args)

        assert "5 tools" in output
        assert "PyTorch" in output
        assert "React" in output

    def test_query_by_category(self, con):
        """Query filtered by category returns matching tools."""
        import argparse

        from landscape.cli.main import cmd_query_tools

        args = argparse.Namespace(
            category="ml_framework", hpc=None, momentum=None, ceiling=None, used_by=None
        )
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_query_tools, args)

        assert "2 tools" in output
        assert "PyTorch" in output
        assert "TensorFlow" in output
        assert "React" not in output

    def test_query_by_momentum(self, con):
        """Query filtered by momentum."""
        import argparse

        from landscape.cli.main import cmd_query_tools

        args = argparse.Namespace(
            category=None, hpc=None, momentum="growing", ceiling=None, used_by=None
        )
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_query_tools, args)

        assert "PyTorch" in output
        assert "React" in output
        assert "OldTool" not in output

    def test_query_by_used_by(self, con):
        """Query filtered by used_by project."""
        import argparse

        from landscape.cli.main import cmd_query_tools

        args = argparse.Namespace(
            category=None, hpc=None, momentum=None, ceiling=None, used_by="KD-GAT"
        )
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_query_tools, args)

        assert "2 tools" in output
        assert "PyTorch" in output
        assert "React" not in output

    def test_query_no_results(self, con):
        """Query with impossible filters returns no results."""
        import argparse

        from landscape.cli.main import cmd_query_tools

        args = argparse.Namespace(
            category="nonexistent", hpc=None, momentum=None, ceiling=None, used_by=None
        )
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_query_tools, args)

        assert "No tools found" in output


class TestCmdInspect:
    def test_inspect_existing_tool(self, con):
        """Inspect shows tool details and edges."""
        import argparse

        from landscape.cli.main import cmd_inspect

        args = argparse.Namespace(name="PyTorch")
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_inspect, args)

        assert "=== PyTorch ===" in output
        assert "pytorch.org" in output
        assert "BSD-3" in output
        assert "ml_framework" in output
        assert "[replaces]" in output
        assert "TensorFlow" in output

    def test_inspect_not_found(self, con):
        """Inspect a non-existent tool exits with error."""
        import argparse

        from landscape.cli.main import cmd_inspect

        args = argparse.Namespace(name="NoSuchTool")
        with (
            patch("landscape.db.connection.get_db", return_value=con),
            pytest.raises(SystemExit),
        ):
            _capture_output(cmd_inspect, args)

    def test_inspect_case_insensitive(self, con):
        """Inspect matches tool name case-insensitively."""
        import argparse

        from landscape.cli.main import cmd_inspect

        args = argparse.Namespace(name="pytorch")
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_inspect, args)

        assert "=== PyTorch ===" in output


class TestCmdCoverage:
    def test_coverage_report(self, con):
        """Coverage shows capabilities and current tools."""
        import argparse

        from landscape.cli.main import cmd_coverage

        args = argparse.Namespace(project="TestProject")
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_coverage, args)

        assert "TestProject" in output
        assert "ml_training" in output
        assert "PyTorch" in output

    def test_coverage_not_found(self, con):
        """Coverage for unknown project exits with error."""
        import argparse

        from landscape.cli.main import cmd_coverage

        args = argparse.Namespace(project="NoProject")
        with (
            patch("landscape.db.connection.get_db", return_value=con),
            pytest.raises(SystemExit),
        ):
            _capture_output(cmd_coverage, args)


class TestCmdNeighborhoods:
    def test_neighborhoods_compute(self, con):
        """Neighborhoods compute runs without error."""
        import argparse

        from landscape.cli.main import cmd_neighborhoods_compute

        args = argparse.Namespace(resolution=1.0, min_size=2, clear=False)
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_neighborhoods_compute, args)

        assert "Computed" in output
        assert "tools" in output.lower()

    def test_neighborhoods_list_empty(self, con):
        """Neighborhoods list when none exist."""
        import argparse

        from landscape.cli.main import cmd_neighborhoods_list

        args = argparse.Namespace()
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_neighborhoods_list, args)

        assert "No neighborhoods" in output

    def test_neighborhoods_list_after_compute(self, con):
        """Neighborhoods list after compute shows neighborhoods."""
        import argparse

        from landscape.analysis.neighborhoods import compute_neighborhoods, persist_neighborhoods
        from landscape.cli.main import cmd_neighborhoods_list

        results = compute_neighborhoods(con, resolution=1.0, min_size=2)
        persist_neighborhoods(con, results)

        args = argparse.Namespace()
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_neighborhoods_list, args)

        assert "neighborhoods" in output.lower()


class TestCmdValidate:
    def test_validate_runs(self, con):
        """Validate command runs and produces output."""
        import argparse

        from landscape.cli.main import cmd_validate

        args = argparse.Namespace()
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_validate, args)

        # Should produce some output (report header at minimum)
        assert len(output) > 0


class TestCmdRecommend:
    def test_recommend_for_tool(self, con):
        """Recommend for tool returns related tools."""
        import argparse

        from landscape.cli.main import cmd_recommend

        args = argparse.Namespace(tool="PyTorch", capability=None, project=None, top_n=5)
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_recommend, args)

        assert "PyTorch" in output

    def test_recommend_capability_without_project(self, con):
        """Recommend with --capability but no --project exits."""
        import argparse

        from landscape.cli.main import cmd_recommend

        args = argparse.Namespace(tool=None, capability="ml_training", project=None, top_n=5)
        with (
            patch("landscape.db.connection.get_db", return_value=con),
            pytest.raises(SystemExit),
        ):
            _capture_output(cmd_recommend, args)

    def test_recommend_no_args(self, con):
        """Recommend with no --tool or --capability exits."""
        import argparse

        from landscape.cli.main import cmd_recommend

        args = argparse.Namespace(tool=None, capability=None, project=None, top_n=5)
        with (
            patch("landscape.db.connection.get_db", return_value=con),
            pytest.raises(SystemExit),
        ):
            _capture_output(cmd_recommend, args)


class TestCmdExport:
    def test_export_creates_parquet(self, con, tmp_path):
        """Export creates Parquet files."""
        import argparse

        from landscape.cli.main import cmd_export

        args = argparse.Namespace(output=str(tmp_path))
        with patch("landscape.db.connection.get_db", return_value=con):
            output = _capture_output(cmd_export, args)

        assert "tools.parquet" in output
        assert (tmp_path / "tools.parquet").exists()
        assert (tmp_path / "edges.parquet").exists()
        assert (tmp_path / "neighborhoods.parquet").exists()
        assert (tmp_path / "projects.parquet").exists()


class TestMainParser:
    def test_no_command_exits(self):
        """Running with no command prints help and exits."""
        from landscape.cli.main import main

        with patch("sys.argv", ["landscape"]), pytest.raises(SystemExit):
            main()

    def test_unknown_command_exits(self):
        """Running with unknown command exits."""
        from landscape.cli.main import main

        with patch("sys.argv", ["landscape", "foobar"]), pytest.raises(SystemExit):
            main()
