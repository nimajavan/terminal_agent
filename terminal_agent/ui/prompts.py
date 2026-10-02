"""
Interactive user confirmation prompts and result renderers.
"""

import sys
from typing import Tuple
from terminal_agent.ui.colors import (
    bold, cyan, green, yellow, red, dim, gray,
    badge_safe, badge_caution, badge_danger, badge_critical
)
from terminal_agent.core.safety import DangerLevel, SafetyAssessment
from terminal_agent.core.privacy import terminal_text

def render_command_card(
    command: str,
    explanation: str,
    assessment: SafetyAssessment,
    provider_name: str,
    model_name: str
) -> None:
    """Print formatted command box to stdout."""
    border = gray("─" * 60)
    print()
    print(border)
    print(f" {bold('Command:')}     {cyan(bold(terminal_text(command)))}")
    if explanation:
        print(f" {bold('Summary:')}     {terminal_text(explanation)}")

    # Safety badge
    badge_map = {
        DangerLevel.SAFE: badge_safe(),
        DangerLevel.CAUTION: badge_caution(),
        DangerLevel.DANGEROUS: badge_danger(),
        DangerLevel.BLOCKED: badge_critical(),
    }
    badge = badge_map.get(assessment.level, "")
    print(f" {bold('Safety:')}      {badge}")

    if assessment.reasons:
        for r in assessment.reasons:
            color_fn = red if assessment.level in [DangerLevel.DANGEROUS, DangerLevel.BLOCKED] else yellow
            print(f"             {color_fn('• ' + r)}")

    print(f" {bold('Engine:')}      {dim(f'{provider_name} ({model_name})')}")
    print(border)


def prompt_user_action(
    command: str,
    is_blocked: bool
) -> Tuple[str, str]:
    """
    Prompt user for action.
    Returns (action, final_command) where action in:
    'run', 'skip', 'edit', 'quit'
    """
    if is_blocked:
        print(red("\n✖ This command is permanently blocked for system safety and cannot be executed."))
        return "skip", command

    prompt_text = (
        f"{bold('Execute command?')} "
        f"[{green('y')}es / {yellow('n')}o / {cyan('e')}dit / {red('q')}uit]: "
    )

    while True:
        try:
            choice = input(prompt_text).strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return "quit", command

        if choice in ["y", "yes"]:
            return "run", command
        elif choice in ["n", "no", ""]:
            return "skip", command
        elif choice in ["q", "quit"]:
            return "quit", command
        elif choice in ["e", "edit"]:
            try:
                edited = input(f"{bold('Edit command:')} ").strip()
                if edited:
                    return "run", edited
                else:
                    return "skip", command
            except (KeyboardInterrupt, EOFError):
                return "skip", command
        else:
            print(gray("Please enter 'y', 'n', 'e', or 'q'."))
