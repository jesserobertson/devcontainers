#!/bin/bash
set -e

su dev -c 'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 /home/linuxbrew/.linuxbrew/bin/brew install bat bat-extras eza fd fish fzf jq just neovim ripgrep starship zoxide'

if [ "${LOGINSHELL:-true}" = "true" ]; then
    chsh -s /home/linuxbrew/.linuxbrew/bin/fish dev
fi
