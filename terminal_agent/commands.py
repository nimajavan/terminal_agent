"""Extended commands for skills, sessions, reviewed plans and recovery."""
import argparse
import json
import os
from pathlib import Path
from terminal_agent.config import load_config
from terminal_agent.core.planning import parse_plan
from terminal_agent.core.privacy import redact, terminal_text
from terminal_agent.core.session import SessionStore
from terminal_agent.core.skills import SkillRegistry
from terminal_agent.core.storage import atomic_json, exclusive_lock
from terminal_agent.core.workflow import AgentWorkflow
from terminal_agent.providers.factory import get_provider
from terminal_agent.ui.dashboard import show_plan


def common(parser):
    parser.add_argument("--project", default=os.getcwd())
    parser.add_argument("-p", "--provider")
    parser.add_argument("-m", "--model")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-approve recognized read-only actions only")
    parser.add_argument("-d", "--dry-run", action="store_true")
    parser.add_argument("--local-only", action="store_true")


def handle_workflow_command(argv):
    parser = argparse.ArgumentParser(prog="lta " + argv[0])
    command = argv[0]
    common(parser)
    if command == "skills":
        parser.add_argument("action", choices=["list", "install", "run"], nargs="?", default="list")
        parser.add_argument("name", nargs="?")
        parser.add_argument("--service")
        parser.add_argument("--url")
        parser.add_argument("--repair", action="store_true")
    elif command == "doctor":
        parser.add_argument("service")
        parser.add_argument("--url")
        parser.add_argument("--repair", action="store_true", help="Propose restart after diagnostics; still requires confirmation")
    elif command == "sessions":
        parser.add_argument("identity", nargs="?")
    elif command == "resume":
        parser.add_argument("identity", nargs="?", default="latest")
        parser.add_argument("--retry", action="store_true", help="Explicitly retry the failed or interrupted step")
        parser.add_argument("--replan", action="store_true")
        parser.add_argument("--instruction", default="Continue toward the goal using the evidence")
        parser.add_argument("--adaptive", action="store_true")
        parser.add_argument("--max-rounds", type=int, default=3)
    elif command == "run-plan":
        parser.add_argument("path")
    elif command == "export-plan":
        parser.add_argument("path")
        parser.add_argument("--session", default="latest")
    elif command == "rollback":
        parser.add_argument("backup_id")
    elif command == "evaluate":
        parser.add_argument("--output")
    args = parser.parse_args(argv[1:])
    cfg = load_config()
    if args.local_only:
        cfg["local_only"] = True
    project = Path(args.project).resolve()
    if not project.is_dir():
        raise ValueError("Project directory does not exist")
    store = SessionStore(project)
    registry = SkillRegistry()
    if command == "evaluate":
        from terminal_agent.evaluation import evaluate
        report = evaluate()
        print(json.dumps(report, indent=2))
        if args.output:
            atomic_json(args.output, report)
        return 0 if report["failed"] == 0 else 1
    if command == "skills" and args.action == "list":
        for name, description in registry.list().items():
            print("%-12s %s" % (name, terminal_text(description)))
        return 0
    if command == "skills" and args.action == "install":
        if not args.name:
            parser.error("Provide a skill JSON path")
        print("Installed local declarative skill: " + registry.install(args.name))
        return 0
    if command == "sessions":
        if args.identity:
            session = store.load(args.identity)
            show_plan(session["plan"], session)
            print(terminal_text(json.dumps(redact(session), ensure_ascii=False, indent=2)))
        else:
            for session in store.list():
                print("%s  %-16s %s" % (session["id"], session["status"], terminal_text(session["goal"])))
        return 0
    if command == "export-plan":
        destination = Path(args.path)
        if destination.exists():
            raise ValueError("Export destination exists; choose a new path")
        atomic_json(destination, store.load(args.session)["plan"])
        print("Exported plan: " + str(destination.resolve()))
        return 0
    # Built-in plans and rollback work with no network/model credentials.
    provider_name = args.provider or (cfg.get("provider") if command == "resume" and (args.replan or args.adaptive) else "rule_based")
    workflow = AgentWorkflow(get_provider(provider_name, args.model, cfg), cfg, project, args.yes, args.dry_run, store)
    if command == "rollback":
        print(terminal_text(workflow.tools.preview_restore(args.backup_id)))
        if args.dry_run:
            return 0
        if input("Restore this file backup? [y/N]: ").strip().lower() not in {"y", "yes"}:
            return 1
        with exclusive_lock(store.directory / "execution.lock"):
            print("Restored: " + workflow.tools.restore(args.backup_id))
        return 0
    if command == "run-plan":
        source = Path(args.path)
        if source.stat().st_size > 300000:
            raise ValueError("Plan file is too large")
        session = workflow.from_plan(parse_plan(source.read_text(encoding="utf-8"), cfg.get("max_steps", 12)))
    elif command == "resume":
        session = workflow.resume(args.identity)
        if args.replan:
            session = workflow.replan(session, args.instruction)
        if args.adaptive:
            session = workflow.run_adaptive(session, args.max_rounds, retry=args.retry)
            return 0 if session["status"] in {"completed", "planned"} else 1
    else:
        if command == "doctor":
            skill = "nginx" if args.service == "nginx" else "systemd"
        else:
            if not args.name:
                parser.error("Provide a skill name")
            skill = args.name
        session = workflow.from_plan(registry.plan(skill, args.service, args.url, args.repair))
    session = workflow.run(session, retry=getattr(args, "retry", False))
    return 0 if args.dry_run or session["status"] == "completed" else 1
