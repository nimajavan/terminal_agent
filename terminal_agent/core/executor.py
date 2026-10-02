"""
Safe command execution and process supervision engine.
Runs commands in subshell, captures stdout/stderr, monitors exit codes,
and handles timeouts gracefully.
"""

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Generator

@dataclass
class ExecutionResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class CommandExecutor:
    def __init__(self, default_timeout: int = 120):
        self.default_timeout = default_timeout

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        env: Optional[dict] = None
    ) -> ExecutionResult:
        """Execute command and capture output."""
        start_time = time.time()
        timeout_val = timeout or self.default_timeout

        execution_env = os.environ.copy()
        if env:
            execution_env.update(env)

        try:
            proc = subprocess.run(
                command,
                shell=True,
                executable="/bin/bash",
                cwd=cwd or os.getcwd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_val,
                env=execution_env
            )
            duration = time.time() - start_time
            return ExecutionResult(
                command=command,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_seconds=duration,
                timed_out=False
            )
        except subprocess.TimeoutExpired as te:
            duration = time.time() - start_time
            stdout = te.stdout if isinstance(te.stdout, str) else (te.stdout.decode() if te.stdout else "")
            stderr = te.stderr if isinstance(te.stderr, str) else (te.stderr.decode() if te.stderr else "")
            return ExecutionResult(
                command=command,
                exit_code=-1,
                stdout=stdout,
                stderr=f"Command timed out after {timeout_val} seconds.\n{stderr}",
                duration_seconds=duration,
                timed_out=True
            )
        except Exception as e:
            duration = time.time() - start_time
            return ExecutionResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"Execution error: {str(e)}",
                duration_seconds=duration,
                timed_out=False
            )

    def execute_interactive(
        self,
        command: str,
        cwd: Optional[str] = None
    ) -> ExecutionResult:
        """
        Execute command with live terminal pass-through (useful for interactive commands
        like htop, nano, git commit, sudo prompts).
        """
        start_time = time.time()
        try:
            ret = subprocess.call(
                command,
                shell=True,
                executable="/bin/bash",
                cwd=cwd or os.getcwd()
            )
            duration = time.time() - start_time
            return ExecutionResult(
                command=command,
                exit_code=ret,
                stdout="",
                stderr="",
                duration_seconds=duration,
                timed_out=False
            )
        except Exception as e:
            duration = time.time() - start_time
            return ExecutionResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                timed_out=False
            )
