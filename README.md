# mlb-game-report

A [Claude Code](https://claude.com/claude-code) skill that produces newspaper-style, keepsake-quality MLB game reports. Sourced live from the public MLB Stats API, with graceful degradation for historical games going back to 1903.

## What it makes

For every game you run it against:

- A structured **CSV dataset directory** (the canonical source of truth)
- A **Markdown briefing** with an ALL-CAPS headline, AP-wire lede, fact box, key takeaways, atmosphere (including NOAA-computed sunrise/sunset at the venue), a Moment of the Game pulled from MLB's `captivatingIndex`, traditional line and box scores, scoring summary, and an itemized play-by-play with Statcast detail and scorebook tags (`HR` `1B` `2B` `BB` `K` `HBP` …)
- A self-contained **HTML version** styled in classic-newspaper typography (optional, via pandoc)
- A **zip bundle** of the dataset + rendered files (optional)
- An auto-updated **INDEX.md** in your games library

### A taste

The Markdown opens with something like:

```
# ANGELS BLANK PADRES, 8–0
### Schanuel 3-for-5, 1 RBI · Soriano fans 8 over 5⅔ IP

**ANGEL STADIUM, April 17, 2026.** Schanuel went 3 for 5 with 1 RBI and
Soriano tossed 5⅔ scoreless innings with 8 strikeouts as the Los Angeles
Angels blanked the San Diego Padres, 8–0, on Friday.
```

And every play-by-play entry looks like:

```
1. `HR` **Moncada** (vs. Waldron) — *Home Run.* ⚾ Yoán Moncada homers (3)…
    ↳ count 2-1, 4 pitches
    ↳ Four-Seam Fastball 92.5 mph, 2023 rpm · Batted: EV 104.3 mph, LA 34°,
      388 ft, fly ball, to CF
```

## Architecture

```
┌────────────────┐         ┌─────────────────────┐         ┌──────────────┐
│  MLB Stats API │ ───────▶│  mlb-fetch          │ ───────▶│  dataset/    │
│  (schedule,    │         │  (API → CSV)        │         │  *.csv       │
│   boxscore,    │         └─────────────────────┘         └──────┬───────┘
│   linescore,   │                                                │
│   feed/live)   │                                                ▼
└────────────────┘         ┌─────────────────────┐         ┌──────────────┐
                           │  mlb-render         │ ◀───────│  dataset/    │
                           │  (CSV → MD + HTML)  │         │  *.csv       │
                           └──────────┬──────────┘         └──────────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │  <slug>.md          │
                           │  <slug>.html        │
                           │  <slug>.zip (opt.)  │
                           └─────────────────────┘
```

Fetching and rendering are completely separate. The CSV dataset is the source of truth — the Markdown and HTML are reproducible views over it. You can hand-edit a CSV (say, to correct a weather reading) and re-run `mlb-render` without hitting the API.

## Dataset layout

Each game writes a directory like:

```
~/games-attended/2026-04-17-padres-at-angels/
├── game.csv            # 1 row: gamePk, date, teams, venue + location, weather,
│                       #        WP/LP/SV, records, attendance, attended metadata
├── linescore.csv       # 1 row per inning + R/H/E summary rows
├── batting.csv         # 1 row per batter: team, order, pos, AB/R/H/RBI/BB/K/HR,
│                       #                    season AVG, season HR
├── pitching.csv        # 1 row per pitcher: team, decision, IP/H/R/ER/BB/K/HR,
│                       #                     season ERA, total pitches
├── plays.csv           # 1 row per plate appearance: inning, half, batter, pitcher,
│                       #                              event, tag, desc, count, score,
│                       #                              is_scoring, captivating_index
└── pitches.csv         # 1 row per pitch: type, speed, spin, call, plate coords,
                        #                   exit velo, launch angle, distance,
                        #                   trajectory, fielded position, hardness
```

The rendered outputs sit alongside the directory at the library root:

```
~/games-attended/
├── INDEX.md
├── 2026-04-17-padres-at-angels.md
├── 2026-04-17-padres-at-angels.html
├── 2026-04-17-padres-at-angels.zip       ← optional bundle
└── 2026-04-17-padres-at-angels/          ← the CSV dataset directory
```

## Install

This is a [Claude Code user skill](https://docs.claude.com/en/docs/claude-code/skills). Clone it into your skills directory:

```bash
git clone https://github.com/agiacalone/mlb-game-report.git \
    ~/.claude/skills/mlb-game-report
```

Or keep it in your normal source tree and symlink:

```bash
git clone https://github.com/agiacalone/mlb-game-report.git ~/git/mlb-game-report
ln -s ~/git/mlb-game-report ~/.claude/skills/mlb-game-report
```

Verify Claude Code picked it up: run `/skills` inside Claude Code (or start a new session). `mlb-game-report` should appear in the available-skills list.

### Dependencies

- **Python 3.9+** (stdlib only — `csv`, `json`, `urllib`, `zipfile`, `zoneinfo`, `pathlib`)
- **[pandoc](https://pandoc.org/)** — *optional*, only needed for `.html` rendering: `brew install pandoc`. Without it you still get Markdown + CSV; HTML rendering gracefully skips with a note.

No external Python packages. No API key. No account.

## Three scripts

### `scripts/mlb-report` — one-command workflow

The everyday driver. Fetches, renders, optionally zips, and updates the INDEX:

```bash
# Attended-game keepsake (primary use case)
scripts/mlb-report --team LAA --date 2026-04-17 \
    --attended --section 220 --row K --seat 14 --with "my daughter" --html --zip --open

# By gamePk
scripts/mlb-report 824048

# Explicit output path (suppresses INDEX regeneration)
scripts/mlb-report 824048 -o /tmp/report.md

# Print Markdown to stdout, write nothing else
scripts/mlb-report 824048 --stdout
```

All flags:

| Flag | Meaning |
|---|---|
| `gamePk` (positional) | MLB gamePk (integer) |
| `--team TEAM --date YYYY-MM-DD` | Alternative to gamePk. Team is a numeric ID or abbreviation (LAA, SD, NYY, …) |
| `--attended` | Mark as a game you attended. Adds the 🎟 attended banner and a PERSONAL NOTES section. |
| `--section` / `--row` / `--seat` | Seat coordinates (shown in banner and INDEX) |
| `--with "NAMES"` | Comma-separated companions |
| `--html` | Also render `<slug>.html` via pandoc + bundled newspaper stylesheet |
| `--open` | Open the rendered HTML in your default browser (implies `--html`) |
| `--zip` | Bundle dataset directory + `.md` + `.html` into `<slug>.zip` (implies `--html`) |
| `-o PATH` | Override the `.md` output location |
| `--stdout` | Print Markdown to stdout; write nothing to disk |
| `--no-index` | Skip regenerating `INDEX.md` |

### `scripts/mlb-fetch` — dataset-only

Pulls from the MLB API and writes the CSV dataset directory. No rendering, no network-free operation required downstream. Useful when you want the raw data but not the Markdown:

```bash
scripts/mlb-fetch 824048 ~/games-attended/2026-04-17-padres-at-angels/
```

### `scripts/mlb-render` — render from dataset

Reads a dataset directory and writes `.md` (and optionally `.html`). Entirely network-free — works from a zip you downloaded weeks ago. Iterate on the stylesheet without re-hitting the API:

```bash
scripts/mlb-render ~/games-attended/2026-04-17-padres-at-angels/ --html --open
```

## Coverage across eras

The MLB Stats API has surprisingly deep historical coverage:

| Era | Regular season | World Series |
|---|:-:|:-:|
| 1903–1960 | box scores only | ✅ full PBP |
| 1961–1973 | ✅ PBP, sparse metadata | ✅ |
| 1974–2014 | ✅ PBP + attendance | ✅ |
| 2015+ | ✅ + full Statcast (exit velo, launch angle, spin, distance) | ✅ |

The script degrades gracefully. On historical games without Statcast, the PBP Statcast line falls back to qualitative descriptors (`Contact: medium-hit, line drive, to CF`). The "Statcast standouts" block becomes "Notables" and lists the home runs. Try it:

```bash
scripts/mlb-report 132727      # Bobby Thomson's Shot Heard 'Round the World, 1951
scripts/mlb-report 67359       # 1927 World Series Game 1 (Murderers' Row)
scripts/mlb-report 67218       # 1903 World Series Game 1
```

## Companion skill

For a **1930s radio broadcast transcript** version of any game report, install [mlb-announcer-1930s](https://github.com/agiacalone/mlb-announcer-1930s). That skill reads the Markdown (and, soon, the CSVs directly) and re-voices the game as a live Golden Age radio call — with Statcast metrics translated into period language ("the Signal Corps tracking apparatus tells us that one came off the bat at one hundred and four miles an hour").

## Data source

All data comes from the public MLB Stats API (no auth, no rate limit):

- `statsapi.mlb.com/api/v1/schedule`
- `statsapi.mlb.com/api/v1/game/{gamePk}/boxscore`
- `statsapi.mlb.com/api/v1/game/{gamePk}/linescore`
- `statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live`

Sunrise/sunset/civil-twilight times are computed locally from venue latitude, longitude, and game date using the standard NOAA solar-position algorithm (pure Python, stdlib only).

## License

MIT. See [`LICENSE`](LICENSE) if present.
