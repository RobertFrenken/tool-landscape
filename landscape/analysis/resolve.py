"""Resolve tool URLs to registry identifiers (GitHub repo, PyPI package, npm package).

Produces a JSON sidecar file that can be hand-corrected. Subsequent runs
merge with existing overrides rather than clobbering them.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

IDENTIFIERS_PATH = Path(__file__).resolve().parents[2] / "data" / "resolved_identifiers.json"

# Known PyPI name overrides where tool name != package name
PYPI_NAME_OVERRIDES: dict[str, str] = {
    "Great Expectations": "great-expectations",
    "Label Studio": "label-studio",
    "Spark NLP": "spark-nlp",
    "Triton Inference Server": "tritonclient",
    "Delta Lake": "delta-spark",
    "Apache Spark": "pyspark",
    "Apache Airflow": "apache-airflow",
    "Apache Kafka": "kafka-python",
    "Apache Beam": "apache-beam",
    "Apache Flink": "apache-flink",
    "Apache Arrow": "pyarrow",
    "Apache Parquet": "pyarrow",
    "scikit-learn": "scikit-learn",
}

# Known GitHub repo overrides where URL doesn't resolve to the main repo
GITHUB_OVERRIDES: dict[str, str] = {
    "DVC": "iterative/dvc",
    "MLflow": "mlflow/mlflow",
    "Ray": "ray-project/ray",
    "Dask": "dask/dask",
    "PyTorch": "pytorch/pytorch",
    "TensorFlow": "tensorflow/tensorflow",
    "Great Expectations": "great-expectations/great_expectations",
    "Label Studio": "HumanSignal/label-studio",
    "Delta Lake": "delta-io/delta",
    "lakeFS": "treeverse/lakeFS",
    "Pachyderm": "pachyderm/pachyderm",
    "Kubeflow": "kubeflow/kubeflow",
    "Seldon Core": "SeldonIO/seldon-core",
    "Feast": "feast-dev/feast",
    "Metaflow": "Netflix/metaflow",
    "Prefect": "PrefectHQ/prefect",
    "Dagster": "dagster-io/dagster",
    "ZenML": "zenml-io/zenml",
    "BentoML": "bentoml/BentoML",
    "Weights & Biases": "wandb/wandb",
    "ClearML": "allegroai/clearml",
    "Optuna": "optuna/optuna",
    "Hugging Face": "huggingface/transformers",
}

_GITHUB_RE = re.compile(r"github\.com/([^/]+/[^/]+?)(?:\.git)?(?:/.*)?$")


@dataclass
class ToolIdentifiers:
    github_repo: str | None = None
    pypi_package: str | None = None
    npm_package: str | None = None
    resolved_by: list[str] = field(default_factory=list)


def _extract_github_from_url(url: str) -> str | None:
    """Extract owner/repo from a GitHub URL."""
    m = _GITHUB_RE.search(url)
    if m:
        return m.group(1)
    return None


def _guess_pypi_name(tool_name: str) -> str:
    """Guess PyPI package name from tool name."""
    if tool_name in PYPI_NAME_OVERRIDES:
        return PYPI_NAME_OVERRIDES[tool_name]
    # Lowercase, replace spaces/underscores with hyphens
    return re.sub(r"[\s_]+", "-", tool_name.lower())


def _guess_npm_name(tool_name: str) -> str:
    """Guess npm package name from tool name."""
    return tool_name.lower().replace(" ", "-")


async def _check_pypi(client: httpx.AsyncClient, package: str) -> dict | None:
    """Check if a PyPI package exists and return its metadata."""
    try:
        resp = await client.get(f"https://pypi.org/pypi/{package}/json", follow_redirects=True)
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        pass
    return None


async def _check_npm(client: httpx.AsyncClient, package: str) -> dict | None:
    """Check if an npm package exists and return its metadata."""
    try:
        resp = await client.get(
            f"https://registry.npmjs.org/{package}",
            headers={"Accept": "application/vnd.npm.install-v1+json"},
            follow_redirects=True,
        )
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        pass
    return None


async def resolve_tool(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    language_ecosystem: list[str],
) -> ToolIdentifiers:
    """Resolve a single tool's identifiers from URL and metadata."""
    ids = ToolIdentifiers()

    # 1. Check hardcoded overrides
    if name in GITHUB_OVERRIDES:
        ids.github_repo = GITHUB_OVERRIDES[name]
        ids.resolved_by.append("github_override")

    # 2. Extract GitHub repo from URL
    if not ids.github_repo:
        gh = _extract_github_from_url(url)
        if gh:
            ids.github_repo = gh
            ids.resolved_by.append("url_parse")

    # 3. Try PyPI for Python tools
    if "python" in language_ecosystem:
        guess = _guess_pypi_name(name)
        pypi_data = await _check_pypi(client, guess)
        if pypi_data:
            ids.pypi_package = guess
            ids.resolved_by.append("pypi_guess")
            # Backfill GitHub from PyPI project_urls
            if not ids.github_repo:
                project_urls = pypi_data.get("info", {}).get("project_urls") or {}
                for key in ("Source", "Repository", "GitHub", "Code", "Source Code", "Homepage"):
                    link = project_urls.get(key, "")
                    gh = _extract_github_from_url(link)
                    if gh:
                        ids.github_repo = gh
                        ids.resolved_by.append("pypi_project_urls")
                        break
                # Also check info.home_page
                if not ids.github_repo:
                    home = pypi_data.get("info", {}).get("home_page") or ""
                    gh = _extract_github_from_url(home)
                    if gh:
                        ids.github_repo = gh
                        ids.resolved_by.append("pypi_home_page")

    # 4. Try npm for JS/TS tools
    if "javascript" in language_ecosystem or "typescript" in language_ecosystem:
        guess = _guess_npm_name(name)
        npm_data = await _check_npm(client, guess)
        if npm_data:
            ids.npm_package = guess
            ids.resolved_by.append("npm_guess")
            # Backfill GitHub from npm repository field
            if not ids.github_repo:
                repo = npm_data.get("repository", {})
                if isinstance(repo, dict):
                    repo_url = repo.get("url", "")
                    gh = _extract_github_from_url(repo_url)
                    if gh:
                        ids.github_repo = gh
                        ids.resolved_by.append("npm_repository")

    return ids


