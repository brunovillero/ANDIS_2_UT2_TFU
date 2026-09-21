#!/bin/zsh
cd "${0:A:h}"
export PATH="$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
python3 demo_dashboard.py
