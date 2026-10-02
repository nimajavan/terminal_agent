#!/usr/bin/env bash
# ==============================================================================
# Linux Terminal Agent (lta) - Uninstaller
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
NC='\033[0m'

if [ "$EUID" -eq 0 ]; then
    INSTALL_DIR="/usr/local/share/linux-terminal-agent"
    BIN_DIR="/usr/local/bin"
else
    INSTALL_DIR="${HOME}/.local/share/linux-terminal-agent"
    BIN_DIR="${HOME}/.local/bin"
fi

echo -e "${RED}${BOLD}Removing Linux Terminal Agent...${NC}"

rm -rf "${INSTALL_DIR}"
rm -f "${BIN_DIR}/lta"
rm -f "${BIN_DIR}/terminal-agent"

echo -e "${GREEN}✔ Linux Terminal Agent has been removed from ${BIN_DIR}.${NC}"
echo "Config and history in ~/.config/linux-terminal-agent and ~/.local/share/linux-terminal-agent were preserved."
echo "To remove configs completely, run: rm -rf ~/.config/linux-terminal-agent ~/.local/share/linux-terminal-agent"
