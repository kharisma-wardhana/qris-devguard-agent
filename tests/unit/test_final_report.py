"""Final report shape (p9-final spec AC #28): sections, Mermaid diagram, length, links and screenshots."""

import re
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[2] / "docs" / "p9-final-evaluation-packaging"
REPORT = (FOLDER / "final-report.md").read_text(encoding="utf-8")
SECTIONS = ["1. Use case", "2. Architecture", "3. Measured results", "4. Observability and alerts", "5. Reproducibility",
            "6. Limitations"]


def test_required_sections_in_order():
    found = re.findall(r"^## (.+)$", REPORT, re.M)
    assert found == SECTIONS


def test_has_a_mermaid_architecture_diagram():
    assert re.search(r"```mermaid\n(?:.|\n)*?flowchart(?:.|\n)*?```", REPORT)


def test_at_most_2500_words_outside_code_and_tables():
    text = re.sub(r"```(?:.|\n)*?```", "", REPORT)
    prose = [ln for ln in text.splitlines() if not ln.lstrip().startswith("|")]
    assert len(" ".join(prose).split()) <= 2500


def test_links_and_images_resolve_and_screenshots_are_small():
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", REPORT):
        if target.startswith(("http://", "https://")):
            continue
        assert (FOLDER / target).exists(), target
    for shot in (FOLDER / "screenshots").glob("*.png") if (FOLDER / "screenshots").is_dir() else []:
        assert shot.stat().st_size <= 1024 * 1024, shot.name


def test_states_measured_not_targets_and_model_graded_calibration():
    assert "measured result" in REPORT.lower()
    assert "model-graded" in REPORT.lower() and "no independent human check" in REPORT
