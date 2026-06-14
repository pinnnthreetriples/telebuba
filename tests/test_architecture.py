"""Architecture guardrails — enforce the layer matrix and config/env sync as tests.

The four-layer rules (`context/conventions.md`, `context/architecture.md`) and the
"`.env.example` mirrors `core/config.py`" contract are otherwise only prose. These
tests fail the build the moment a layer boundary is crossed or a config field has no
documented env key, which is cheaper than catching layer-rot by eye in review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.config import Settings, settings

_ROOT = Path(__file__).resolve().parent.parent


def _imported_roots(path: Path) -> set[str]:
    """Top-level package names this module imports (absolute imports only)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _modules(layer: str) -> list[Path]:
    return sorted(p for p in (_ROOT / layer).glob("*.py") if p.name != "__init__.py")


def _violations(layer: str, forbidden: set[str]) -> list[str]:
    out: list[str] = []
    for path in _modules(layer):
        hit = _imported_roots(path) & forbidden
        if hit:
            out.append(f"{path.relative_to(_ROOT).as_posix()} imports {sorted(hit)}")
    return out


def test_features_do_not_import_telethon_sqlalchemy_or_each_other() -> None:
    # Features are UI-thin: no DB/Telegram SDK, and no cross-feature imports.
    assert _violations("features", {"sqlalchemy", "telethon", "features"}) == []


def test_services_do_not_import_ui_or_sdks() -> None:
    # Services are pure logic: no NiceGUI, no SQLAlchemy/Telethon, no features.
    assert _violations("services", {"nicegui", "sqlalchemy", "telethon", "features"}) == []


def test_schemas_depend_only_on_pydantic_and_stdlib() -> None:
    # Schemas are leaf contracts: no project layers, no third-party SDKs.
    forbidden = {"core", "services", "features", "sqlalchemy", "telethon", "nicegui"}
    assert _violations("schemas", forbidden) == []


def test_core_does_not_import_upper_layers() -> None:
    # Infrastructure must not depend on the layers above it.
    assert _violations("core", {"features", "services"}) == []


def test_env_example_covers_every_config_field() -> None:
    """Every `core/config.py` field must have a `NAMESPACE__FIELD` key in .env.example."""
    env_text = (_ROOT / ".env.example").read_text(encoding="utf-8")
    missing: list[str] = []
    for namespace in Settings.model_fields:
        nested = getattr(settings, namespace)
        prefix = type(nested).model_config.get("env_prefix", "")
        for field_name in type(nested).model_fields:
            key = f"{prefix}{field_name.upper()}"
            if f"{key}=" not in env_text:
                missing.append(key)
    assert missing == [], f".env.example is missing keys: {missing}"


@pytest.mark.parametrize("layer", ["core", "services", "features", "schemas"])
def test_layer_directory_exists(layer: str) -> None:
    # Guard against a rename silently turning the import checks into no-ops.
    assert (_ROOT / layer).is_dir()
    assert _modules(layer), f"no modules found under {layer}/"
