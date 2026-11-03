#!/usr/bin/env bash
# PrepPi installer splash screen.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 - "$SCRIPT_DIR/logo.ansi" <<'PY'
import os
import re
import shutil
import sys

logo_path = sys.argv[1]
if not os.path.isfile(logo_path):
    raise SystemExit("PrepPi splash artwork is missing: " + logo_path)

RESET = "\033[0m"
GREEN = "\033[1;32m"
DIM = "\033[90m"
BLUEGREY = "\033[1;34m"
WHITE = "\033[1;37m"

terminal_width = shutil.get_terminal_size((120, 40)).columns
use_colour = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

ansi_pattern = re.compile(r"\033\[[0-?]*[ -/]*[@-~]")

with open(logo_path, encoding="utf-8") as logo:
    for line in logo.read().splitlines():
        visible = ansi_pattern.sub("", line)
        if not use_colour:
            line = visible
        offset = max(0, (terminal_width - 70) // 2)
        print(" " * offset + line)

def centred(text, colour):
    padding = max(0, (terminal_width - len(text)) // 2)
    if use_colour:
        print(" " * padding + colour + text + RESET)
    else:
        print(" " * padding + text)

print()

rule_width = min(max(70, 40), terminal_width, 76)
rule = "─" * rule_width
rule_offset = max(0, (terminal_width - rule_width) // 2)

if use_colour:
    print(" " * rule_offset + DIM + rule + RESET)
else:
    print(" " * rule_offset + rule)

centred("HOUSEHOLD PREPAREDNESS SYSTEM", BLUEGREY)
centred("VERSION 0.1.3", GREEN)

if use_colour:
    print(" " * rule_offset + DIM + rule + RESET)
else:
    print(" " * rule_offset + rule)

print()
centred("> Preparing your system...", WHITE)
centred("> Checking prerequisites", WHITE)
centred("> Installation will begin shortly.", DIM)
print()
centred("Welcome to PrepPi installation.", GREEN)
print()
PY
