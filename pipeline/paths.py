"""Project paths. All pipeline commands should be run from any cwd; paths are rooted here."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
LOCKED_DIR = DATA_DIR / "locked"
PROPOSED_DIR = DATA_DIR / "proposed"
RAW_DIR = DATA_DIR / "raw"

TIPS_PATH = DATA_DIR / "tips.json"
STATS_PATH = DATA_DIR / "stats.json"
EXAMPLE_TIPS_PATH = DATA_DIR / "example-tips.json"
FIXTURES_PATH = DATA_DIR / "fixtures.json"
TEAM_NAMES_PATH = DATA_DIR / "team-names-hk.json"
PROPOSALS_PATH = PROPOSED_DIR / "proposals.json"
CONFIG_PATH = ROOT / "pipeline" / "leagues.json"
API_KEY_FILE = ROOT / ".api_key"


def ensure_dirs() -> None:
    for p in (DATA_DIR, CACHE_DIR, LOCKED_DIR, PROPOSED_DIR, RAW_DIR):
        p.mkdir(parents=True, exist_ok=True)
