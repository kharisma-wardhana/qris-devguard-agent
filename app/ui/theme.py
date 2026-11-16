"""The demo UI's look, ported from docs/p9-ui-refresh/reference/qris-devguard-ui.html (spec Behaviour §1, §9; AC #13).

Rules for this module (no Streamlit import):
- `CSS` is a constant. Everything else returns fixed markup whose only variable parts are `int` values (rendered with
  `str(int)`) or labels looked up in this module's own dicts by key. A key the dicts don't know renders the fixed word
  "Unknown"; the key itself is never written out. So no text from the API, the manifest or the user reaches HTML.
- Non-int values raise TypeError (bool is refused too: it is an int subclass, but never a count).
"""

from __future__ import annotations

from typing import Any

UNKNOWN = ("Unknown", "s-neutral")

CSS = """
:root{
  --ink:#14213b;--muted:#6c7890;--line:#e7ebf2;--canvas:#f5f7fb;--surface:#fff;
  --navy:#101d35;--navy-2:#192944;--teal:#087f8c;--teal-2:#e5f5f5;
  --blue:#4569d4;--blue-2:#edf1ff;--green:#16865d;--green-2:#e9f7f0;
  --amber:#a96912;--amber-2:#fff4df;--red:#c34e55;--red-2:#fff0f0;
  --radius:15px;
}
.stApp,[data-testid="stAppViewContainer"]{background:var(--canvas);color:var(--ink);color-scheme:light}
[data-testid="stHeader"]{background:rgba(255,255,255,.9);border-bottom:1px solid var(--line);backdrop-filter:blur(14px)}
[data-testid="stMainBlockContainer"],.block-container{max-width:1470px;padding-top:3.2rem}
h1{font-size:25px!important;line-height:1.2!important;letter-spacing:-.8px;font-weight:760!important;color:var(--ink)!important}
h3{font-size:13px!important;font-weight:750!important;letter-spacing:-.1px;color:#293b56!important;padding-top:.4rem!important}
[data-testid="stMain"] [data-testid="stText"]{color:#52617a;font-size:12.5px}
[data-testid="stMain"] [data-testid="stCaptionContainer"]{color:var(--muted)}

/* sidebar */
[data-testid="stSidebar"]{background:var(--navy)}
[data-testid="stSidebar"] [data-testid="stSidebarContent"]{background:var(--navy)}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] [data-testid="stText"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p{color:#94a4bd!important;font-size:11px}
[data-testid="stSidebar"] [data-baseweb="select"]>div{background:#172641;border-color:#33435e;color:#eaf0fa}
[data-testid="stSidebar"] [data-baseweb="select"] svg{fill:#91a0ba}
[data-testid="stSidebar"] [role="radiogroup"]{gap:4px;width:100%}
[data-testid="stSidebar"] [role="radiogroup"] label{width:100%;margin:0;padding:10px 12px;border-radius:9px;transition:.18s}
[data-testid="stSidebar"] [role="radiogroup"] label>div:first-child{display:none}
[data-testid="stSidebar"] [role="radiogroup"] label p{color:#aebbd0;font-size:12.5px;font-weight:560}
[data-testid="stSidebar"] [role="radiogroup"] label:hover{background:#1b2c49}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked){background:#203a58;box-shadow:inset 3px 0 #36c0b8}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p{color:#fff}
[data-testid="stSidebar"] .stButton button{background:#263d5d;color:#d8e6f7;border:0;border-radius:7px;font-size:11px}
[data-testid="stSidebar"] .stButton button:hover{background:#2f4a6f;color:#fff}

/* buttons */
.stButton button,.stFormSubmitButton button{border-radius:9px;font-weight:650}
.stButton button[kind="primary"],.stFormSubmitButton button[kind="primaryFormSubmit"],
[data-testid="stBaseButton-primary"],[data-testid="stBaseButton-primaryFormSubmit"]{background:var(--teal);border-color:var(--teal);color:#fff}
.stButton button[kind="primary"]:hover,[data-testid="stBaseButton-primary"]:hover{background:#066d78;border-color:#066d78}

/* keyed containers (Streamlit adds st-key-<key> to keyed containers) */
div[class*="st-key-panel"]{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:0 2px 7px rgba(30,50,80,.025);padding:16px 18px}
div[class*="st-key-diagnosis"]{background:#f8fafc;border:1px solid #e8edf3;border-radius:12px;padding:14px 16px}
div[class*="st-key-approval"]{background:#fffbf2;border:1px solid #f1ddb7;border-radius:12px;padding:14px 16px}
div[class*="st-key-launch"]{background:linear-gradient(120deg,#f7fcfc,#f5f8ff);border:1px solid #deeeee;
  border-radius:13px;padding:16px 18px}
div[class*="st-key-row"]{border-bottom:1px solid #f0f2f6;padding:4px 0}

/* fixed markup from this module */
.dg-brand{display:flex;align-items:center;gap:11px;padding:2px 4px 18px}
.dg-mark{width:35px;height:35px;border-radius:11px;background:linear-gradient(145deg,#2ac3bd,#087f8c);display:grid;
  place-items:center;box-shadow:0 5px 14px rgba(0,173,170,.2);flex:none}
.dg-mark svg{width:21px;height:21px}
.dg-name{font-weight:750;letter-spacing:-.35px;font-size:15px;color:#eaf0fa}.dg-name span{color:#58d0c7}
.dg-sub{font-size:10px;color:#91a0ba;letter-spacing:1.4px;text-transform:uppercase;margin-top:1px}
.dg-env{display:inline-flex;align-items:center;gap:7px;background:#eff8f4;color:#287b5b;border:1px solid #dcefe5;
  padding:5px 10px;border-radius:20px;font-size:10px;font-weight:700}
.dg-env i{width:6px;height:6px;border-radius:50%;background:#27a875}
.dg-label{font-size:9px;letter-spacing:1.4px;text-transform:uppercase;color:#7889a5;font-weight:700;margin:6px 0 2px}
.dg-eyebrow{color:var(--teal);font-size:10px;font-weight:800;letter-spacing:1.4px;text-transform:uppercase;margin-bottom:-6px}
.dg-banner{display:flex;align-items:center;gap:9px;background:#f0f5ff;border:1px solid #dfe8ff;color:#536a9d;
  border-radius:10px;padding:9px 12px;font-size:12px}
.dg-banner b{color:#344f91}
.dg-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}
.dg-stats.n2{grid-template-columns:repeat(2,minmax(0,1fr))}.dg-stats.n3{grid-template-columns:repeat(3,minmax(0,1fr))}
.dg-stat{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:15px 16px;
  box-shadow:0 2px 6px rgba(30,50,80,.02)}
.dg-stat-top{color:#75829a;font-size:11px;font-weight:600}
.dg-stat-value{font-size:25px;letter-spacing:-.8px;font-weight:750;margin-top:8px;color:var(--ink)}
.dg-stat-foot{font-size:10px;color:#8793a7;margin-top:3px}
.dg-panel-title{font-size:13px;font-weight:750;color:var(--ink)}
.dg-panel-sub{font-size:10px;color:#8995a8;margin-top:2px}
.dg-approval-top{display:flex;align-items:center;gap:8px;color:#9b691c;font-size:12px;font-weight:800}
.dg-pill{display:inline-flex;align-items:center;gap:5px;border-radius:20px;padding:4px 8px;font-size:10px;font-weight:700;
  white-space:nowrap}
.dg-pill:before{content:'';width:5px;height:5px;border-radius:50%;background:currentColor}
.s-review{background:var(--amber-2);color:#9a650e}.s-ok{background:var(--green-2);color:#16744f}
.s-running{background:var(--blue-2);color:#4865c1}.s-bad{background:var(--red-2);color:#b7464d}
.s-neutral{background:#eef1f5;color:#5d6b82}

@media(max-width:1100px){.dg-stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:540px){.dg-stats,.dg-stats.n3{grid-template-columns:1fr 1fr;gap:8px}.dg-stat{padding:12px}
  .dg-stat-value{font-size:22px}h1{font-size:22px!important}}
"""

