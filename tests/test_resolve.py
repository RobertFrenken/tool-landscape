"""Tests for the URL resolver."""

from landscape.analysis.resolve import _extract_github_from_url, _guess_pypi_name


def test_extract_github_from_url_standard():
    assert _extract_github_from_url("https://github.com/iterative/dvc") == "iterative/dvc"


def test_extract_github_from_url_with_path():
    assert (
        _extract_github_from_url("https://github.com/mlflow/mlflow/tree/master") == "mlflow/mlflow"
    )


def test_extract_github_from_url_with_git_suffix():
    assert _extract_github_from_url("https://github.com/ray-project/ray.git") == "ray-project/ray"


def test_extract_github_from_url_not_github():
    assert _extract_github_from_url("https://dvc.org") is None


def test_extract_github_from_url_empty():
    assert _extract_github_from_url("") is None


def test_guess_pypi_name_simple():
    assert _guess_pypi_name("mlflow") == "mlflow"


def test_guess_pypi_name_with_spaces():
    assert _guess_pypi_name("Great Expectations") == "great-expectations"


def test_guess_pypi_name_override():
    assert _guess_pypi_name("Apache Spark") == "pyspark"
