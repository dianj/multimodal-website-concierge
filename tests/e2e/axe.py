"""axe-core accessibility checks inside Playwright tests (WCAG 2.0/2.1 levels A and AA).

axe-core 4.14.1 is vendored in vendor/axe.min.js (MPL-2.0, Deque Systems; licence header kept in the file), so the
check needs no network and gives the same result locally and in CI.
"""
from pathlib import Path

from playwright.sync_api import Page

AXE_SOURCE = (Path(__file__).parent / "vendor" / "axe.min.js").read_text(encoding="utf-8")
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]


# The preview fades colours in (CSS transitions); axe must see the finished colours, not a frame in between.
NO_TRANSITIONS = "*, *::before, *::after { transition: none !important; animation: none !important; }"


def axe_violations(page: Page) -> list[dict]:
    if not page.evaluate("() => typeof window.axe !== 'undefined'"):
        page.add_script_tag(content=AXE_SOURCE)
        page.add_style_tag(content=NO_TRANSITIONS)
    result = page.evaluate("tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", WCAG_TAGS)
    return result["violations"]


def assert_no_axe_violations(page: Page):
    violations = axe_violations(page)
    report = [f"{v['id']} ({v['impact']}): {v['help']} -> " + "; ".join(n["target"][0] for n in v["nodes"][:5])
              for v in violations]
    assert not violations, "\n".join(report)
