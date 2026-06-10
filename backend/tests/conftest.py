import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# isolate the paper library before backend.main is imported
os.environ.setdefault("PAPER_READER_DATA_DIR", tempfile.mkdtemp(prefix="paper-reader-test-"))
