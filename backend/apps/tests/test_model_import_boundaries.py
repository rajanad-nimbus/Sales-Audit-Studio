import ast
from pathlib import Path


def test_feature_code_imports_persistence_from_owning_apps():
    backend_root = Path(__file__).resolve().parents[2]
    app_root = backend_root / "apps"
    violations = []

    for source_path in app_root.rglob("*.py"):
        if "tests" in source_path.relative_to(app_root).parts:
            continue
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        for node in ast.walk(tree):
            imports_compatibility_facade = (
                isinstance(node, ast.ImportFrom) and node.module == "models"
            ) or (
                isinstance(node, ast.Import)
                and any(alias.name == "models" for alias in node.names)
            )
            if imports_compatibility_facade:
                relative_path = source_path.relative_to(backend_root)
                violations.append(f"{relative_path}:{node.lineno}")

    assert not violations, "Feature modules must import models from their owning apps:\n" + "\n".join(violations)
