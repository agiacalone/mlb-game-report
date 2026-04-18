---
name: mlb-game-report
description: Use when the user asks for an MLB game recap, box score, game log, newspaper-style report, or keepsake record of a specific MLB game — by team + date, or by gamePk. The primary use case is generating permanent keepsake logs of games Anthony attended in person, with atmosphere, sunlight timing, moment-of-the-game, Statcast standouts, and a personal-notes section. Also supports plain reports for games not attended.
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
# Attended-game keepsake (default use case — writes to ~/games-attended/, updates INDEX.md)
~/.claude/skills/mlb-game-report/scripts/mlb-report --team LAA --date 2026-04-17 \
    --attended --section 220 --row K --seat 14 --with "my daughter"

# Not attended — still ends up in ~/games-attended/ unless -o overrides
~/.claude/skills/mlb-game-report/scripts/mlb-report 824048

# Explicit output file
~/.claude/skills/mlb-game-report/scripts/mlb-report 824048 -o /tmp/report.md

# Print to stdout
~/.claude/skills/mlb-game-report/scripts/mlb-report 824048 --stdout

# Also render a newspaper-styled HTML version (requires pandoc)
~/.claude/skills/mlb-game-report/scripts/mlb-report --team LAA --date 2026-04-17 --attended --html

# Render AND open in the browser
~/.claude/skills/mlb-game-report/scripts/mlb-report --team LAA --date 2026-04-17 --attended --open
```

The script accepts either a numeric team ID (e.g. `108`) or an abbreviation (`LAA`, `SD`, `NYY`, …).

### Attended-game flags (primary use case)

- `--attended` — stamps an "Attended." banner below the subhead, adds a **PERSONAL NOTES** section at the bottom, and writes `attended: true` into the frontmatter
- `--section`, `--row`, `--seat` — seat details shown in the banner and indexed
- `--with "names, here"` — comma-separated companions, shown in the banner

**Always pass `--attended` when the user says they went to the game, attended, was at the game, saw it live, etc.** The atmospherics, moment-of-the-game callout, and personal notes section are the whole point of this skill.

### Output path & library

- Default library: `~/games-attended/YYYY-MM-DD-<away_slug>-at-<home_slug>.md`
- After every write, the script regenerates `~/games-attended/INDEX.md` (sorted by date, with seat info and score). Use `--no-index` to skip.
- `-o PATH` overrides the default location but also disables automatic INDEX regeneration unless you point it back into `~/games-attended/`.
- Files begin with YAML frontmatter so the index scanner can read them; do not strip the frontmatter when re-editing.

### HTML rendering

- `--html` renders a sibling `.html` using pandoc + the bundled `scripts/newspaper.css` stylesheet (classic newspaper look: serif body, small-caps section headers, narrow column, tight box-score tables, § § § ornamental rules).
- The HTML is self-contained (styles inlined), so it opens in any browser and is safe to share.
- `--open` implies `--html` and additionally launches the file in the default browser via `open`.
- After any HTML write, `INDEX.md` links point to the `.html` sibling when it exists, and an `INDEX.html` is auto-generated too.
- Requires `pandoc` (via `brew install pandoc`). Without it, the script still writes the Markdown and warns to stderr.

### When the user only specifies "yesterday" / "last night"

Today's date is available in the system context (`# currentDate`). Subtract one day and pass `--date YYYY-MM-DD`. Do not guess — if unsure, ask.

### Multiple games in a day (doubleheaders)

The script warns to stderr and picks the first game. If the user wants game 2, prefer the MLB MCP to list both gamePks, then pass the correct one to the script:

- `mcp__mlb__get_schedule` with `date` (and optional `teamId`) returns all games for the date with their `gamePk` and `gameNumber`.

Fallback if the MCP is unavailable:

```bash
curl -s "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD&teamId=ID" | python3 -c "import json,sys; [print(g['gamePk'], g['gameNumber']) for d in json.load(sys.stdin)['dates'] for g in d['games']]"
```

## What the report contains

Structured as a **game briefing**: inverted-pyramid, skimmable at the top, fully detailed at the bottom. Preserves baseball traditions (ALL-CAPS newspaper headline, traditional line and box scores, IP fractions like 5⅔, W/L/S decision tags) while modernizing with fact boxes, key takeaway bullets, and Statcast detail.

