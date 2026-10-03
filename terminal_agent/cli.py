"""
CLI Interface for Linux Terminal Agent (lta).
Supports single prompt execution, interactive REPL mode, configuration management,
diagnostics, and history inspection.
"""

import sys
import argparse
import os
from typing import List, Optional
from terminal_agent.config import load_config, save_config, set_config_value, DEFAULT_CONFIG
from terminal_agent.core.context import get_system_context
from terminal_agent.core.engine import TerminalAgentEngine
from terminal_agent.core.history import HistoryManager
from terminal_agent.providers.factory import get_provider, PROVIDER_REGISTRY
from terminal_agent.core.privacy import redact, terminal_text
from terminal_agent.ui.colors import (
    bold, cyan, green, yellow, red, dim, gray, magenta, set_color_enabled
)

VERSION = "3.0.0"

BANNER = f"""
{cyan(bold('  _      _____          '))}
{cyan(bold(' | |    |_   _|   /\\    '))}  {bold('Linux Terminal Agent')} {dim(f'v{VERSION}')}
{cyan(bold(' | |      | |    /  \\   '))}  {green('Natural Language → Linux Shell Automation')}
{cyan(bold(' | |___  _| |_  / /\\ \\  '))}  {dim('Modular AI Engine (Online & Offline)')}
{cyan(bold(' |_____||_____|/_/  \\_\\ '))}
"""

def print_banner():
    print(BANNER)


def handle_config_command(sub_args: List[str]) -> None:
    """Handle `lta config [get/set/list/init]`."""
    cfg = load_config()
    action = sub_args[0] if sub_args else "list"

    if action == "list":
        import json
        print(bold("\n--- Current Configuration ---"))
        print(json.dumps(redact(cfg), indent=2))
        return

    if action == "init":
        save_config(DEFAULT_CONFIG)
        print(green("Configuration reset to default settings."))
        return

    if action == "get":
        if len(sub_args) < 2:
            print(red("Please specify a key. Example: lta config get provider"))
            return
        key = sub_args[1]
        val = cfg
        for part in key.split("."):
            val = val.get(part) if isinstance(val, dict) else None
        print(terminal_text(f"{key} = {redact({key: val})[key]}"))
        return

    if action == "set":
        if len(sub_args) < 3:
            print(red("Please specify key and value. Example: lta config set provider ollama"))
            return
        key = sub_args[1]
        val = sub_args[2]
        set_config_value(key, val)
        print(green(f"Updated: {key}"))
        return

    print(red(f"Unknown config action: {action}. Use 'list', 'get', 'set', or 'init'."))


def handle_test_command(cfg: dict, provider_name: Optional[str] = None) -> int:
    """Test connectivity to AI provider."""
    print(bold("\nTesting AI Provider Connectivity..."))
    provider = get_provider(name=provider_name, config=cfg)
    print(f"Target Provider: {bold(provider.name)} (Model: {provider.model})")
    print(f"Mode: {'Offline / Local' if provider.is_offline else 'Online Cloud API'}")

    ok, message = provider.test_connection()
    if ok:
        print(green(f"✔ Success: {message}"))
    else:
        print(red(f"✖ Failed: {message}"))
    return 0 if ok else 1


def handle_info_command(cfg: dict) -> None:
    """Display system context and agent setup."""
    print_banner()
    ctx = get_system_context()
    print(bold("--- System Environment ---"))
    print(f"Distribution:     {green(ctx.distro)} {ctx.distro_version}")
    print(f"Kernel:           {ctx.kernel} ({ctx.arch})")
    print(f"Shell:            {ctx.shell}")
    print(f"Package Manager:  {cyan(ctx.pkg_manager)}")
    print(f"User / Privs:     {ctx.user} ({'root' if ctx.is_root else ('can sudo' if ctx.can_sudo else 'unprivileged')})")
    print(f"Working Dir:      {ctx.cwd}")
    print(f"Init System:      {ctx.init_system}")

    print(bold("\n--- Agent Configuration ---"))
    active_provider = cfg.get("provider", "ollama")
    print(f"Active Provider:  {cyan(active_provider)}")
    print(f"Default Model:    {cfg.get('model', 'default')}")
    print(f"Available Backends: {', '.join(PROVIDER_REGISTRY.keys())}")


