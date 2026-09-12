"""What the package imports of itself, and in which direction."""

from pathlib import Path

import ast
import snapforge

from .harness import check


def imported_here(tree):
    """The sibling modules a file imports at module level, and no other."""
    found = set()

    def walk(node, in_function):
        for child in ast.iter_child_nodes(node):
            nested = in_function or isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if not nested and isinstance(child, ast.ImportFrom) \
                    and child.level == 1:
                if child.module:
                    found.add(child.module.split(".")[0])
                else:
                    found.update(a.name for a in child.names)
            walk(child, nested)

    walk(tree, False)
    return found

root = Path(snapforge.__file__).parent
graph = {p.stem: imported_here(ast.parse(p.read_text(encoding="utf-8")))
         for p in sorted(root.glob("*.py")) if p.stem != "__init__"}


@check("no module-level import in the package closes a cycle")
def _():
    # A cycle is an ImportError, or worse a half-built module.
    def cycle_from(start):
        stack = [(start, [start])]
        seen = set()
        while stack:
            name, path = stack.pop()
            for other in sorted(graph.get(name, ())):
                if other == start:
                    return path + [other]
                if other in graph and (name, other) not in seen:
                    seen.add((name, other))
                    stack.append((other, path + [other]))
        return None

    for name in sorted(graph):
        found = cycle_from(name)
        assert found is None, " -> ".join(found)


@check("the import graph read here is the one the package really has")
def _():
    # An empty graph would pass the cycle test above for the wrong reason.
    assert "arch" in graph["classify"], "classify imports arch, and this missed it"
    assert "db" in graph["adopt"], "adopt imports db, and this missed it"
    assert "rewrite" in graph["recipe"], "recipe imports rewrite, and this missed it"
    # db reaches adopt inside a function, which is not an edge.
    assert "adopt" not in graph["db"], \
        "db imports adopt inside a function and this counted it anyway"
    for name, edges in sorted(graph.items()):
        for other in sorted(edges):
            assert other in graph, f"{name} imports a missing .{other}"
