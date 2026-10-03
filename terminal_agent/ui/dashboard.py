"""Dependency-free terminal dashboard; degrades to plain text in redirected output."""
from terminal_agent.ui.colors import bold, cyan, green, red, yellow
from terminal_agent.core.privacy import terminal_text


def show_plan(plan, session=None):
    print("\n" + bold(terminal_text(plan["goal"])))
    print(terminal_text(plan.get("summary", "")))
    if session:
        print("Session: " + session["id"] + " | " + session["status"])
    outcomes = {r["step_id"]: r["status"] for r in (session or {}).get("results", [])}
    for number, step in enumerate(plan["steps"], 1):
        status = outcomes.get(step["id"], "pending")
        print(" %2d. [%s] %s (%s)" % (number, status, terminal_text(step["title"]), step["tool"]))
        if step.get("depends_on"):
            print("     after: " + ", ".join(step["depends_on"]))
        if step.get("verify"):
            for check in step["verify"]:
                print("     verify %s: exit=%s, stdout contains %s" % (check["tool"], check.get("expected_exit", 0), terminal_text(repr(check.get("contains", "")))))


def show_result(result, status, show_output=True):
    color = green if status == "passed" else red
    print(color("[%s] exit=%s | %.2fs" % (status.upper(), result.exit_code, result.duration_seconds)))
    if result.stdout and show_output:
        print(terminal_text(result.stdout))
    if result.stderr:
        print(red(terminal_text(result.stderr)))


def review_action(preview, assessment, auto_yes=False, editable=False):
    print(cyan(terminal_text(preview)))
    print(yellow(assessment.level.value + ": " + "; ".join(assessment.reasons)))
    if assessment.is_blocked:
        return "blocked", None
    if auto_yes and not assessment.requires_confirmation:
        return "run", None
    while True:
        try:
            choice = input("[y] run / [n] pause / [e] edit / [q] stop (default: pause): ").strip().lower()
            if choice in {"y", "yes"}:
                return "run", None
            if choice in {"", "n", "no", "q", "quit"}:
                return "pause", None
            if choice in {"e", "edit"}:
                if not editable:
                    print("Edit a saved plan with 'lta export-plan', then use 'lta run-plan'.")
                    continue
                command = input("New shell command: ").strip()
                return ("edit", command) if command else ("pause", None)
        except (EOFError, KeyboardInterrupt):
            return "pause", None
