"""Package name → tool-landscape name lookup table.

Maps common dependency names (from pyproject.toml, package.json, requirements.txt)
to canonical tool names in the tools table. Used by `landscape spec extract` to
identify current tools from a project's dependency manifest.

Unmapped dependencies are listed in comments in the generated spec.
"""

from __future__ import annotations

# Python packages (PyPI names) → tool-landscape canonical names
PYTHON_PACKAGES: dict[str, str] = {
    # Deep learning
    "torch": "PyTorch",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "tf": "TensorFlow",
    "jax": "JAX",
    "jaxlib": "JAX",
    "keras": "Keras",
    "lightning": "PyTorch Lightning",
    "pytorch-lightning": "PyTorch Lightning",
    # Graph neural networks
    "torch-geometric": "PyTorch Geometric",
    "torch_geometric": "PyTorch Geometric",
    "pyg": "PyTorch Geometric",
    "dgl": "DGL",
    "torch-scatter": "PyTorch Geometric",
    "torch-sparse": "PyTorch Geometric",
    # ML / data science
    "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "xgboost": "XGBoost",
    "lightgbm": "LightGBM",
    "catboost": "CatBoost",
    "pandas": "pandas",
    "polars": "Polars",
    "numpy": "NumPy",
    "scipy": "SciPy",
    # Experiment tracking
    "mlflow": "MLflow",
    "wandb": "Weights & Biases",
    "neptune": "Neptune.ai",
    "clearml": "ClearML",
    "aim": "Aim",
    "comet-ml": "Comet",
    # Orchestration / scheduling
    "ray": "Ray",
    "ray[tune]": "Ray",
    "ray[train]": "Ray",
    "optuna": "Optuna",
    "hyperopt": "Hyperopt",
    "dask": "Dask",
    "prefect": "Prefect",
    "airflow": "Apache Airflow",
    "apache-airflow": "Apache Airflow",
    "luigi": "Luigi",
    "metaflow": "Metaflow",
    # Configuration
    "pydantic": "Pydantic",
    "hydra-core": "Hydra",
    "omegaconf": "OmegaConf",
    "dynaconf": "Dynaconf",
    # Databases
    "duckdb": "DuckDB",
    "sqlalchemy": "SQLAlchemy",
    "psycopg2": "PostgreSQL",
    "psycopg": "PostgreSQL",
    "pymongo": "MongoDB",
    "redis": "Redis",
    "sqlite3": "SQLite",
    # Data versioning
    "dvc": "DVC",
    # Visualization
    "matplotlib": "Matplotlib",
    "seaborn": "Seaborn",
    "plotly": "Plotly",
    "altair": "Altair",
    "bokeh": "Bokeh",
    "streamlit": "Streamlit",
    "gradio": "Gradio",
    "dash": "Dash",
    # Web frameworks
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "starlette": "Starlette",
    "uvicorn": "Uvicorn",
    # Testing
    "pytest": "pytest",
    "unittest": "unittest",
    "hypothesis": "Hypothesis",
    # Linting / formatting
    "ruff": "ruff",
    "black": "Black",
    "flake8": "Flake8",
    "mypy": "mypy",
    "pyright": "Pyright",
    # Package management (detected from tool, not dependency)
    "uv": "uv",
    "pip": "pip",
    "poetry": "Poetry",
    "pdm": "PDM",
    "hatch": "Hatch",
    "flit": "Flit",
    # LLM / AI
    "langchain": "LangChain",
    "llama-index": "LlamaIndex",
    "openai": "OpenAI API",
    "anthropic": "Anthropic API",
    "transformers": "Hugging Face Transformers",
    "datasets": "Hugging Face Datasets",
    "tokenizers": "Hugging Face Tokenizers",
    # Networking / HTTP
    "httpx": "httpx",
    "requests": "Requests",
    "aiohttp": "aiohttp",
    # Serialization
    "pyyaml": "PyYAML",
    "toml": "TOML",
    "msgpack": "MessagePack",
    "protobuf": "Protocol Buffers",
    # Geospatial
    "geopandas": "GeoPandas",
    "shapely": "Shapely",
    "fiona": "Fiona",
    "pyproj": "pyproj",
    "rasterio": "Rasterio",
    # Graph analytics
    "networkx": "NetworkX",
    "igraph": "igraph",
    "python-igraph": "igraph",
    "graph-tool": "graph-tool",
}

