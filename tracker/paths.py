from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "games.db"
CACHE_DIR = DATA_DIR / "cache"
ALIASES_PATH = DATA_DIR / "team_aliases.json"
PARKS_PATH = DATA_DIR / "parks.json"
SECRET_KEY_PATH = DATA_DIR / "secret_key"


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
