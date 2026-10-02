"""Unit tests for safety and danger level detection."""
import unittest
from terminal_agent.core.safety import analyze_command, DangerLevel

class TestSafetyAnalyzer(unittest.TestCase):
    def test_blocked_catastrophic_commands(self):
        blocked = [
            "rm -rf /",
            "rm -rf /*",
            "rm -rf ~",
            ":(){ :|:& };:",
            "mkfs.ext4 /dev/sda",
            "dd if=/dev/zero of=/dev/sda bs=1M",
            "chmod -R 777 /",
            "echo test > /dev/sda",
        ]
        for cmd in blocked:
            assessment = analyze_command(cmd)
            self.assertTrue(assessment.is_blocked, f"Expected {cmd} to be blocked")
            self.assertEqual(assessment.level, DangerLevel.BLOCKED)

    def test_dangerous_commands(self):
        dangerous = [
            "rm -rf /tmp/myfolder",
            "shutdown -h now",
            "reboot",
            "iptables -F",
            "kill -9 -1",
            "fdisk /dev/sdb",
        ]
        for cmd in dangerous:
            assessment = analyze_command(cmd)
            self.assertFalse(assessment.is_blocked)
            self.assertEqual(assessment.level, DangerLevel.DANGEROUS)

    def test_caution_commands(self):
        caution = [
            "systemctl restart nginx",
            "pkill python",
            "git reset --hard HEAD~1",
            "apt-get remove docker",
            "chmod +x script.sh",
        ]
        for cmd in caution:
            assessment = analyze_command(cmd)
            self.assertFalse(assessment.is_blocked)
            self.assertEqual(assessment.level, DangerLevel.CAUTION)

    def test_safe_readonly_commands(self):
        safe = [
            "ls -la",
            "cat /etc/os-release",
            "df -h",
            "free -m",
            "uname -a",
            "grep -rn 'foo' .",
            "systemctl status nginx",
            "ip a",
        ]
        for cmd in safe:
            assessment = analyze_command(cmd)
            self.assertEqual(assessment.level, DangerLevel.SAFE)

if __name__ == "__main__":
    unittest.main()
