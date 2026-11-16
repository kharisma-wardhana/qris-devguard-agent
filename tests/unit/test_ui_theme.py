"""p9 ui-refresh theme: fixed markup only (docs/p9-ui-refresh/spec.md Behaviour §9; AC #13)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ui import theme

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app" / "ui" / "streamlit_app.py"
HOSTILE = "[click](https://evil.example/x) <img src=x onerror=alert(1)>"


def test_css_carries_the_reference_tokens_and_no_runtime_placeholders():
    ref = (ROOT / "docs/p9-ui-refresh/reference/qris-devguard-ui.html").read_text(encoding="utf-8")
    for token in ("--navy:#101d35", "--teal:#087f8c", "--canvas:#f5f7fb", "--amber-2:#fff4df", "--red:#c34e55"):
        assert token in theme.CSS and token in ref
    assert "{total}" not in theme.CSS and "%s" not in theme.CSS
    assert theme.style() == f"<style>{theme.CSS}</style>"


@pytest.mark.parametrize("value", ["3", 3.0, True, b"3", [3]])
def test_stat_refuses_anything_but_int_or_none(value):
    with pytest.raises(TypeError):
        theme.stat("threads", value)
    with pytest.raises(TypeError):
        theme.stat("ready", 1, total=value)
    with pytest.raises(TypeError):
        theme.count_head("sources", value)


def test_stat_renders_ints_and_a_dash_for_no_figure():
    assert ">7<" in theme.stat("threads", 7) and "My threads" in theme.stat("threads", 7)
    assert ">—<" in theme.stat("pending_forbidden", None) and "Approver roles only" in theme.stat("pending_forbidden", None)
    assert "of 2 checks up" in theme.stat("ready", 1, total=2)
    assert "of — gates" in theme.stat("gates_passing", None)


@pytest.mark.parametrize("group, key, word", [("thread", "awaiting_approval", "Needs review"), ("gate", "FAIL", "FAIL"),
                                              ("gate", "NOT RUN", "NOT RUN"), ("health", "up", "Online"),
                                              ("source", "synthetic", "SYNTHETIC"), ("kind", "critical", "Critical")])
def test_known_pills(group, key, word):
    assert f">{word}<" in theme.pill(group, key)


@pytest.mark.parametrize("group, key", [("thread", HOSTILE), ("gate", "pass"), (HOSTILE, "PASS"), ("gate", None),
                                        ("gate", ["PASS"]), ("thread", {"x": 1})])
def test_unknown_keys_render_unknown_and_never_the_key(group, key):
    out = theme.pill(group, key)
    assert ">Unknown<" in out and "evil.example" not in out and "pass<" not in out
    for fn in (theme.eyebrow, theme.banner, theme.panel_head, theme.label):
        assert "evil.example" not in fn(HOSTILE)
    assert "evil.example" not in theme.stat(HOSTILE, 1) and theme.description(HOSTILE) == ""


def test_stats_takes_only_stat_cards():
    row = theme.stats([theme.stat("threads", 1), theme.stat("awaiting", 0)])
    assert row.startswith('<div class="dg-stats n2">')
    with pytest.raises(TypeError):
        theme.stats([HOSTILE])


def test_every_page_has_an_eyebrow_and_description():
    for page in ("Overview", "Threads and chat", "Approvals", "Alerts", "Knowledge base", "Evaluations"):
        assert theme.PAGES[page][0] and theme.description(page)

# --- the app passes only theme output to raw HTML (AC #13) -------------------------------------------------------

def _is_theme_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "theme")


def test_streamlit_app_sends_only_theme_markup_to_st_html():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    html_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "html"]
    assert html_calls, "the app is expected to inject the theme"
    for call in html_calls:
        assert len(call.args) == 1 and not call.keywords and _is_theme_call(call.args[0]), ast.unparse(call)
        inner = call.args[0]
        if inner.func.attr == "stats":                      # stats([...]) must hold theme.stat(...) calls only
            (arg,) = inner.args
            assert isinstance(arg, ast.List) and all(_is_theme_call(e) and e.func.attr == "stat" for e in arg.elts)
    unsafe = [k for n in ast.walk(tree) if isinstance(n, ast.Call) for k in n.keywords if k.arg == "unsafe_allow_html"]
    assert not unsafe
