"""The evaluation code is not part of the product (p9-final spec, Architecture).

- No module under app/ outside app/evaluation/ imports app.evaluation.
- No module anywhere under app/ imports `anthropic` at module level; app/evaluation/judge.py imports it only
  inside a function (the real client constructor).
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


def _imports(tree: ast.AST, top_level_only: bool) -> set[str]:
    nodes = tree.body if top_level_only else list(ast.walk(tree))
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _modules():
    for path in APP.rglob("*.py"):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_runtime_never_imports_evaluation():
    offenders = []
    for path, tree in _modules():
        if "evaluation" in path.relative_to(APP).parts:
            continue
        if any(n == "app.evaluation" or n.startswith("app.evaluation.") for n in _imports(tree, False)):
            offenders.append(str(path))
    assert offenders == []


def test_anthropic_never_imported_at_module_level():
    offenders = [str(p) for p, tree in _modules()
                 if any(n == "anthropic" or n.startswith("anthropic.") for n in _imports(tree, True))]
    assert offenders == []


def test_anthropic_only_imported_in_evaluation():
    offenders = [str(p) for p, tree in _modules()
                 if "evaluation" not in p.relative_to(APP).parts
                 and any(n == "anthropic" or n.startswith("anthropic.") for n in _imports(tree, False))]
    assert offenders == []