def handle_history_command() -> None:
    """Display recent execution history."""
    history = HistoryManager()
    entries = history.get_recent(limit=15)
    if not entries:
        print(dim("No command history found."))
        return

    print(bold("\n--- Recent Command History ---"))
    for i, e in enumerate(entries, 1):
        status_sym = green("✔") if e.exit_code == 0 else red("✖") if e.exit_code is not None else yellow("○")
        print(f"{i:2d}. {status_sym} [{e.provider}] {dim(e.query)}")
        print(f"    {cyan(e.command)}")


def run_interactive_repl(engine: TerminalAgentEngine) -> None:
    """Launch interactive REPL session."""
    print_banner()
    ctx = engine.context
    prov = engine.provider
    mode_str = green("Offline (Local)") if prov.is_offline else cyan("Online (Cloud)")
    print(f"System: {ctx.distro} | Shell: {ctx.shell} | Backend: {bold(prov.name)} [{mode_str}]")
    print(dim("Type your request in plain English or Persian. Type 'exit', 'quit', or 'help' to navigate.\n"))

    while True:
        try:
            cwd_display = os.path.basename(os.getcwd()) or "/"
            prompt_str = f"{cyan('lta')} {dim(f'({cwd_display})')} > "
            user_input = input(prompt_str).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ["exit", "quit", "q"]:
            print("Goodbye!")
            break

        if user_input.lower() == "help":
            print(bold("\nInteractive Commands:"))
            print("  help              Show this help")
            print("  info              Show system and provider information")
            print("  history           Show command execution history")
            print("  agent <goal>      Plan and verify a multi-step task")
            print("  sessions / resume Inspect or continue project work")
            print("  cd <directory>    Change project directory")
            print("  clear             Clear terminal screen")
            print("  exit / quit       Exit interactive mode")
            print("  <any prompt>      Translate instruction into shell command and execute\n")
            continue

        if user_input.lower() == "clear":
            os.system("clear")
            continue

        if user_input.lower() == "info":
            handle_info_command(engine.config)
            continue

        if user_input.lower() == "history":
            handle_history_command()
            continue

        if user_input.startswith("cd "):
            from pathlib import Path
            destination = Path(user_input[3:].strip()).expanduser().resolve()
            if destination.is_dir():
                os.chdir(destination)
                engine.context = get_system_context()
                engine.history = HistoryManager()
            else:
                print(red("Directory does not exist."))
            continue

        if user_input.startswith("agent "):
            from terminal_agent.core.workflow import AgentWorkflow
            workflow = AgentWorkflow(engine.provider, engine.config, auto_yes=engine.auto_yes, dry_run=engine.dry_run)
            try:
                workflow.run(workflow.create(user_input[6:]))
            except (ValueError, OSError, RuntimeError) as exc:
                print(red(terminal_text(exc)))
            continue

        if user_input.lower() in {"sessions", "resume"}:
            from terminal_agent.commands import handle_workflow_command
            try:
                workflow_args = [user_input.lower(), "-p", engine.provider.name, "-m", engine.provider.model]
                if engine.dry_run:
                    workflow_args.append("--dry-run")
                if engine.auto_yes:
                    workflow_args.append("--yes")
                handle_workflow_command(workflow_args, config=engine.config)
            except (ValueError, OSError, RuntimeError) as exc:
                print(red(terminal_text(exc)))
            continue

        engine.process_prompt(user_input)
        print()


