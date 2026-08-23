# games-attended

Personal tracker for MLB games attended. Log a date and two teams, confirm the official game, and report over the combined log.

See [PLAN.md](PLAN.md) for the v1 spec and later features.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Use

```bash
python -m tracker serve          # local UI at http://127.0.0.1:5000
python -m tracker add --date 2024-06-15 --home "Red Sox" --away "Yankees"
python -m tracker enrich
python -m tracker list
python -m tracker report
python -m tracker report --html  # writes report.html
```

The UI is the main path: **Add** → confirm/reject the MLB match → game page. Official details come from the MLB Stats API. Your log lives in `data/games.db` (gitignored).

```bash
pytest
```
