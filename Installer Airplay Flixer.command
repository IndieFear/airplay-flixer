#!/bin/zsh
set -e
cd -- "${0:A:h}"
/usr/bin/env python3 scripts/install.py
