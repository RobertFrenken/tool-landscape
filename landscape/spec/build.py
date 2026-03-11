"""Interactive spec builder — questionnaire-driven spec creation.

Two modes:
- interactive_build(): prompts user via input() for all spec fields
- build_from_answers(path): loads answers from a JSON file (for agents/automation)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from landscape.spec.templates import list_templates, load_template, merge_specs


def _ask(prompt: str, default: str = "") -> str:
    """Prompt user for input with optional default."""
    if default:
        result = input(f"{prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"{prompt}: ").strip()


def _ask_yn(prompt: str, default: bool = False) -> bool:
    """Prompt user for yes/no."""
    suffix = "(Y/n)" if default else "(y/N)"
    result = input(f"{prompt} {suffix}: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


def _ask_int(prompt: str, default: int | None = None) -> int | None:
    """Prompt user for an integer."""
    default_str = str(default) if default is not None else ""
    while True:
        raw = _ask(prompt, default_str)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print("  Please enter an integer.")


def _ask_choice(prompt: str, choices: list[str], default: str = "") -> str:
    """Prompt user to pick from a list of choices."""
    print(f"{prompt}")
    for i, c in enumerate(choices, 1):
        marker = " *" if c == default else ""
        print(f"  {i}. {c}{marker}")
    while True:
        raw = input(f"Choice [1-{len(choices)}]: ").strip()
        if not raw and default:
            return default
        try:
            idx = int(raw)
            if 1 <= idx <= len(choices):
                return choices[idx - 1]
        except ValueError:
            # Allow typing the name directly
            if raw in choices:
                return raw
        print(f"  Please enter 1-{len(choices)} or a valid name.")


def _ask_multi_choice(prompt: str, choices: list[str]) -> list[str]:
    """Prompt user to pick zero or more from a list."""
    print(f"{prompt}")
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    print("  Enter numbers separated by commas, or empty for none.")
    raw = input("Selection: ").strip()
    if not raw:
        return []
    selected = []
    for part in raw.split(","):
        part = part.strip()
        try:
            idx = int(part)
            if 1 <= idx <= len(choices):
                selected.append(choices[idx - 1])
        except ValueError:
            if part in choices:
                selected.append(part)
    return selected


def interactive_build() -> dict:
    """Run interactive questionnaire, return spec dict."""
    print("=== Spec Builder ===\n")

    # 1. Project metadata
    print("-- Project --")
    project_name = _ask("Project name")
    description = _ask("Description (one line)", "")

    # 2. Environment
    print("\n-- Environment --")
    primary = _ask_choice(
        "Primary environment:",
        ["hpc", "cloud", "local", "edge"],
        default="local",
    )
    gpu_required = _ask_yn("GPU required?", default=False)
    internet_on_compute = _ask_yn("Internet available on compute nodes?", default=True)
    team_size = _ask_int("Team size", default=1)

    # 3. Template selection
    print("\n-- Templates --")
    available = list_templates()
    if available:
        templates = _ask_multi_choice(
            "Select templates to compose (components will be inherited):",
            available,
        )
    else:
        print("No templates available.")
        templates = []

    # 4. Components
    components: dict[str, dict] = {}

    if templates:
        # Show what we got from templates
        merged_template: dict = {}
        for t in templates:
            tdata = load_template(t)
            if not merged_template:
                merged_template = tdata
            else:
                merged_template = merge_specs(merged_template, tdata)
        template_components = merged_template.get("components", {})
        if template_components:
            print(f"\nInherited {len(template_components)} components from templates:")
            for name in template_components:
                desc = template_components[name].get("description", "")
                print(f"  - {name}: {desc}")

    print("\n-- Additional Components --")
    while _ask_yn("Add a component?", default=False):
        comp_name = _ask("Component name (snake_case)")
        if not comp_name:
            continue
        comp_desc = _ask("Description", "")
        current_tool = _ask("Current tool (or empty)", "")
        comp: dict[str, Any] = {}
        if comp_desc:
            comp["description"] = comp_desc
        if current_tool:
            comp["current_tool"] = current_tool
        components[comp_name] = comp
        print(f"  Added: {comp_name}")

    # 5. Stack pins
    print("\n-- Stack Pins --")
    pins_raw = _ask("Tools already committed to (comma-separated, or empty)", "")
    stack_pins = [p.strip() for p in pins_raw.split(",") if p.strip()] if pins_raw else []

    # 6. Assemble spec dict
    spec_data: dict[str, Any] = {
        "spec_version": "1",
        "project": {"name": project_name},
    }
    if description:
        spec_data["project"]["description"] = description
    if team_size:
        spec_data["project"]["team_size_ceiling"] = team_size

    environment: dict[str, Any] = {"primary": primary}
    if gpu_required:
        environment["gpu_required"] = True
    if not internet_on_compute:
        environment["internet_on_compute"] = False
    spec_data["environment"] = environment

    if templates:
        spec_data["extends"] = templates

    if stack_pins:
        spec_data["stack_pins"] = stack_pins

    if components:
        spec_data["components"] = components

    # 7. Output path
    default_output = (
        f"{project_name.lower().replace(' ', '-')}-spec.yaml" if project_name else "spec.yaml"
    )
    output_path = _ask("\nOutput file", default_output)

    return {"spec": spec_data, "output_path": output_path}


def build_from_answers(answers_path: str) -> dict:
    """Load answers from JSON file, return spec dict (non-interactive mode for agents).

    Expected JSON format:
    {
        "project_name": "My Project",
        "description": "...",
        "environment": {"primary": "hpc", "gpu_required": true, "internet_on_compute": false},
        "team_size": 3,
        "templates": ["ml-research"],
        "extra_components": {"my_comp": {"description": "..."}},
        "stack_pins": ["PyTorch", "DuckDB"]
    }
    """
    path = Path(answers_path)
    if not path.exists():
        raise FileNotFoundError(f"Answers file not found: {path}")

    with open(path) as f:
        answers = json.load(f)

    project_name = answers.get("project_name", "Untitled")
    description = answers.get("description", "")
    env = answers.get("environment", {})
    team_size = answers.get("team_size")
    templates = answers.get("templates", [])
    extra_components = answers.get("extra_components", {})
    stack_pins = answers.get("stack_pins", [])

    # Validate templates exist
    available = list_templates()
    for t in templates:
        if t not in available:
            raise ValueError(f"Template '{t}' not found. Available: {available}")

    # Build spec dict
    spec_data: dict[str, Any] = {
        "spec_version": "1",
        "project": {"name": project_name},
    }
    if description:
        spec_data["project"]["description"] = description
    if team_size:
        spec_data["project"]["team_size_ceiling"] = team_size

    if env:
        spec_data["environment"] = env

    if templates:
        spec_data["extends"] = templates

    if stack_pins:
        spec_data["stack_pins"] = stack_pins

    if extra_components:
        spec_data["components"] = extra_components

    return {"spec": spec_data, "output_path": None}
