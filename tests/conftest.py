import os
import shutil
import tempfile
from pathlib import Path


TEST_ROOT = Path(tempfile.mkdtemp(prefix="irqlens-tests-"))
os.environ["IRQLENS_DB_PATH"] = str(TEST_ROOT / "test.db")
os.environ["IRQLENS_OUTPUT_DIR"] = str(TEST_ROOT / "output")
os.environ["IRQLENS_AGENT_TOKEN"] = ""
os.environ["IRQLENS_API_TOKEN"] = ""
os.environ["IRQLENS_TOKEN"] = ""


def pytest_configure(config):
    if getattr(config.option, "output", None) == "test-results":
        config.option.output = str(TEST_ROOT / "browser-artifacts")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)