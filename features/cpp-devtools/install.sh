#!/bin/bash
set -e

su dev -c '/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge \
    clang clang-tools lldb gdb \
    cmake ninja make pkg-config ccache \
    helix'
