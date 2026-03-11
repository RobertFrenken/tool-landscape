"""Codebase scanning → draft spec generation.

Deterministic CLI command that reads a codebase's surface-level signals
(pyproject.toml, package.json, config files, directories) and infers
components + current tools.

The output is intentionally conservative — it captures what IS, not what
SHOULD BE. Tightening constraints and adding preferences is the agent's
(or human's) job via the refinement protocol.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from landscape.spec.dep_map import (
    BUILD_BACKENDS,
    FILE_PATTERNS,
    resolve_npm_dep,
    resolve_python_dep,
)

# ── Infer category from tool name ────────────────────────────────────────────

TOOL_CATEGORIES: dict[str, str] = {
    # Frameworks
    "PyTorch": "training_framework",
    "TensorFlow": "training_framework",
    "JAX": "training_framework",
    "Keras": "training_framework",
    "PyTorch Lightning": "training_framework",
    # GNN
    "PyTorch Geometric": "graph_library",
    "DGL": "graph_library",
    # Experiment tracking
    "MLflow": "experiment_tracking",
    "Weights & Biases": "experiment_tracking",
    "Neptune.ai": "experiment_tracking",
    "ClearML": "experiment_tracking",
    "Aim": "experiment_tracking",
    "Comet": "experiment_tracking",
    # Orchestration
    "Ray": "orchestration",
    "Optuna": "orchestration",
    "Dask": "orchestration",
    "Prefect": "orchestration",
    "Apache Airflow": "orchestration",
    "Metaflow": "orchestration",
    # Config
    "Pydantic": "config_management",
    "Hydra": "config_management",
    "OmegaConf": "config_management",
    # Database
    "DuckDB": "query_engine",
    "PostgreSQL": "database",
    "MongoDB": "database",
    "SQLite": "database",
    "Redis": "database",
    # Visualization
    "D3.js": "visualization",
    "Matplotlib": "visualization",
    "Plotly": "visualization",
    "Altair": "visualization",
    "Bokeh": "visualization",
    # Web frameworks
    "FastAPI": "web_framework",
    "Flask": "web_framework",
    "Django": "web_framework",
    "Streamlit": "dashboard",
    "Gradio": "dashboard",
    "Dash": "dashboard",
    # Site frameworks
    "Observable Framework": "site_framework",
    "MkDocs": "site_framework",
    "Quarto": "site_framework",
    "Docusaurus": "site_framework",
    "Hugo": "site_framework",
    "Astro": "site_framework",
    "Next.js": "site_framework",
    # Testing
    "pytest": "testing",
    "Jest": "testing",
    "Vitest": "testing",
    "Playwright": "testing",
    # Linting
    "ruff": "linting",
    "Black": "linting",
    "Flake8": "linting",
    # Package management
    "uv": "package_management",
    "Poetry": "package_management",
    "PDM": "package_management",
    "Hatch": "package_management",
    "pip": "package_management",
    # Data versioning
    "DVC": "data_versioning",
    # CI/CD
    "GitHub Actions": "ci_cd",
    "GitLab CI": "ci_cd",
    "Docker": "containerization",
    "Docker Compose": "containerization",
    # Geospatial
    "GeoPandas": "geospatial",
    # Graph analytics
    "NetworkX": "graph_analytics",
}


def _parse_pyproject(path: Path) -> dict[str, str]:
    """Parse pyproject.toml for dependencies → tool names.

    Returns dict of tool_name → source_info.
    """
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    text = path.read_text()
    data = tomllib.loads(text)

    tools: dict[str, str] = {}

    # Dependencies
    deps = data.get("project", {}).get("dependencies", [])
    optional_deps = data.get("project", {}).get("optional-dependencies", {})
    all_deps = list(deps)
    for group_deps in optional_deps.values():
        all_deps.extend(group_deps)

    for dep in all_deps:
        # Strip version specifiers: "torch>=2.0" → "torch"
        pkg_name = re.split(r"[>=<!~;\s\[]", dep)[0].strip()
        tool_name = resolve_python_dep(pkg_name)
        if tool_name:
            tools[tool_name] = f"pyproject.toml dependency: {pkg_name}"

    # Build backend
    build_backend = data.get("build-system", {}).get("build-backend", "")
    for backend_prefix, tool_name in BUILD_BACKENDS.items():
        if build_backend.startswith(backend_prefix):
            tools[tool_name] = f"pyproject.toml build-backend: {build_backend}"
            break

    return tools


def _parse_package_json(path: Path) -> dict[str, str]:
    """Parse package.json for dependencies → tool names."""
    data = json.loads(path.read_text())
    tools: dict[str, str] = {}

    for dep_group in ("dependencies", "devDependencies"):
        for pkg_name in data.get(dep_group, {}):
            tool_name = resolve_npm_dep(pkg_name)
            if tool_name:
                tools[tool_name] = f"package.json {dep_group}: {pkg_name}"

    return tools


def _parse_requirements_txt(path: Path) -> dict[str, str]:
    """Parse requirements.txt for dependencies."""
    tools: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        pkg_name = re.split(r"[>=<!~;\s\[]", line)[0].strip()
        tool_name = resolve_python_dep(pkg_name)
        if tool_name:
            tools[tool_name] = f"requirements.txt: {pkg_name}"
    return tools


def _scan_file_patterns(project_dir: Path) -> dict[str, str]:
    """Scan for config files and directories that indicate tool usage."""
    tools: dict[str, str] = {}

    for pattern, tool_name in FILE_PATTERNS.items():
        if tool_name is None:
            continue
        target = project_dir / pattern
        if target.exists():
            tools[tool_name] = f"detected file: {pattern}"

    # Check for SLURM scripts
    slurm_files = list(project_dir.glob("**/*.sbatch")) + list(project_dir.glob("**/*.slurm"))
    if slurm_files:
        tools["SLURM"] = f"detected {len(slurm_files)} SLURM scripts"

    return tools


def _detect_environment(project_dir: Path) -> dict[str, object]:
    """Infer environment from project signals."""
    env: dict[str, object] = {}

    # Check for SLURM → HPC
    slurm_files = list(project_dir.glob("**/*.sbatch")) + list(project_dir.glob("**/*.slurm"))
    if slurm_files:
        env["primary"] = "hpc"

    # Check for GPU usage (CUDA imports, torch.cuda references)
    py_files = list(project_dir.glob("**/*.py"))[:50]  # limit scan
    for py_file in py_files:
        try:
            content = py_file.read_text(errors="ignore")
            if "torch.cuda" in content or "cuda" in content.lower():
                env["gpu_required"] = True
                break
        except OSError:
            continue

    return env


def extract_spec(project_dir: str | Path) -> dict:
    """Scan a project directory and generate a draft spec dict.

    Returns a raw dict (not a ProjectSpec) so the caller can review/modify
    before parsing. Each inferred value includes a comment about its source.
    """
    project_dir = Path(project_dir).resolve()
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Not a directory: {project_dir}")

    # Collect all detected tools
    all_tools: dict[str, str] = {}

    # Parse dependency manifests
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        all_tools.update(_parse_pyproject(pyproject))

    package_json = project_dir / "package.json"
    if package_json.exists():
        all_tools.update(_parse_package_json(package_json))

    for req_file in project_dir.glob("requirements*.txt"):
        all_tools.update(_parse_requirements_txt(req_file))

    # Scan for file patterns
    all_tools.update(_scan_file_patterns(project_dir))

    # Detect environment
    env = _detect_environment(project_dir)

    # Group tools by inferred component category
    components: dict[str, dict] = {}
    unmapped_tools: list[str] = []

    for tool_name, source in all_tools.items():
        category = TOOL_CATEGORIES.get(tool_name)
        if category:
            if category not in components:
                components[category] = {
                    "description": "",
                    "current_tool": tool_name,
                    "require": {},
                    "notes": [f"# inferred from {source}"],
                }
            else:
                # Multiple tools in same category — note it
                components[category]["notes"].append(f"# also detected: {tool_name} ({source})")
        else:
            unmapped_tools.append(f"{tool_name} ({source})")

    # Build the spec dict
    spec: dict = {
        "spec_version": "1",
        "project": {
            "name": project_dir.name,
        },
        "environment": env,
        "stack_pins": [],
        "components": components,
    }

    # Add unmapped tools as a top-level comment (in notes)
    if unmapped_tools:
        spec["_unmapped_tools"] = unmapped_tools

    return spec