def _main():
    for output in (sys.stdout, sys.stderr):
        if hasattr(output, "reconfigure"):
            output.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] in {"ops", "deploy"}:
        from terminal_agent.deployment.cli import main as operations_main
        return operations_main((["deploy"] if sys.argv[1] == "deploy" else []) + sys.argv[2:])
    from terminal_agent.commands import handle_workflow_command
    if len(sys.argv) > 1 and sys.argv[1] in {"skills", "sessions", "resume", "run-plan", "export-plan", "rollback", "doctor", "evaluate"}:
        return handle_workflow_command(sys.argv[1:])
    # Detect subcommands if first argument matches
    subcmds = {"config", "test", "info", "history"}
    if len(sys.argv) > 1 and sys.argv[1] in subcmds:
        cmd = sys.argv[1]
        cfg = load_config()
        if cmd == "config":
            handle_config_command(sys.argv[2:])
        elif cmd == "test":
            prov = sys.argv[2] if len(sys.argv) > 2 else None
            return handle_test_command(cfg, prov)
        elif cmd == "info":
            handle_info_command(cfg)
        elif cmd == "history":
            handle_history_command()
        return

    parser = argparse.ArgumentParser(
        prog="lta",
        description="Linux Terminal Agent - Convert natural language prompts into Linux shell commands.",
        epilog="Deployment and server operations: lta ops --help or lta deploy --help."
    )

    parser.add_argument("prompt", nargs="*", help="Natural language instruction for the terminal.")
    parser.add_argument("-p", "--provider", choices=list(PROVIDER_REGISTRY.keys()), help="AI provider backend (e.g. ollama, openai, groq, anthropic, gemini, local, rule_based).")
    parser.add_argument("-m", "--model", help="AI model name to use.")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-execute safe commands without prompting.")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Formulate and display command without executing.")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start interactive REPL mode.")
    parser.add_argument("--agent", action="store_true", help="Plan, execute and verify a multi-step task.")
    parser.add_argument("--adaptive", action="store_true", help="Review evidence and propose follow-up plans, bounded by --max-rounds.")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--local-only", action="store_true", help="Forbid cloud models; local endpoints must be loopback.")
    parser.add_argument("--project", default=os.getcwd(), help="Project directory for file tools and isolated session storage.")
    parser.add_argument("--no-color", action="store_true", help="Disable colored terminal output.")
    parser.add_argument("-v", "--version", action="version", version=f"Linux Terminal Agent {VERSION}")

    args = parser.parse_args()
    if args.adaptive and not args.agent:
        parser.error("--adaptive requires --agent")
    if not 1 <= args.max_rounds <= 10:
        parser.error("--max-rounds must be between 1 and 10")

    if args.no_color:
        set_color_enabled(False)

    cfg = load_config()
    if args.local_only:
        cfg["local_only"] = True
    from pathlib import Path
    args.project = str(Path(args.project).resolve())
    os.chdir(args.project)

    user_prompt = " ".join(args.prompt).strip()

    provider = get_provider(
        name=args.provider or cfg.get("provider"),
        model=args.model,
        config=cfg
    )

    if args.agent:
        if not user_prompt:
            parser.error("--agent requires a task description")
        from terminal_agent.core.workflow import AgentWorkflow
        workflow = AgentWorkflow(provider, cfg, args.project, args.yes, args.dry_run)
        session = workflow.create(user_prompt)
        if args.adaptive:
            session = workflow.run_adaptive(session, args.max_rounds)
        else:
            session = workflow.run(session)
        return 0 if session["status"] in {"completed", "planned"} else 1

    engine = TerminalAgentEngine(
        provider=provider,
        config=cfg,
        auto_yes=args.yes or cfg.get("auto_execute_safe", False),
        dry_run=args.dry_run
    )

    if args.interactive or not user_prompt:
        run_interactive_repl(engine)
    else:
        result, response = engine.process_prompt(user_prompt)
        return 0 if (result and result.succeeded) or (args.dry_run and response and response.command) else 1


def main():
    try:
        return _main()
    except (ValueError, OSError, RuntimeError) as exc:
        print(red("Error: " + terminal_text(exc)), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Inspect the saved session before resuming.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