async def resolve_all(
    tools: list[dict],
    *,
    existing: dict[str, dict] | None = None,
    skip_resolved: bool = True,
) -> dict[str, dict]:
    """Resolve identifiers for all tools.

    Args:
        tools: List of tool dicts from seed data (need 'name', 'url', 'language_ecosystem').
        existing: Previously resolved identifiers (loaded from JSON sidecar).
        skip_resolved: If True, skip tools that already have identifiers in existing.

    Returns:
        Merged dict of {tool_name: {github_repo, pypi_package, npm_package, resolved_by}}.
    """
    existing = existing or {}
    results = dict(existing)  # Start from existing, preserve overrides

    to_resolve = []
    for t in tools:
        name = t["name"]
        if skip_resolved and name in existing and existing[name].get("github_repo"):
            continue
        to_resolve.append(t)

    if not to_resolve:
        logger.info("All %d tools already resolved", len(tools))
        return results

    logger.info("Resolving %d / %d tools", len(to_resolve), len(tools))

    async with httpx.AsyncClient(timeout=15.0) as client:
        for i, t in enumerate(to_resolve):
            name = t["name"]
            url = t.get("url", "")
            langs = t.get("language_ecosystem", [])

            ids = await resolve_tool(client, name, url, langs)
            results[name] = asdict(ids)

            if (i + 1) % 50 == 0:
                logger.info("  resolved %d / %d", i + 1, len(to_resolve))

    return results


def load_identifiers() -> dict[str, dict]:
    """Load existing resolved identifiers from the sidecar file."""
    if IDENTIFIERS_PATH.exists():
        return json.loads(IDENTIFIERS_PATH.read_text())
    return {}


def save_identifiers(identifiers: dict[str, dict]) -> None:
    """Save resolved identifiers to the sidecar file."""
    IDENTIFIERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTIFIERS_PATH.write_text(json.dumps(identifiers, indent=2, sort_keys=True) + "\n")
    logger.info("Saved %d identifiers to %s", len(identifiers), IDENTIFIERS_PATH)
