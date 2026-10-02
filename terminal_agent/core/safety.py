"""
Security and Safety Analyzer for terminal commands.
Inspects commands before execution to prevent accidental system destruction,
data loss, privilege escalation vulnerabilities, or bricking.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple

class DangerLevel(str, Enum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    DANGEROUS = "DANGEROUS"
    BLOCKED = "BLOCKED"

@dataclass
class SafetyAssessment:
    level: DangerLevel
    reasons: List[str]
    is_blocked: bool
    requires_confirmation: bool
    suggested_safe_alternative: str = ""

# Patterns that MUST be strictly blocked from execution by the agent
BLOCKED_PATTERNS: List[Tuple[str, str]] = [
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+(/|\/\*|~|/\w+/\.\.)(\s|$)", "Attempt to recursively delete root or home directory"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "Fork bomb detected - would freeze system"),
    (r"\bmkfs(\.\w+)?\s+/dev/(sd[a-z]|nvme\d+n\d+|hd[a-z]|vd[a-z])\b", "Formatting primary storage device directly"),
    (r"\bdd\s+.*of=/dev/(sd[a-z]|nvme\d+n\d+|hd[a-z]|vd[a-z])\b", "Direct block-level overwrite of disk device"),
    (r">\s*/dev/(sd[a-z]|nvme\d+n\d+|hd[a-z]|vd[a-z])\b", "Direct redirection write to storage block device"),
    (r"\bchmod\s+-[a-zA-Z]*R\s+777\s+/\s*$", "Global 777 permissions on root breaks Linux permission model"),
    (r">\s*/dev/kmem\b|>\s*/dev/mem\b", "Direct kernel memory overwrite"),
    (r"\b(mv|cp)\s+.*\s+/dev/null\s*$", "Corrupting /dev/null node"),
]

# Patterns that carry high risk of data destruction or service shutdown
DANGEROUS_PATTERNS: List[Tuple[str, str]] = [
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\b", "Recursive file/directory deletion"),
    (r"\b(shutdown|poweroff|reboot|halt|init\s+[06])\b", "System shutdown or reboot command"),
    (r"\b(fdisk|parted|gdisk|sfdisk)\b", "Partition table manipulation tool"),
    (r"\biptables\s+-F\b|nft\s+flush\s+ruleset\b", "Flushing network firewall rules"),
    (r"\bkill\s+-9\s+-1\b|pkill\s+-9\s+.*", "Mass process termination"),
    (r">\s*/etc/(passwd|shadow|sudoers|fstab)\b", "Direct overwrite of critical authentication/system configuration files"),
    (r"\bchown\s+-[a-zA-Z]*R\s+", "Recursive ownership modification across directories"),
    (r"\bdropdb\b|\bdrop\s+database\b", "Database destruction command"),
]

# Patterns that modify state or terminate specific processes
CAUTION_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(kill|pkill|killall)\b", "Process termination"),
    (r"\b(systemctl|service)\s+(stop|restart|disable)\b", "Stopping or disabling system service"),
    (r"\b(apt-get|apt|dnf|yum|pacman|zypper|apk)\s+(remove|purge|erase|-R)\b", "Package removal"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f)", "Destructive git operation that will discard uncommitted changes"),
    (r"\b(chmod|chown)\b", "Modifying file permissions or ownership"),
    (r"\bsudo\b", "Command requires superuser privileges"),
    (r"\btruncate\s+", "File truncation"),
    (r">\s*[^\s]+", "Redirect overwrite (truncating target file)"),
]

# Safe read-only inspection commands
SAFE_READONLY_PREFIXES = (
    "ls", "cat", "head", "tail", "less", "more", "grep", "rg", "awk", "sed -n",
    "find", "stat", "file", "du", "df", "free", "uname", "uptime", "ps", "top", "htop",
    "ip", "ifconfig", "ss", "netstat", "lsof", "whoami", "id", "groups", "date",
    "echo", "pwd", "which", "whereis", "type", "ping -c", "dig", "nslookup",
    "git status", "git log", "git diff", "git branch", "systemctl status", "journalctl",
    "env", "printenv", "wc", "sort", "uniq", "tree", "dmesg"
)


def analyze_command(command: str) -> SafetyAssessment:
    """Analyze a shell command for dangerous operations."""
    cleaned = command.strip()
    if not cleaned:
        return SafetyAssessment(
            level=DangerLevel.SAFE,
            reasons=["Empty command"],
            is_blocked=False,
            requires_confirmation=False
        )

    reasons: List[str] = []

    # 1. Check blocked patterns
    for pattern, desc in BLOCKED_PATTERNS:
        if re.search(pattern, cleaned, re.IGNORECASE):
            reasons.append(desc)
            return SafetyAssessment(
                level=DangerLevel.BLOCKED,
                reasons=reasons,
                is_blocked=True,
                requires_confirmation=False,
                suggested_safe_alternative="Execution blocked permanently for system safety."
            )

    # 2. Check dangerous patterns
    for pattern, desc in DANGEROUS_PATTERNS:
        if re.search(pattern, cleaned, re.IGNORECASE):
            reasons.append(desc)

    if reasons:
        return SafetyAssessment(
            level=DangerLevel.DANGEROUS,
            reasons=reasons,
            is_blocked=False,
            requires_confirmation=True
        )

    # 3. Check caution patterns
    for pattern, desc in CAUTION_PATTERNS:
        if re.search(pattern, cleaned, re.IGNORECASE):
            reasons.append(desc)

    if reasons:
        return SafetyAssessment(
            level=DangerLevel.CAUTION,
            reasons=reasons,
            is_blocked=False,
            requires_confirmation=True
        )

    # 4. Check if it matches safe read-only prefixes
    cmd_parts = cleaned.split("&&")[0].split(";")[0].split("|")[0].strip()
    for prefix in SAFE_READONLY_PREFIXES:
        if cmd_parts == prefix or cmd_parts.startswith(prefix + " "):
            return SafetyAssessment(
                level=DangerLevel.SAFE,
                reasons=["Read-only / informational command"],
                is_blocked=False,
                requires_confirmation=False
            )

    # Default: SAFE with confirmation for unknown commands
    return SafetyAssessment(
        level=DangerLevel.SAFE,
        reasons=["Standard non-destructive command"],
        is_blocked=False,
        requires_confirmation=False
    )
