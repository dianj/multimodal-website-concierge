"""LLM evaluation: does the real model pick the right tools and end in the right site state?

Unlike the deterministic tests (which use a fake LLM), this runs real prompts against the models
in LM Studio. Each case starts from a fresh default site in a throwaway database and is graded on
the *outcome* (final JSON state, tools used, reply), not on exact wording.

Usage (LM Studio must be running; models are loaded/unloaded one at a time):
    python evals/run_evals.py                      # all models in ALLOWED_MODELS
    python evals/run_evals.py --models gemma-4-26b-a4b-it
Results: evals/results/latest.md (summary table) and a JSON file with every reply.
"""
import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["WEBSITE_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="concierge-evals-"), "eval_cms.db")

import server  # noqa: E402

RESULTS_DIR = os.path.join(ROOT, "evals", "results")


# --- helpers for checks ----------------------------------------------------------------

def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def luminance(hex_color):
    return server.color_contrast_ratio(hex_color, "#000000") / 21  # 0 (black) .. 1 (white), monotonic


def called(run, *names):
    return any(t.startswith(names) for t in run["tools"])


def cyrillic(text):
    return bool(re.search(r"[Ѐ-ӿ]", text))


def mentions(text, *words):
    lower = text.lower()
    return any(w in lower for w in words)


@dataclass
class Case:
    id: str
    prompt: str
    check: Callable[[dict], bool]
    note: str


BRIEF = open(os.path.join(ROOT, "sample_attachments", "new_services_brief.txt"), encoding="utf-8").read()

CASES = [
    Case("primary-color-en", "Change the primary color to a dark green.",
         lambda r: (lambda c: c[1] > c[0] and c[1] > c[2] and luminance(r["state"]["theme"]["primary_color"]) < 0.3)(rgb(r["state"]["theme"]["primary_color"])),
         "primary color becomes a dark green"),
    Case("primary-color-bg", "Смени основния цвят на тъмносин.",
         lambda r: (lambda c: c[2] > c[0] and c[2] >= c[1])(rgb(r["state"]["theme"]["primary_color"])) and cyrillic(r["reply"]),
         "primary color becomes blue; reply in Bulgarian"),
    Case("reorder", "Move the testimonials section above the services section.",
         lambda r: r["state"]["section_order"].index("testimonials") < r["state"]["section_order"].index("services"),
         "testimonials ordered before services"),
    Case("dark-theme-atomic", "Make the whole site dark: a dark background with light text.",
         lambda r: luminance(r["state"]["theme"]["background_color"]) < 0.2 and luminance(r["state"]["theme"]["text_color"]) > 0.6,
         "dark background + light text in the light theme (needs one atomic call)"),
    Case("dark-mode-switcher", "Add dark mode with a Light / Dark / Auto switcher in a separate bar at the top of the site.",
         lambda r: r["state"]["theme"]["dark_mode"]["enabled"] and r["state"]["theme"]["dark_mode"]["switcher_position"] == "top_bar",
         "dark mode enabled, switcher in top bar"),
    Case("slider-placeholder", "Add two more testimonials to the What Clients Say section.",
         lambda r: len(r["state"]["sections"]["testimonials"]["items"]) >= 3 and mentions(r["reply"], "placeholder", "replace", "real"),
         "3+ testimonials (slider) and placeholder warning"),
    Case("content-brief", f"Please apply this brief to the website.\n\n[Attached Document: new_services_brief.txt]\n{BRIEF}",
         lambda r: {"AI Workflow Automation", "E-Commerce Modernization", "24/7 Managed Security"} <= {i["name"] for i in r["state"]["sections"]["services"]["items"]}
         and "08:30" in r["state"]["sections"]["contact"]["working_hours"],
         "3 services replaced and working hours updated from a brief"),
    Case("duplicate-paragraph", "Duplicate the hero subtitle so there are two identical paragraphs one under the other.",
         lambda r: (lambda p: len(p) == 2 and p[0] == p[1])(r["state"]["sections"]["hero"]["subtitle"].split("\n\n")),
         "subtitle has two identical paragraphs"),
    Case("paragraph-bg", "Добави втори параграф в подзаглавието на началната секция: „Работим с клиенти от цял свят.“",
         lambda r: len(r["state"]["sections"]["hero"]["subtitle"].split("\n\n")) == 2 and "цял свят" in r["state"]["sections"]["hero"]["subtitle"],
         "second paragraph added (Bulgarian request)"),
    Case("card-outline-dark", "In dark mode, the service card borders look too dark. Please give the cards a lighter, white-ish outline.",
         lambda r: luminance(r["state"]["theme"]["card_style"]["dark"]["border_color"]) > 0.6 and called(r, "update_card_style"),
         "dark-mode card border becomes light"),
    Case("hover-cards", "Add a subtle hover effect to the service cards: lift them a little and add a shadow.",
         lambda r: (lambda h: h["enabled"] and (h["lift_px"] > 0 or h["scale"] > 1) and h["shadow"] != "none")(r["state"]["theme"]["hover_effects"]["service_cards"]),
         "service-card hover with movement and shadow"),
    Case("guardrail-contrast", "Make the background black and the text dark navy blue.",
         lambda r: server.color_contrast_ratio(r["state"]["theme"]["background_color"], r["state"]["theme"]["text_color"]) >= 4.5
         and mentions(r["reply"], "contrast", "readab", "legib", "hard to read"),
         "unreadable combination is not saved; reply explains contrast"),
    Case("mandatory-phone", "Remove the phone number from the contact section.",
         lambda r: r["state"]["sections"]["contact"]["phone"].strip() != "",
         "phone number is kept (guardrail)"),
    Case("honesty-partial", "Change the services heading to 'What We Do' and also add a newsletter signup form at the bottom of the page.",
         lambda r: r["state"]["sections"]["services"]["heading"] == "What We Do" and mentions(r["reply"], "newsletter")
         and mentions(r["reply"], "can't", "cannot", "not able", "unable", "don't have", "not possible", "ticket"),
         "does the possible part, honestly reports the impossible part"),
    Case("verify-state", "Can you check what the current primary color of the site is?",
         lambda r: called(r, "get_current_site_state") and "#2563eb" in r["reply"].lower(),
         "reads the state before answering"),
    Case("approval-ticket", "I'm happy with how the site looks now. Please approve it and send it to the developers.",
         lambda r: called(r, "create_approval_ticket"),
         "creates the CR ticket on approval"),
]


def reset_site():
    if os.path.exists(server.DB_PATH):
        os.remove(server.DB_PATH)
    server.init_db()


def run_case(model, case):
    reset_site()
    history = [{"role": "user", "content": [{"type": "text", "text": case.prompt}]}]
    tools = []
    started = time.time()
    try:
        msg = server.run_llm_turn(model, [], history, tools)
        reply = msg.content or ""
        error = None
    except Exception as e:  # count infrastructure errors as failures, but keep going
        reply, error = "", str(e)
    seconds = time.time() - started
    run = {"reply": reply, "tools": tools, "state": server.get_latest_state()[1], "seconds": seconds, "error": error}
    try:
        passed = error is None and bool(case.check(run))
    except Exception as e:
        passed, run["error"] = False, f"check failed: {e}"
    return {"case": case.id, "passed": passed, **{k: v for k, v in run.items() if k != "state"},
            "final_state": run["state"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=[m for m, _ in server.ALLOWED_MODELS])
    parser.add_argument("--cases", nargs="*", help="run only these case ids")
    args = parser.parse_args()
    cases = [c for c in CASES if not args.cases or c.id in args.cases]
    labels = dict(server.ALLOWED_MODELS)
    results = {}

    for model in args.models:
        print(f"\n=== {model}: loading in LM Studio ...", flush=True)
        switched = server.switch_model(model)
        if switched["status"] != "success":
            print(f"    could not load: {switched['message']}")
            continue
        results[model] = []
        for case in cases:
            r = run_case(model, case)
            results[model].append(r)
            print(f"    {'PASS' if r['passed'] else 'FAIL'}  {case.id:<22} {r['seconds']:5.1f}s  tools={len(r['tools'])}", flush=True)

    server.switch_model(server.MODEL_NAME)  # leave LM Studio with the default model loaded

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(RESULTS_DIR, f"results-{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    lines = [f"# Eval results ({stamp})", "",
             f"{len(cases)} cases, one fresh site per case, graded on the final state / tools / reply. "
             "Generated by `python evals/run_evals.py`.", "",
             "| Model | Passed | Median time per turn |", "|---|---|---|"]
    for model, rows in results.items():
        passed = sum(r["passed"] for r in rows)
        lines.append(f"| {labels.get(model, model)} | **{passed}/{len(rows)}** | {statistics.median(r['seconds'] for r in rows):.1f} s |")
    lines += ["", "| Case | What is checked | " + " | ".join(labels.get(m, m) for m in results) + " |",
              "|---|---|" + "---|" * len(results)]
    for case in cases:
        cells = []
        for model in results:
            r = next(x for x in results[model] if x["case"] == case.id)
            cells.append("✅" if r["passed"] else "❌")
        lines.append(f"| `{case.id}` | {case.note} | " + " | ".join(cells) + " |")
    with open(os.path.join(RESULTS_DIR, "latest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
