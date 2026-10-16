"""Architecture fitness tests: the layer rules from docs/ARCHITECTURE.md, checked on every build.

The point is that "the LLM never has authority" is a property of the import graph, not a promise.
If someone wires the agent straight to an executor, the database or the sandbox client, CI fails.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "app"
LAYERS = ["core", "infra", "modules", "guard", "agent", "interface"]  # lowest first
COMPOSITION_ROOT = {"app", "app.main", "app.bootstrap"}
SIGNING_KEY_OWNER = "app.guard.capability"
PUBLIC_MODULE_API = {"service", "schemas"}


@cache
def source_modules() -> dict[str, Path]:
    modules = {}
    for path in APP_DIR.rglob("*.py"):
        parts = list(path.relative_to(APP_DIR.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules[".".join(parts)] = path
    return modules


def _resolve(target: str) -> str:
    """Map `from app.guard import api` to `app.guard.api` when that is a real submodule."""
    return target if target in source_modules() else target.rsplit(".", 1)[0]


@cache
def imports_of(module: str) -> tuple[tuple[str, int], ...]:
    tree = ast.parse(source_modules()[module].read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.append((f"<relative import level {node.level}>", node.lineno))
                continue
            for alias in node.names:
                found.append((_resolve(f"{node.module}.{alias.name}"), node.lineno))
    return tuple((name, line) for name, line in found if name.startswith(("app", "<relative")))


def layer_of(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in LAYERS else None


def module_name_of(module: str) -> str | None:
    """`app.modules.ledger.service` -> `ledger`."""
    parts = module.split(".")
    return parts[2] if len(parts) > 2 and parts[1] == "modules" else None


def all_imports():
    for module in sorted(source_modules()):
        if module in COMPOSITION_ROOT:
            continue
        for target, line in imports_of(module):
            yield module, target, line


def test_layer_packages_exist():
    for layer in LAYERS:
        assert (APP_DIR / layer / "__init__.py").exists(), f"missing layer package app/{layer}"


def test_no_relative_imports():
    bad = [f"{m}:{line}" for m, t, line in all_imports() if t.startswith("<relative")]
    assert not bad, f"use absolute imports so the layer rules are checkable: {bad}"


def test_no_upward_imports():
    bad = []
    for module, target, line in all_imports():
        source_layer, target_layer = layer_of(module), layer_of(target)
        if source_layer and target_layer and LAYERS.index(target_layer) > LAYERS.index(source_layer):
            bad.append(f"{module}:{line} imports {target} ({source_layer} -> {target_layer})")
    assert not bad, "upward imports break the layering:\n" + "\n".join(bad)


def test_agent_only_reaches_the_guard_api():
    bad = []
    for module, target, line in all_imports():
        if layer_of(module) != "agent":
            continue
        target_layer = layer_of(target)
        if target_layer in {"modules", "infra"}:
            bad.append(f"{module}:{line} imports {target}")
        if target_layer == "guard" and not (target == "app.guard.api" or target.startswith("app.guard.api.")):
            bad.append(f"{module}:{line} imports {target} (only app.guard.api is allowed)")
    assert not bad, "the agent layer must have no path to state-changing code:\n" + "\n".join(bad)


def test_only_the_capability_signer_touches_the_signing_key():
    bad = []
    for module, path in source_modules().items():
        if module == SIGNING_KEY_OWNER:
            continue
        text = path.read_text(encoding="utf-8").lower()
        if "guard_signing_key" in text:
            bad.append(module)
    assert not bad, f"only {SIGNING_KEY_OWNER} may read the signing key: {bad}"


def test_script_only_secrets_stay_out_of_the_app():
    """The demo owner's PIN is read only by the seed and demo scripts; the running app never loads it."""
    bad = [m for m, path in source_modules().items() if "demo_owner_pin" in path.read_text(encoding="utf-8").lower()]
    assert not bad, f"the app must never load the owner's PIN: {bad}"


def test_only_the_guard_mints_authorized_actions():
    bad = []
    for module, path in source_modules().items():
        if module in {SIGNING_KEY_OWNER, "app.core.actions"}:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == "AuthorizedAction":
                    bad.append(f"{module}:{node.lineno}")
    assert not bad, f"AuthorizedAction may only be constructed by {SIGNING_KEY_OWNER}: {bad}"


def test_owner_controls_are_reachable_only_from_the_interface():
    bad = [
        f"{m}:{line}"
        for m, t, line in all_imports()
        if t.startswith("app.guard.owner_controls")
        and layer_of(m) != "interface"
        and not m.startswith("app.guard.owner_controls")
    ]
    assert not bad, f"owner-only controls must not be importable by the agent or modules: {bad}"


def test_modules_use_each_others_public_api_only():
    bad = []
    for module, target, line in all_imports():
        target_module = module_name_of(target)
        if target_module is None or module_name_of(module) == target_module:
            continue
        parts = target.split(".")
        if len(parts) < 4 or parts[3] not in PUBLIC_MODULE_API:
            bad.append(f"{module}:{line} imports {target}")
    assert not bad, "import another module only through its service/schemas:\n" + "\n".join(bad)


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_has_a_docstring(layer):
    tree = ast.parse((APP_DIR / layer / "__init__.py").read_text(encoding="utf-8"))
    assert ast.get_docstring(tree), f"app/{layer}/__init__.py should say what the layer owns"
