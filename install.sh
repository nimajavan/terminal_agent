#!/usr/bin/env bash
# ==============================================================================
# Linux Terminal Agent (lta) - Automated Installer
# Supported on: Ubuntu, Debian, Fedora, Arch, Alpine, CentOS, RHEL, openSUSE
# ==============================================================================

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${CYAN}${BOLD}"
echo "  _      _____          "
echo " | |    |_   _|   /\\      Linux Terminal Agent (LTA)"
echo " | |      | |    /  \\     Automated Linux Installation Script"
echo " | |___  _| |_  / /\\ \\  "
echo " |_____||_____|/_/  \\_\\ "
echo -e "${NC}"

# 1. Check Python 3
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✖ Python 3 is required but not installed.${NC}"
    echo "Please install Python 3 using your package manager (e.g. sudo apt install python3)"
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${GREEN}✔ Detected Python ${PY_VER}${NC}"

# 2. Determine installation target
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$EUID" -eq 0 ]; then
    INSTALL_DIR="/usr/local/share/linux-terminal-agent"
    BIN_DIR="/usr/local/bin"
else
    INSTALL_DIR="${HOME}/.local/share/linux-terminal-agent/app"
    BIN_DIR="${HOME}/.local/bin"
fi

echo -e "${CYAN}Installing to: ${BOLD}${INSTALL_DIR}${NC}"
mkdir -p "${INSTALL_DIR}"
mkdir -p "${BIN_DIR}"

# 3. Copy application files
cp -r "${SCRIPT_DIR}/terminal_agent" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/lta" "${INSTALL_DIR}/lta"
chmod +x "${INSTALL_DIR}/lta"

# 4. Create wrapper executable in BIN_DIR
cat << 'EOF' > "${BIN_DIR}/lta"
#!/usr/bin/env bash
INSTALL_PATH="__INSTALL_DIR__"
PYTHONPATH="${INSTALL_PATH}" exec python3 "${INSTALL_PATH}/lta" "$@"
EOF

# Substitute actual install path
sed -i "s|__INSTALL_DIR__|${INSTALL_DIR}|g" "${BIN_DIR}/lta"
chmod +x "${BIN_DIR}/lta"

# Also create 'terminal-agent' symlink for convenience
ln -sf "${BIN_DIR}/lta" "${BIN_DIR}/terminal-agent"

# 5. Check PATH for regular user
if [ "$EUID" -ne 0 ]; then
    case ":$PATH:" in
        *":${HOME}/.local/bin:"*) ;;
        *)
            echo -e "${YELLOW}Notice: ${HOME}/.local/bin is not in your current PATH.${NC}"
            SHELL_RC=""
            if [ -f "${HOME}/.bashrc" ]; then
                SHELL_RC="${HOME}/.bashrc"
            elif [ -f "${HOME}/.zshrc" ]; then
                SHELL_RC="${HOME}/.zshrc"
            fi
            if [ -n "${SHELL_RC}" ]; then
                echo 'export PATH="${HOME}/.local/bin:${PATH}"' >> "${SHELL_RC}"
                echo -e "${GREEN}✔ Added ~/.local/bin to ${SHELL_RC}${NC}"
            fi
            ;;
    esac
fi

echo ""
echo -e "${GREEN}${BOLD}✔ Installation completed successfully!${NC}"
echo -e "${CYAN}You can now run:${NC} ${BOLD}lta --help${NC} or ${BOLD}lta \"your prompt\"${NC}"
echo ""
echo -e "${BOLD}Quick Setup Guide:${NC}"
echo "1. Offline with Ollama (Recommended for privacy):"
echo "   - Install Ollama: curl -fsSL https://ollama.com/install.sh | sh"
echo "   - Download a coding model: ollama run qwen2.5-coder:7b"
echo "   - Set provider: lta config set provider ollama"
echo ""
echo "2. Online with API Key (OpenAI / Groq / Claude / Gemini):"
echo "   - For Groq (Fast & Free Tier): export GROQ_API_KEY=\"your-key\" && lta -p groq"
echo "   - For OpenAI: export OPENAI_API_KEY=\"your-key\" && lta -p openai"
echo ""
echo "3. Interactive Mode:"
echo "   - Run: lta"
echo ""
