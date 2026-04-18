# mlb-game-report

A Claude Code skill that generates newspaper-style MLB game reports in Markdown, sourced live from the public MLB Stats API.

## Layout

- `SKILL.md` — trigger criteria and usage instructions for Claude
- `scripts/mlb-report` — Python 3 generator (stdlib only, no dependencies)

## Usage

```bash
# By team + date
scripts/mlb-report --team LAA --date 2026-04-17 -o report.md

# By gamePk
scripts/mlb-report 824048 -o report.md

# Print to stdout
scripts/mlb-report 824048
```

## Output

Each report contains:

1. Auto-generated headline and subhead
2. Dateline + lede paragraph with top performer
3. Pitching summary (W/L/S)
4. Inning-by-inning scoring narrative
5. Supporting offensive performances
6. Full line score (handles extra innings)
7. Full batting box scores (AB/H/R/RBI/BB/K)
8. Full pitching box scores (IP/H/R/ER/BB/K) with W/L/S tags
9. Notes: umpires, attendance, duration, weather, records

## Install as a Claude Code skill

Clone into your user skills directory:

```bash
git clone <repo-url> ~/.claude/skills/mlb-game-report
```

Claude will then auto-trigger the skill on requests like "write up the Angels game from yesterday" or "box score for gamePk 824048".

## Data source

All data comes from the public MLB Stats API (no auth):

- `statsapi.mlb.com/api/v1/schedule`
- `statsapi.mlb.com/api/v1/game/{gamePk}/boxscore`
- `statsapi.mlb.com/api/v1/game/{gamePk}/linescore`
- `statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live`
