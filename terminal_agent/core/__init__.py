"""Core agent architecture components."""
from terminal_agent.core.context import SystemContext, get_system_context
from terminal_agent.core.safety import DangerLevel, SafetyAssessment, analyze_command
from terminal_agent.core.executor import CommandExecutor, ExecutionResult
from terminal_agent.core.history import HistoryManager, HistoryEntry

__all__ = [
    "SystemContext", "get_system_context",
    "DangerLevel", "SafetyAssessment", "analyze_command",
    "CommandExecutor", "ExecutionResult",
    "HistoryManager", "HistoryEntry"
]
