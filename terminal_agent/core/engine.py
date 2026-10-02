"""
Core Agent Orchestration Engine.
Coordinates context collection, LLM generation, safety evaluation,
user interaction, command execution, and history recording.
"""

import sys
from typing import Optional, Tuple
from terminal_agent.core.context import get_system_context, SystemContext
from terminal_agent.core.safety import analyze_command, DangerLevel, SafetyAssessment
from terminal_agent.core.executor import CommandExecutor, ExecutionResult
from terminal_agent.core.history import HistoryManager
from terminal_agent.providers.base import BaseProvider, AgentResponse
from terminal_agent.providers.factory import get_provider
from terminal_agent.ui.prompts import render_command_card, prompt_user_action
from terminal_agent.ui.colors import green, red, yellow, bold, dim, cyan
from terminal_agent.providers.router import ModelRouter
from terminal_agent.core.privacy import redact, terminal_text

class TerminalAgentEngine:
    def __init__(
        self,
        provider: Optional[BaseProvider] = None,
        config: Optional[dict] = None,
        auto_yes: bool = False,
        dry_run: bool = False
    ):
        self.config = config or {}
        self.provider = provider or get_provider(config=self.config)
        self.auto_yes = auto_yes
        self.dry_run = dry_run
        self.context: SystemContext = get_system_context()
        self.history = HistoryManager()
        self.executor = CommandExecutor(default_timeout=self.config.get("timeout", 120))
        self.router = ModelRouter(self.provider, self.config)

    def process_prompt(self, prompt: str) -> Tuple[Optional[ExecutionResult], Optional[AgentResponse]]:
        """Process a single natural language prompt and manage lifecycle."""
        if not prompt.strip():
            return None, None

        # 1. Ask provider to generate command
        history_context = self.history.get_context_for_prompt(limit=4)
        try:
            response = self.router.generate(
                prompt=prompt,
                context=self.context,
                history=history_context
            )
        except Exception as e:
            print(red("\nModel generation error: " + terminal_text(e)))
            return None, None

        command = response.clean_command()
        if not command:
            print(yellow("\n⚠ No command could be formulated for this request."))
            return None, response

        # 2. Safety Assessment
        assessment = analyze_command(command)

        # 3. Render command card
        render_command_card(
            command=command,
            explanation=response.explanation,
            assessment=assessment,
            provider_name=response.provider_name,
            model_name=response.model_name
        )

        # 4. Dry-run handling
        if self.dry_run:
            print(dim("\n[Dry-run mode: Command was NOT executed]"))
            self.history.add(
                query=prompt,
                command=command,
                explanation=response.explanation,
                executed=False,
                exit_code=None,
                provider=self.provider.name
            )
            return None, response

        # 5. Blocked command check
        if assessment.is_blocked:
            print(red("\n✖ Blocked: Command contains catastrophic or destructive system operations."))
            self.history.add(
                query=prompt,
                command=command,
                explanation=response.explanation,
                executed=False,
                exit_code=-1,
                provider=self.provider.name
            )
            return None, response

        # 6. User confirmation logic
        action = "run"
        final_cmd = command

        if not self.auto_yes:
            action, final_cmd = prompt_user_action(command, is_blocked=assessment.is_blocked)
        else:
            # If auto-yes is active, still warn on DANGEROUS commands
            if assessment.requires_confirmation:
                print(yellow("\n⚠ Command is categorized as DANGEROUS. Confirming even in auto-mode..."))
                action, final_cmd = prompt_user_action(command, is_blocked=assessment.is_blocked)

        if action == "quit":
            print(dim("Operation cancelled by user."))
            sys.exit(0)

        if action != "run":
            print(dim("Execution skipped."))
            self.history.add(
                query=prompt,
                command=command,
                explanation=response.explanation,
                executed=False,
                exit_code=None,
                provider=self.provider.name
            )
            return None, response

        # Editing never bypasses policy or approval of the newly proposed command.
        while final_cmd != command:
            command = final_cmd
            assessment = analyze_command(command)
            render_command_card(command, "Edited command", assessment, response.provider_name, response.model_name)
            if assessment.is_blocked:
                print(red("Edited command is blocked."))
                return None, response
            action, final_cmd = prompt_user_action(command, is_blocked=False)
            if action != "run":
                return None, response

        # 7. Execute command
        print(f"\n{bold('Executing:')} {cyan(terminal_text(final_cmd))}\n")
        result = self.executor.execute_interactive(final_cmd)
        if result.stderr:
            print(red(terminal_text(result.stderr)))

        # 8. Record in history
        self.history.add(
            query=prompt,
            command=final_cmd,
            explanation=response.explanation,
            executed=True,
            exit_code=result.exit_code,
            provider=response.provider_name
        )

        if result.exit_code == 0:
            print(green(f"\n✔ Command completed successfully in {result.duration_seconds:.2f}s"))
        else:
            print(red(f"\n✖ Command exited with status code {result.exit_code}"))

        print(self.router.summary())
        return result, response
