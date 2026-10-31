#!/usr/bin/env python3
"""Run core tests with private HOME, state and config regardless of caller environment."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='agentsmon-tests-') as tmp:
    home=Path(tmp)
    env=dict(os.environ,HOME=tmp,AGENTSMON_STATE=str(home/'state'),
             AGENTSMON_CONFIG=str(home/'config.json'),PYTHONPATH=str(root))
    command=[sys.executable,'-m','unittest','discover','-s','tests',*sys.argv[1:]]
    raise SystemExit(subprocess.call(command,cwd=root,env=env))
