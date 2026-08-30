#!/bin/bash
set -e

su dev -c '/home/linuxbrew/.linuxbrew/bin/brew install llvm cmake ninja ccache pkgconf helix'

# Homebrew's llvm is keg-only: clang/clang++/clangd/lld/lldb/clang-format/
# clang-tidy live in opt/llvm/bin and are NOT symlinked onto PATH. Add that
# dir for the dev user so `clang` and `clangd` (Helix's default C/C++ LSP)
# resolve. `make` comes from the base image's build-essential. Written as
# root then chowned - same pattern as py-devtools' languages.toml.
LLVM_BIN=/home/linuxbrew/.linuxbrew/opt/llvm/bin
mkdir -p /home/dev/.config/fish/conf.d
echo "fish_add_path -gp $LLVM_BIN" > /home/dev/.config/fish/conf.d/cpp-devtools.fish
echo "export PATH=\"$LLVM_BIN:\$PATH\"" >> /home/dev/.bashrc
chown dev:dev /home/dev/.config/fish/conf.d/cpp-devtools.fish