PILLS: dict[str, dict[str, tuple[str, str]]] = {
    "thread": {"idle": ("Open", "s-neutral"), "running": ("Investigating", "s-running"),
               "awaiting_approval": ("Needs review", "s-review"), "completed": ("Resolved", "s-ok"),
               "failed": ("Failed", "s-bad")},
    "gate": {"PASS": ("PASS", "s-ok"), "FAIL": ("FAIL", "s-bad"), "NOT RUN": ("NOT RUN", "s-review"),
             "STALE": ("STALE", "s-review")},
    "kind": {"critical": ("Critical", "s-bad"), "gate": ("Gate", "s-running")},
    "health": {"up": ("Online", "s-ok"), "down": ("Offline", "s-bad")},
    "source": {"synthetic": ("SYNTHETIC", "s-running"), "reference": ("Reference only", "s-neutral")},
    "approval": {"pending": ("Pending", "s-review"), "approved": ("Approved", "s-ok"), "rejected": ("Rejected", "s-bad"),
                 "expired": ("Expired", "s-neutral"), "executed": ("Executed", "s-ok")},
}

STATS: dict[str, tuple[str, str]] = {     # key -> (label, foot); "{total}" in a foot is filled with an int
    "threads": ("My threads", "Threads you created (newest 50)"),
    "awaiting": ("Awaiting approval", "Paused until an approver decides"),
    "pending": ("Pending approvals", "Need a human decision"),
    "pending_forbidden": ("Pending approvals", "Approver roles only"),
    "ready": ("API readiness", "of {total} checks up"),
    "alerts_total": ("Alerts received", "Stored by the webhook receiver"),
    "alerts_firing": ("Firing", "Severity ALERT"),
    "gates_passing": ("Gates passing", "of {total} gates"),
    "not_passing": ("Not passing", "Gates below their threshold"),
    "critical_not_passing": ("Critical not passing", "Any one blocks the release"),
}

