#!/usr/bin/env python
"""Django's command-line utility. Run from the repo root: python api/manage.py migrate"""

import os
import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # api/: config and backtests
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root: engine
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
