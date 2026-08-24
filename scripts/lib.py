"""Shared config/path resolution for claude-job-search scripts.

All scripts run with CWD = repo root (where the /job-search skill runs).
Override locations with env vars if you must:
  JOB_SEARCH_DATA    — data directory (default: ./data)
  JOB_SEARCH_CONFIG  — config file (default: ./config.yaml)
"""

import os
import sys
from pathlib import Path

import yaml

# Canonical listing status enum, shared by generate_chart.py and
# generate_run_report.py so their status breakdowns can't drift apart.
STATUS_ORDER = ['To Apply', 'Applied', 'Screen', 'Interviewing', 'Offer', 'Stale', 'Rejected', 'Skipped', 'Passed']
STATUS_COLORS = {
    'To Apply': '#42a5f5', 'Applied': '#26a69a', 'Screen': '#ffa726',
    'Interviewing': '#ab47bc', 'Offer': '#66bb6a',
    'Stale': '#8d6e63',
    'Rejected': '#ef5350', 'Skipped': '#bdbdbd', 'Passed': '#90a4ae',
}


def data_root() -> Path:
    return Path(os.environ.get('JOB_SEARCH_DATA', 'data'))


def config_path() -> Path:
    return Path(os.environ.get('JOB_SEARCH_CONFIG', 'config.yaml'))


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        print(f'No config at {path}. Run: cp config.yaml.example config.yaml '
              f'and edit it first.', file=sys.stderr)
        sys.exit(2)
    cfg = yaml.safe_load(path.read_text()) or {}
    return cfg


def cfg_get(cfg: dict, dotted: str, default=None):
    """cfg_get(cfg, 'integrations.whatsapp.chat_db') with ~-expansion on paths."""
    node = cfg
    for key in dotted.split('.'):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    if isinstance(node, str) and node.startswith('~'):
        return str(Path(node).expanduser())
    return node


def session_dir(name: str) -> Path:
    """Persistent browser profile dir for a source (login survives runs)."""
    d = data_root() / '.sessions' / name
    d.mkdir(parents=True, exist_ok=True)
    return d
