"""Architecture guardrails — enforce the layer matrix and config/env sync as tests.

The four-layer rules (`context/conventions.md`, `context/architecture.md`) and the
"`.env.example` mirrors `core/config.py`" contract are otherwise only prose. These
tests fail the build the moment a layer boundary is crossed or a config field has no
documented env key, which is cheaper than catching layer-rot by eye in review.

The scan walks the whole tree (``rglob``), not just top-level files, so a forbidden
import smuggled into a package submodule (``services/accounts/proxy.py``,
``core/telegram_client/_actions.py``, ...) is caught — earlier the guardrails only
saw ``services/accounts.py`` and similar, which gave false confidence after the
package splits landed.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from core.config import Settings, settings

_ROOT = Path(__file__).resolve().parent.parent


def _imported_modules(path: Path) -> set[str]:
    """All absolute imports in ``path`` as fully-qualified module names."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _python_modules(layer: str) -> list[Path]:
    """Every ``.py`` file under ``layer`` (recursive), including ``__init__.py``.

    ``__init__.py`` is intentionally not auto-skipped: package roots can violate
    layer rules just as easily as their siblings (a stray ``from telethon import``
    in ``services/accounts/__init__.py`` would be just as wrong as in any other
    submodule). Files with no real code contribute no imports, so leaving them in
    the scan costs nothing.
    """
    return sorted((_ROOT / layer).rglob("*.py"))


def _root_package(module: str) -> str:
    return module.split(".", 1)[0]


def _violations(layer: str, forbidden_roots: set[str]) -> list[str]:
    out: list[str] = []
    for path in _python_modules(layer):
        hits = {_root_package(m) for m in _imported_modules(path)} & forbidden_roots
        if hits:
            out.append(f"{path.relative_to(_ROOT).as_posix()} imports {sorted(hits)}")
    return out


def test_services_do_not_import_ui_or_sdks() -> None:
    # Services are pure logic: no SQLAlchemy/Telethon (gateways), and transport-
    # agnostic — never the API layer or FastAPI. (NiceGUI is gone but stays
    # banned so it can't creep back.)
    forbidden = {"nicegui", "sqlalchemy", "telethon", "fastapi", "api"}
    assert _violations("services", forbidden) == []


def test_schemas_depend_only_on_pydantic_and_stdlib() -> None:
    # Schemas are leaf contracts: no project layers, no third-party SDKs.
    forbidden = {"core", "services", "api", "sqlalchemy", "telethon", "nicegui", "fastapi"}
    assert _violations("schemas", forbidden) == []


def test_core_does_not_import_upper_layers() -> None:
    # Infrastructure must not depend on the layers above it.
    assert _violations("core", {"api", "services"}) == []


# api/ is the UI-thin top layer: it may import only services, schemas, fastapi,
# the stdlib, and from core ONLY config + logging — the executable firewall that
# replaced the old features→{config,logging} allowlist when api/ landed.
_API_ALLOWED_ROOTS = {"services", "schemas", "fastapi", "api"} | set(sys.stdlib_module_names)
_API_ALLOWED_CORE = {"core.config", "core.logging"}


def test_api_imports_only_allowlisted() -> None:
    violations: list[str] = []
    for path in _python_modules("api"):
        rel = path.relative_to(_ROOT).as_posix()
        for module in _imported_modules(path):
            if _root_package(module) == "core":
                if module not in _API_ALLOWED_CORE:
                    violations.append(f"{rel} imports {module}")
            elif _root_package(module) not in _API_ALLOWED_ROOTS:
                violations.append(f"{rel} imports {module}")
    assert violations == []


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


@pytest.mark.parametrize("layer", ["api", "core", "services", "schemas"])
def test_layer_directory_exists(layer: str) -> None:
    # Guard against a rename silently turning the import checks into no-ops.
    assert (_ROOT / layer).is_dir()
    assert _python_modules(layer), f"no modules found under {layer}/"


@pytest.mark.parametrize(
    ("layer", "subpath"),
    [
        ("api", "v1"),
        ("services", "accounts"),
        ("services", "warming"),
        ("core", "telegram_client"),
        ("core", "repositories"),
    ],
)
def test_subpackage_modules_are_checked(layer: str, subpath: str) -> None:
    """Rglob must reach into package submodules.

    Without this, the architecture checks only saw top-level files and silently
    skipped every file added under ``services/accounts/``, ``features/warming/``,
    ``core/telegram_client/``, etc. — exactly the surface that grew most after
    the package splits.
    """
    subpkg = (_ROOT / layer / subpath).resolve()
    reached = [p for p in _python_modules(layer) if p.resolve().is_relative_to(subpkg)]
    assert reached, f"{layer}/{subpath} submodules are not being checked"
