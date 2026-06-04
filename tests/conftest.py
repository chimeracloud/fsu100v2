"""
Shared pytest fixtures.

CRITICAL: `FSU100V2_DISABLE_GCP_IO` is set at module load (before any
FSU100V2 module is imported) so the lifespan's GCS / Pub/Sub calls
short-circuit. Without this, every test would hit real GCP services,
slow the suite down 100×, and risk overwriting production blobs.

This is the same kill-switch pattern FSU1B uses — caught the hard way
during FSU1B's first test run.
"""
import os

# CRITICAL: set BEFORE anything imports our modules.
os.environ.setdefault("FSU100V2_DISABLE_GCP_IO", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
