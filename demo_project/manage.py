#!/usr/bin/env python
"""Django management entry point for the demo project."""
import os
import sys
from pathlib import Path


def main():
    # Allow running from a source checkout without installing the packages.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
