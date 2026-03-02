"""Pytest configuration for SPHinXsim tests.

This file is automatically loaded by pytest before running tests.
"""

import sys
import os


def pytest_configure(config):
    """Pytest hook called before test collection.
    
    Prevents Python from creating __pycache__ directories during test runs.
    Note: This only affects bytecode generation after conftest.py is loaded.
    To prevent conftest.py itself from creating __pycache__, set the environment
    variable before running pytest:
    
        PYTHONDONTWRITEBYTECODE=1 python -m pytest
    """
    # Set environment variable for test subprocesses
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    
    # Prevent bytecode generation for module imports after this point
    sys.dont_write_bytecode = True


