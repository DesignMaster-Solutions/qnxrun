#!/usr/bin/env python3
"""Run from a checkout without installing: ./qnxrun.py doctor"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from qnxrun.cli import main  # noqa: E402

sys.exit(main())
