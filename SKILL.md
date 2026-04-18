---
name: mlb-game-report
description: Use when the user asks for an MLB game recap, box score, game log, or newspaper-style report for a specific MLB game — by team + date, or by gamePk. Produces a Markdown article with a written summary, line score, full batting and pitching box scores, and game notes, all sourced live from the MLB Stats API.
---

# MLB Game Report

## When to use

Invoke this skill when the user asks for any of:

- A game recap / summary / write-up for an MLB game
- A box score or line score in Markdown form
- A "newspaper article" or "article-style" report for a specific game
- Fetching results for a team on a given date

Typical triggers: *"write up the Angels game from yesterday"*, *"give me a box score for gamePk 824048"*, *"Dodgers recap for 2026-04-10"*.

## How to use

There is a ready-made script at `scripts/mlb-report` (Python 3, stdlib only). Run it via Bash — do **not** re-implement the API calls inline.

### Invocation

```bash
# By team abbreviation + date (preferred when user gives team and date)
~/.claude/skills/mlb-game-report/scripts/mlb-report --team LAA --date 2026-04-17 -o /tmp/report.md

# By gamePk (use when user gives an ID, or when you already have one from the schedule API)
~/.claude/skills/mlb-game-report/scripts/mlb-report 824048 -o /tmp/report.md

# Print to stdout (omit -o)
~/.claude/skills/mlb-game-report/scripts/mlb-report 824048
```

The script accepts either a numeric team ID (e.g. `108`) or an abbreviation (`LAA`, `SD`, `NYY`, …).

### Output path

- If the user specifies a path or filename, honor it.
- Otherwise default to `~/<team>-<date>.md` (e.g. `~/angels-2026-04-17.md`) in the user's home directory — consistent with how the first report was written.
- Use `-o` to write directly; avoid piping stdout through `tee` or redirecting in the shell when a flag exists.

### When the user only specifies "yesterday" / "last night"

Today's date is available in the system context (`# currentDate`). Subtract one day and pass `--date YYYY-MM-DD`. Do not guess — if unsure, ask.

### Multiple games in a day (doubleheaders)

The script warns to stderr and picks the first game. If the user wants game 2, fetch both gamePks from the schedule API directly and pass the correct one:

```bash
curl -s "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD&teamId=ID" | python3 -c "import json,sys; [print(g['gamePk'], g['gameNumber']) for d in json.load(sys.stdin)['dates'] for g in d['games']]"
```

## What the report contains

1. **Headline** — auto-generated (e.g. "Angels Blank Padres" for a shutout, otherwise "Winner Top Loser, R–R").
2. **Subhead** — leading performer from the winning side.
3. **Dateline + lede** — venue, date, final score, top performer.
4. **Pitching paragraph** — WP line, save, LP.
5. **Scoring summary** — paragraph-form recap of every scoring play, grouped by half-inning, built from `liveData.plays.scoringPlays`.
6. **Supporting performances** — 2+ hit or multi-RBI games on both sides.
7. **Line score table** (handles extra innings).
8. **Full batting box** — AB/H/R/RBI/BB/K, away and home.
9. **Full pitching box** — IP/H/R/ER/BB/K with W/L/S tags.
10. **Notes** — WP, balks, HBP, pitch-timer violations, umpires, attendance, duration, weather, records.

## When the data isn't what you expect

- Games that aren't Final (in-progress, postponed, suspended) will still produce a report but the narrative may be incomplete. Check `game['status']['detailedState']` first if the user's intent is ambiguous.
- Spring training and minor-league games use different `sportId` values. The script uses `sportId=1` (MLB). If the user asks for a Triple-A or spring game, use the schedule API manually to get the gamePk, then pass it in.

## API reference

Everything comes from the public MLB Stats API (no auth needed):

- `GET https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD[&teamId=ID]`
- `GET https://statsapi.mlb.com/api/v1/game/{gamePk}/boxscore`
- `GET https://statsapi.mlb.com/api/v1/game/{gamePk}/linescore`
- `GET https://statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live` (used for decisions + scoring plays)

## Extending

If the user wants a variant (e.g. plain text, tweet-sized, include win probabilities, pitch-by-pitch), read the existing script first (`scripts/mlb-report`) and extend it rather than starting from scratch. The boxscore JSON has far more than is currently surfaced — advanced stats, fielding, baserunning, pitch counts, inherited runners — all already fetched, just not rendered.
