# conftest.py
import pytest
import sys
from PyQt6.QtWidgets import QApplication

# Fixture to ensure a QApplication instance exists for tests that need it.
# This prevents errors when creating widgets.
@pytest.fixture(scope="session")
def qapp():
    """Session-wide QApplication."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app   