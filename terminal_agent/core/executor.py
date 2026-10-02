"""Bounded process supervision. Shell execution deliberately requires Linux."""
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from terminal_agent.core.privacy import terminal_text


@dataclass
class ExecutionResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def succeeded(self):
        return self.exit_code == 0 and not self.timed_out


class CommandExecutor:
    def __init__(self, default_timeout=120, output_limit=65536):
        self.default_timeout = default_timeout
        self.output_limit = output_limit
        self.active = None

    def cancel(self):
        proc = self.active
        if proc is not None and proc.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except ProcessLookupError:
                pass

    def execute(self, command, cwd=None, timeout=None, env=None, stream=False):
        started = time.monotonic()
        shell = isinstance(command, str)
        label = command if shell else repr(command)
        if shell and (os.name != "posix" or not os.path.isfile("/bin/bash")):
            return ExecutionResult(label, -1, "", "Bash execution requires Linux (or a Linux WSL session).", 0)
        execution_env = os.environ.copy()
        execution_env.update({"PAGER": "cat", "SYSTEMD_PAGER": "cat", "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"})
        if env:
            execution_env.update(env)
        chunks = {"stdout": [], "stderr": []}
        counts = {"stdout": 0, "stderr": 0}

        def consume(pipe, name):
            # Decode incrementally; cap stored output while draining the pipe.
            import codecs
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                raw = os.read(pipe.fileno(), 4096)
                if not raw:
                    break
                text = decoder.decode(raw)
                remaining = max(0, self.output_limit - counts[name])
                chunks[name].append(text[:remaining]) if remaining else None
                counts[name] += len(text)
                if stream:
                    print(terminal_text(text), end="", flush=True)
            tail = decoder.decode(b"", final=True)
            if tail and counts[name] < self.output_limit:
                chunks[name].append(tail)
            pipe.close()

        try:
            proc = subprocess.Popen(
                command, shell=shell, executable="/bin/bash" if shell else None,
                cwd=cwd or os.getcwd(), env=execution_env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            self.active = proc
            readers = [threading.Thread(target=consume, args=(getattr(proc, name), name), daemon=True) for name in chunks]
            for reader in readers:
                reader.start()
            timed_out = False
            try:
                proc.wait(timeout=self.default_timeout if timeout is None else timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self.cancel()
                proc.wait()
            except KeyboardInterrupt:
                self.cancel()
                proc.wait()
                chunks["stderr"].append("\nCancelled by user.")
            finally:
                # Kill descendants even when the shell exits leaving background jobs.
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            for reader in readers:
                reader.join(timeout=2)
            if timed_out:
                chunks["stderr"].append("\nCommand timed out.")
            for name in chunks:
                if counts[name] > self.output_limit:
                    chunks[name].append("\n[Output truncated]")
            return ExecutionResult(label, -1 if timed_out else proc.returncode,
                                   "".join(chunks["stdout"]), "".join(chunks["stderr"]),
                                   time.monotonic() - started, timed_out)
        except (OSError, ValueError) as exc:
            return ExecutionResult(label, -1, "", str(exc), time.monotonic() - started)
        finally:
            self.active = None

    def execute_interactive(self, command, cwd=None):
        # Stream output while retaining evidence and enforcing the same timeout.
        return self.execute(command, cwd=cwd, stream=True)
