"""
Zero-dependency, offline rule-based intelligent NLP parser.
Translates common natural language prompts (English and Persian) directly into
tailored Linux commands without requiring any AI backend or internet connection.
"""

import re
from typing import List, Dict, Tuple, Optional, Any
from terminal_agent.providers.base import BaseProvider, AgentResponse
from terminal_agent.core.context import SystemContext

class RuleBasedProvider(BaseProvider):
    name = "rule_based"
    is_offline = True

    def __init__(self, model: str = "builtin-rules", config: Optional[Dict[str, Any]] = None):
        super().__init__(model=model, config=config)

    def generate(
        self,
        prompt: str,
        context: SystemContext,
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        p = prompt.strip().lower()
        sudo_prefix = "sudo " if not context.is_root else ""
        pkg = context.pkg_manager

        # 1. Disk & Filesystem
        if any(w in p for w in ["disk space", "disk usage", "free space", "فضای دیسک", "حجم هارد"]):
            return AgentResponse(
                command="df -h",
                explanation="Displays disk space usage in human-readable format.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )

        # 2. Memory / RAM
        if any(w in p for w in ["free memory", "ram usage", "memory usage", "میزان رم", "مصرف رم", "حافظه"]):
            return AgentResponse(
                command="free -h",
                explanation="Shows total, used, and available RAM and swap space.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )

        # 3. CPU & System info
        if any(w in p for w in ["cpu info", "processor", "مشخصات پردازنده", "پردازنده"]):
            return AgentResponse(
                command="lscpu",
                explanation="Gathers and displays detailed CPU architecture information.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )
        if any(w in p for w in ["os info", "linux version", "distro version", "نسخه لینوکس", "مشخصات سیستم"]):
            return AgentResponse(
                command="cat /etc/os-release && uname -a",
                explanation="Prints detailed Linux distribution release and kernel version.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )

        # 4. Ports & Networking
        port_kill_match = re.search(r"(?:kill|free|بستن|کشتن).*?(?:port|پورت)\s*(\d+)", p)
        if port_kill_match:
            port = port_kill_match.group(1)
            return AgentResponse(
                command=f"{sudo_prefix}fuser -k {port}/tcp",
                explanation=f"Kills any process currently listening on TCP port {port}.",
                provider_name=self.name,
                model_name=self.model,
                confidence=0.95
            )

        if any(w in p for w in ["listening port", "open port", "ports", "پورت های باز", "پورتها"]):
            return AgentResponse(
                command=f"{sudo_prefix}ss -tulpn",
                explanation="Lists all active listening TCP and UDP sockets with their process names.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )

        if any(w in p for w in ["public ip", "external ip", "ip عمومی", "ای پی خارجی"]):
            return AgentResponse(
                command="curl -s https://ifconfig.me || curl -s https://api.ipify.org",
                explanation="Fetches and displays your public external IP address.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )

        if any(w in p for w in ["local ip", "ip address", "network interface", "آدرس آی پی", "شبکه"]):
            return AgentResponse(
                command="ip -brief addr show",
                explanation="Shows compact IP addresses assigned to network interfaces.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )

        # 5. Top processes
        if any(w in p for w in ["top memory", "top mem", "ram consuming", "بیشترین مصرف رم"]):
            return AgentResponse(
                command="ps aux --sort=-%mem | head -n 11",
                explanation="Shows top 10 processes consuming the most RAM.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )
        if any(w in p for w in ["top cpu", "cpu consuming", "بیشترین مصرف cpu", "بیشترین پردازش"]):
            return AgentResponse(
                command="ps aux --sort=-%cpu | head -n 11",
                explanation="Shows top 10 processes consuming the most CPU.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )

        # 6. File search by size
        size_match = re.search(r"(?:larger than|bigger than|بزرگتر از)\s*(\d+)\s*(m|mb|g|gb|k|kb)?", p)
        if size_match or "large files" in p or "فایل های حجیم" in p:
            size_num = size_match.group(1) if size_match else "100"
            unit = (size_match.group(2) or "M").upper()[0] if size_match else "M"
            return AgentResponse(
                command=f"find . -type f -size +{size_num}{unit} -exec ls -lh {{}} +",
                explanation=f"Searches current directory for files larger than {size_num}{unit} and lists details.",
                provider_name=self.name,
                model_name=self.model,
                confidence=0.95
            )

        # 7. Package installation
        install_match = re.search(r"(?:install|نصب|اضافه کردن)\s+([a-zA-Z0-9_\-]+)", p)
        if install_match and not any(w in p for w in ["script", "os", "system"]):
            target_pkg = install_match.group(1)
            if pkg == "apt":
                cmd = f"{sudo_prefix}apt-get update && {sudo_prefix}apt-get install -y {target_pkg}"
            elif pkg == "pacman":
                cmd = f"{sudo_prefix}pacman -S --noconfirm {target_pkg}"
            elif pkg == "dnf":
                cmd = f"{sudo_prefix}dnf install -y {target_pkg}"
            elif pkg == "apk":
                cmd = f"{sudo_prefix}apk add {target_pkg}"
            else:
                cmd = f"{sudo_prefix}apt install {target_pkg}"
            return AgentResponse(
                command=cmd,
                explanation=f"Installs package '{target_pkg}' using detected package manager ({pkg}).",
                provider_name=self.name,
                model_name=self.model,
                confidence=0.9
            )

        # 8. Git helpers
        if "git status" in p or "وضعیت گیت" in p:
            return AgentResponse(
                command="git status",
                explanation="Shows current git working tree status.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )
        if any(w in p for w in ["git log", "recent commits", "آخرین کامیت ها"]):
            return AgentResponse(
                command="git log --oneline -n 10",
                explanation="Displays the last 10 git commits in one-line format.",
                provider_name=self.name,
                model_name=self.model,
                confidence=1.0
            )

        # 9. Service status
        svc_match = re.search(r"(?:status of|status|وضعیت سرویس|سرویس)\s+([a-zA-Z0-9_\-]+)", p)
        if svc_match:
            svc_name = svc_match.group(1)
            return AgentResponse(
                command=f"systemctl status {svc_name}",
                explanation=f"Checks current status and logs for {svc_name} service.",
                provider_name=self.name,
                model_name=self.model,
                confidence=0.9
            )

        # Generic fallback
        return AgentResponse(
            command=f"# Echo request: {prompt}",
            explanation="Prompt could not be matched by offline rule parser. Configure an AI provider (Ollama or API key) for complex reasoning.",
            provider_name=self.name,
            model_name=self.model,
            confidence=0.2
        )

    def test_connection(self) -> Tuple[bool, str]:
        return True, "Built-in offline rule engine is active and ready (Zero dependencies)."
