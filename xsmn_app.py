"""Compatibility entry point; the canonical Streamlit UI lives in src/xsmn_app.py."""

import runpy
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
runpy.run_path(str(SRC / 'xsmn_app.py'), run_name='__main__')