PAGES: dict[str, tuple[str, str]] = {     # page option value -> (eyebrow, description)
    "Overview": ("Developer command centre", "Investigate QRIS API issues with grounded answers and deterministic validation."),
    "Threads and chat": ("Case workspace", "Evidence-backed diagnosis, deterministic tool results and human decisions."),
    "Approvals": ("Human in the loop", "Every write waits for an approver. Approvals are bound to the exact payload."),
    "Alerts": ("Observability", "Langfuse alerts received by the local webhook receiver. Receiving one never triggers anything."),
    "Knowledge base": ("Ground truth", "Versioned sources used to ground every diagnosis."),
    "Evaluations": ("Quality gates", "Measured results from the newest release-gate report."),
}

PANELS: dict[str, tuple[str, str]] = {    # key -> (title, subtitle)
    "launch": ("Start a new investigation", "Pick a synthetic example. A new thread opens with the example filled in."),
    "recent": ("Recent investigations", "Your newest threads"),
    "pinned": ("Pinned references", "Approved sources from the knowledge base"),
    "health": ("System health", "API readiness checks"),
    "tools": ("Deterministic tool results", "Validity is decided by the tool, never by the LLM."),
    "gates": ("Release gates", "A critical failure blocks the release."),
    "sources": ("Indexed sources", "Approved and retrievable"),
}

BANNERS: dict[str, str] = {
    "demo": "<b>Demo workspace</b> — every record here is synthetic. No production system is connected.",
    "kb": "Only approved, retrievable sources are listed. Each answer cites chunks from these documents.",
    "eval": "Measured results from the newest release-gate report (scripts/release_gate.py). Thresholds come from "
            "configs/eval_thresholds.yaml. Nothing on this page is a target.",
}

_MARK = ('<svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" '
         'stroke-linejoin="round"><path d="M12 3 20 7.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/></svg>')
_SHIELD = ('<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8">'
           '<path d="M12 3 20 7v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4Z"/><path d="M12 8v5m0 3h.01"/></svg>')


def _int(v: Any) -> str:
    if v is None:
        return "—"
    if type(v) is not int:                 # noqa: E721  (bool and str are refused on purpose)
        raise TypeError("theme values must be int or None")
    return str(v)


def _lookup(table: dict[str, Any], key: Any) -> Any:
    return table.get(key) if isinstance(key, str) else None


def style() -> str:
    return f"<style>{CSS}</style>"


def brand() -> str:
    return (f'<div class="dg-brand"><div class="dg-mark">{_MARK}</div><div><div class="dg-name">DevGuard '
            '<span>AI</span></div><div class="dg-sub">QRIS integration suite</div></div></div>'
            '<span class="dg-env"><i></i>SANDBOX</span>')


def label(text_key: str) -> str:
    """A small sidebar section label from a fixed set."""
    words = {"workspace": "Workspace", "identity": "Demo identity", "health": "API health"}
    return f'<div class="dg-label">{words.get(text_key, "") if isinstance(text_key, str) else ""}</div>'


def eyebrow(page: Any) -> str:
    entry = _lookup(PAGES, page)
    return f'<div class="dg-eyebrow">{entry[0] if entry else ""}</div>'


def description(page: Any) -> str:
    entry = _lookup(PAGES, page)
    return entry[1] if entry else ""


def banner(key: Any) -> str:
    text = _lookup(BANNERS, key)
    return f'<div class="dg-banner">{text or ""}</div>'


def pill(group: Any, key: Any) -> str:
    table = _lookup(PILLS, group) or {}
    text, cls = _lookup(table, key) or UNKNOWN
    return f'<span class="dg-pill {cls}">{text}</span>'


def panel_head(key: Any) -> str:
    title, sub = _lookup(PANELS, key) or ("", "")
    return f'<div class="dg-panel-title">{title}</div><div class="dg-panel-sub">{sub}</div>'


def count_head(key: Any, n: int | None) -> str:
    """A panel title with a count, e.g. "Indexed sources" / "5 listed"."""
    title, _ = _lookup(PANELS, key) or ("", "")
    return f'<div class="dg-panel-title">{title}</div><div class="dg-panel-sub">{_int(n)} listed</div>'


def approval_head() -> str:
    return f'<div class="dg-approval-top">{_SHIELD} Human approval required</div>'


def stat(key: Any, value: int | None, total: int | None = None) -> str:
    label_text, foot = _lookup(STATS, key) or ("Unknown", "")
    v = _int(value)
    foot = foot.replace("{total}", _int(total))
    return (f'<div class="dg-stat"><div class="dg-stat-top">{label_text}</div>'
            f'<div class="dg-stat-value">{v}</div><div class="dg-stat-foot">{foot}</div></div>')


def stats(cards: list[str]) -> str:
    """A responsive row of cards built by `stat()` (2, 3 or 4 per row)."""
    cls = {2: " n2", 3: " n3"}.get(len(cards), "")
    for c in cards:
        if not (isinstance(c, str) and c.startswith('<div class="dg-stat">')):
            raise TypeError("stats() takes cards built by stat()")
    return f'<div class="dg-stats{cls}">{"".join(cards)}</div>'
