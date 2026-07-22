"""Keep Hindsight's selected client and supported floor synchronized."""

import ast
from pathlib import Path
import re
import tomllib

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version
import yaml

from tools.lazy_deps import feature_specs


ROOT = Path(__file__).parents[3]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _selected_requirement() -> Requirement:
    metadata = tomllib.loads(_read("pyproject.toml"))
    specs = metadata["project"]["optional-dependencies"]["hindsight"]
    assert len(specs) == 1
    requirement = Requirement(specs[0])
    assert requirement.name == "hindsight-client"
    assert len(requirement.specifier) == 1
    assert next(iter(requirement.specifier)).operator == "=="
    return requirement


def _assigned_string(relative: str, name: str) -> str:
    tree = ast.parse(_read(relative), filename=relative)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value = ast.literal_eval(node.value)
            assert isinstance(value, str)
            return value
    raise AssertionError(f"{name} assignment not found in {relative}")


def test_hindsight_client_version_surfaces_are_synchronized():
    selected_requirement = _selected_requirement()
    selected = Version(next(iter(selected_requirement.specifier)).version)

    lazy_specs = feature_specs("memory.hindsight")
    assert len(lazy_specs) == 1
    assert Requirement(lazy_specs[0]).specifier == selected_requirement.specifier

    minimum = Version(
        _assigned_string("plugins/memory/hindsight/__init__.py", "_MIN_CLIENT_VERSION")
    )
    assert minimum == selected

    manifest = yaml.safe_load(_read("plugins/memory/hindsight/plugin.yaml"))
    manifest_requirement = Requirement(manifest["pip_dependencies"][0])
    assert manifest_requirement.name == "hindsight-client"
    assert selected.major == 0
    expected_manifest_specifier = SpecifierSet(
        f">={selected},<0.{selected.minor + 2}"
    )
    assert manifest_requirement.specifier == expected_manifest_specifier

    locked = [
        package
        for package in tomllib.loads(_read("uv.lock"))["package"]
        if package["name"] == "hindsight-client"
    ]
    assert len(locked) == 1
    assert Version(locked[0]["version"]) == selected

    documented = [
        Requirement(spec.replace(" ", ""))
        for spec in re.findall(r"`(hindsight-client [^`]+)`", _read("plugins/memory/hindsight/README.md"))
    ]
    assert selected_requirement.specifier in [item.specifier for item in documented]
    assert manifest_requirement.specifier in [item.specifier for item in documented]