# npm packages → tool-landscape canonical names
NPM_PACKAGES: dict[str, str] = {
    # Visualization
    "d3": "D3.js",
    "d3-selection": "D3.js",
    "d3-scale": "D3.js",
    "d3-geo": "D3.js",
    "chart.js": "Chart.js",
    "echarts": "Apache ECharts",
    "plotly.js": "Plotly",
    "vega": "Vega",
    "vega-lite": "Vega-Lite",
    "@observablehq/plot": "Observable Plot",
    # Frameworks
    "react": "React",
    "vue": "Vue.js",
    "svelte": "Svelte",
    "next": "Next.js",
    "nuxt": "Nuxt",
    "astro": "Astro",
    "gatsby": "Gatsby",
    "@11ty/eleventy": "Eleventy",
    # Build tools
    "vite": "Vite",
    "webpack": "webpack",
    "esbuild": "esbuild",
    "rollup": "Rollup",
    "parcel": "Parcel",
    # Testing
    "jest": "Jest",
    "vitest": "Vitest",
    "playwright": "Playwright",
    "cypress": "Cypress",
    # CSS
    "tailwindcss": "Tailwind CSS",
    "sass": "Sass",
    # Data
    "@duckdb/duckdb-wasm": "DuckDB",
    "apache-arrow": "Apache Arrow",
    # Mapping
    "leaflet": "Leaflet",
    "mapbox-gl": "Mapbox GL JS",
    "maplibre-gl": "MapLibre GL JS",
    "topojson-client": "TopoJSON",
    # Graph
    "cytoscape": "Cytoscape.js",
    "sigma": "Sigma.js",
    # UI
    "htmx.org": "htmx",
}

# Config/build file patterns → tool detection
FILE_PATTERNS: dict[str, str] = {
    "mkdocs.yml": "MkDocs",
    "mkdocs.yaml": "MkDocs",
    "observablehq.config.js": "Observable Framework",
    "observablehq.config.ts": "Observable Framework",
    "_quarto.yml": "Quarto",
    "quarto.yml": "Quarto",
    "docusaurus.config.js": "Docusaurus",
    "hugo.toml": "Hugo",
    "hugo.yaml": "Hugo",
    "eleventy.config.js": "Eleventy",
    ".eleventy.js": "Eleventy",
    "astro.config.mjs": "Astro",
    "next.config.js": "Next.js",
    "next.config.mjs": "Next.js",
    "nuxt.config.ts": "Nuxt",
    "svelte.config.js": "SvelteKit",
    "vite.config.ts": "Vite",
    "vite.config.js": "Vite",
    "webpack.config.js": "webpack",
    "tsconfig.json": "TypeScript",
    "Dockerfile": "Docker",
    "Containerfile": "Podman",
    "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
    "Makefile": "Make",
    "justfile": "just",
    "Taskfile.yml": "Task",
    ".github/workflows": "GitHub Actions",
    ".gitlab-ci.yml": "GitLab CI",
    "Jenkinsfile": "Jenkins",
    ".circleci": "CircleCI",
    ".dvc": "DVC",
    "dvc.yaml": "DVC",
    "mlruns": "MLflow",
    "MLproject": "MLflow",
    "ray_results": "Ray",
    ".pre-commit-config.yaml": "pre-commit",
    "pyproject.toml": None,  # detected separately by build-backend
    "Cargo.toml": "Rust/Cargo",
    "go.mod": "Go",
}

# Build backend detection (from pyproject.toml [build-system])
BUILD_BACKENDS: dict[str, str] = {
    "hatchling": "Hatch",
    "poetry.core": "Poetry",
    "pdm.backend": "PDM",
    "flit_core": "Flit",
    "setuptools": "setuptools",
    "maturin": "Maturin",
}


def resolve_python_dep(package_name: str) -> str | None:
    """Resolve a Python package name to a tool-landscape canonical name."""
    # Normalize: lowercase, strip extras
    name = package_name.lower().split("[")[0].strip()
    return PYTHON_PACKAGES.get(name)


def resolve_npm_dep(package_name: str) -> str | None:
    """Resolve an npm package name to a tool-landscape canonical name."""
    return NPM_PACKAGES.get(package_name)
