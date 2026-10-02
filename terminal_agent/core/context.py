"""
Linux system context detector.
Gathers runtime environment information (OS distro, shell, package manager,
permissions, current directory) to provide accurate context to LLM models.
"""

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

@dataclass
class SystemContext:
    os_name: str
    distro: str
    distro_version: str
    kernel: str
    arch: str
    shell: str
    cwd: str
    user: str
    is_root: bool
    can_sudo: bool
    pkg_manager: str
    init_system: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        privilege = "root" if self.is_root else ("sudo-capable" if self.can_sudo else "standard user")
        return (
            f"OS: {self.distro} {self.distro_version} ({self.kernel}, {self.arch}) | "
            f"Shell: {self.shell} | Pkg: {self.pkg_manager} | User: {self.user} ({privilege}) | "
            f"CWD: {self.cwd}"
        )

    def system_prompt_context(self) -> str:
        return (
            f"- Operating System: {self.distro} {self.distro_version} (Kernel {self.kernel}, Arch {self.arch})\n"
            f"- Default Package Manager: {self.pkg_manager}\n"
            f"- Current Shell: {self.shell}\n"
            f"- Current Working Directory: {self.cwd}\n"
            f"- Current User: {self.user} (is_root: {self.is_root}, can_sudo: {self.can_sudo})\n"
            f"- Init System: {self.init_system}"
        )


def _detect_distro():
    """Parse /etc/os-release or fallback to platform.system."""
    distro_name = platform.system()
    distro_version = platform.release()

    if os.path.exists("/etc/os-release"):
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                data = {}
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        data[k.strip()] = v.strip().strip('"').strip("'")
                name = data.get("PRETTY_NAME") or data.get("NAME") or distro_name
                version = data.get("VERSION_ID") or data.get("VERSION") or ""
                return name, version
        except Exception:
            pass

    return distro_name, distro_version


def _detect_pkg_manager() -> str:
    """Find the default system package manager."""
    candidates = [
        ("apt-get", "apt"),
        ("dnf", "dnf"),
        ("pacman", "pacman"),
        ("zypper", "zypper"),
        ("apk", "apk"),
        ("emerge", "portage"),
        ("yum", "yum"),
        ("brew", "homebrew"),
        ("nix-env", "nix"),
    ]
    for binary, name in candidates:
        if shutil.which(binary):
            return name
    return "unknown"


def _detect_shell() -> str:
    """Detect current user shell."""
    shell_env = os.environ.get("SHELL", "")
    if shell_env:
        return os.path.basename(shell_env)
    return "bash"


def _check_can_sudo() -> bool:
    """Check if current user can run sudo without hanging."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    try:
        res = subprocess.run(
            ["sudo", "-n", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2
        )
        return res.returncode == 0
    except Exception:
        return False


def _detect_init_system() -> str:
    """Detect if systemd, openrc, or docker/container init is running."""
    if os.path.exists("/run/systemd/system"):
        return "systemd"
    if os.path.exists("/run/openrc"):
        return "openrc"
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return "container"
    return "init"


def get_system_context() -> SystemContext:
    """Collect full Linux system context."""
    distro, distro_version = _detect_distro()
    is_root = (os.geteuid() == 0) if hasattr(os, "geteuid") else False
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ("root" if is_root else "user")

    return SystemContext(
        os_name=platform.system(),
        distro=distro,
        distro_version=distro_version,
        kernel=platform.release(),
        arch=platform.machine(),
        shell=_detect_shell(),
        cwd=os.getcwd(),
        user=user,
        is_root=is_root,
        can_sudo=_check_can_sudo(),
        pkg_manager=_detect_pkg_manager(),
        init_system=_detect_init_system(),
    )