1. **ALL-CAPS newspaper headline** (e.g. "ANGELS BLANK PADRES, 8–0") and a subhead naming the top hitter and starting pitcher.
2. **Dateline + AP-wire lede** — one sentence in classic wire style: *"Schanuel went 3 for 5 with 1 RBI and Soriano tossed 5⅔ scoreless innings with 8 strikeouts as the Los Angeles Angels blanked the San Diego Padres, 8–0, on Friday."*
3. **AT A GLANCE** — two-column fact box: final, venue, att, time, weather, WP/LP/SV, umpires, team records. The single most-scannable block.
4. **KEY TAKEAWAYS** — three bullets: **Pitching** (winner's line with season ERA + loser), **Offense** (home runs + top hitters on both sides), **Turning point** (biggest scoring inning).
5. **ATMOSPHERE** — time-of-day classification with precise local sunset ("Late-afternoon start — sun still up at first pitch, setting at 7:24 PM; middle innings slide into dusk. Full dark around 7:50 PM."), sun rise/set/civil-twilight times, weather + wind, stadium location/elevation/field azimuth, crowd size, and **Statcast standouts** (longest HR, hardest-hit ball, fastest pitch).
6. **MOMENT OF THE GAME** — a blockquote pulling the play with the highest MLB `captivatingIndex`, with matchup, count, Statcast numbers, and the MLB-provided description.
7. **HOW IT HAPPENED** — one-sentence scoring-inning summary ("Angels scored 3 in the 2nd, 3 in the 4th, and 2 in the 5th.").
8. **SCORING** — traditional one-line-per-run format: `**ANGELS 2nd.** Moncada homers to center. *Padres 0, Angels 1.*`
9. **LINE SCORE** table (traditional; handles extra innings; bolds the R column).
10. **BOX SCORE — BATTING** — AB/R/H/RBI/BB/K/AVG with totals row, away and home.
11. **BOX SCORE — PITCHING** — IP (as 5⅔)/H/R/ER/BB/K/HR/ERA with W/L/S tags.
12. **NOTES** — WP, balks, HBP, IBB, SB, CS, E, DP, pitch-timer violations, etc.
13. **PLAY-BY-PLAY** — every plate appearance itemized MLB Gameday-style under a half-inning header (`### Bottom 2nd — Angels batting · Padres pitching`). Each item: batter (vs. pitcher), event type, full MLB description, scoring tag, count/pitch count, and a Statcast line with last pitch type/velocity/spin and (for balls in play) EV/LA/distance/trajectory/fielded position. Mid-inning pitching changes annotated.
14. **PERSONAL NOTES** *(only if `--attended`)* — empty template section at the bottom for hand-editing memories.

## When the data isn't what you expect

- Games that aren't Final (in-progress, postponed, suspended) will still produce a report but the narrative may be incomplete. Check `game['status']['detailedState']` first if the user's intent is ambiguous.
- Spring training and minor-league games use different `sportId` values. The script uses `sportId=1` (MLB). If the user asks for a Triple-A or spring game, use the schedule API manually to get the gamePk, then pass it in.

## API reference

The core report is generated by `scripts/mlb-report`, which calls the public MLB Stats API directly (no auth):

- `GET https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD[&teamId=ID]`
- `GET https://statsapi.mlb.com/api/v1/game/{gamePk}/boxscore`
- `GET https://statsapi.mlb.com/api/v1/game/{gamePk}/linescore`
- `GET https://statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live` (used for decisions + scoring plays)

Do **not** replace the script with MCP calls — the report depends on the full boxscore/linescore/feed-live payloads (PBP, Statcast, captivatingIndex) which the MCP does not expose in equivalent depth.

### MLB MCP tools — prefer when applicable

An `mcp__mlb__*` server is installed. Prefer it for *lookups around* a report (faster, no JSON wrangling) while leaving the main recap to the script:

| Need | Tool |
|---|---|
| Resolve doubleheader gamePks for a date/team | `mcp__mlb__get_schedule` |
| Confirm a team's recent/next game when the user is vague ("last home game") | `mcp__mlb__get_team_schedule` |
| Player bio / handedness / birthplace for color | `mcp__mlb__search_player` → `mcp__mlb__get_player_bio` |
| Season or career stats to contextualize a performance | `mcp__mlb__get_player_stats` |
| Standings line for the post-game records | `mcp__mlb__get_standings` |
| League leaderboard context ("how does 8 K rank?") | `mcp__mlb__get_stat_leaders` |
| Roster moves around game date (call-up, IL, trade) | `mcp__mlb__get_transactions` |
| Venue / division / franchise context | `mcp__mlb__get_team_info`, `mcp__mlb__get_team_roster` |

`mcp__mlb__get_game_recap` returns a summary only — do **not** use it to generate the keepsake report; the script's output is canonical.

## Extending

If the user wants a variant (e.g. plain text, tweet-sized, include win probabilities, pitch-by-pitch), read the existing script first (`scripts/mlb-report`) and extend it rather than starting from scratch. The boxscore JSON has far more than is currently surfaced — advanced stats, fielding, baserunning, pitch counts, inherited runners — all already fetched, just not rendered.
