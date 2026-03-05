"""Pytest configuration for SPHinXsim tests.

This file is automatically loaded by pytest before running tests.
"""

import sys
import os
import shutil
from pathlib import Path

import pytest


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


@pytest.fixture
def build_temp_path(tmp_path):
    """Provide a temporary directory within .build-temp for test outputs.
    
    This ensures all test artifacts are collected in the project's .build-temp
    directory rather than scattered across system temp locations.
    
    Returns
    -------
    Path
        A unique temporary directory under .build-temp/pytest-temp/
    """
    # Find project root (where pyproject.toml is)
    project_root = Path(__file__).parent.parent
    
    # Create base pytest temp directory in .build-temp
    base_temp = project_root / ".build-temp" / "pytest-temp"
    base_temp.mkdir(parents=True, exist_ok=True)
    
    # Create a unique subdirectory using tmp_path's name
    temp_dir = base_temp / tmp_path.name
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    yield temp_dir
    
    # Cleanup after test (optional - can comment out to keep for debugging)
    # shutil.rmtree(temp_dir, ignore_errors=True)


