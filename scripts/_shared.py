"""Shared helpers and renderer for the mlb-game-report toolchain.

Used by ``mlb-fetch`` (writes the dataset CSVs) and ``mlb-render`` (reads them,
emits the Markdown report). The ``render_markdown`` function is the single
source of truth for Markdown output — ``mlb-report`` invokes it indirectly by
running fetch then render. Keep everything here stdlib-only.
"""
from __future__ import annotations

import csv
import json
import math
import re
from datetime import date as _date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Constants --------------------------------------------------------------

API = "https://statsapi.mlb.com/api/v1"
API11 = "https://statsapi.mlb.com/api/v1.1"

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th",
           6: "6th", 7: "7th", 8: "8th", 9: "9th", 10: "10th",
           11: "11th", 12: "12th"}
ORDINAL_WORD = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
                6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth"}

# Statcast fielding position codes (1=P through 9=RF)
POS = {1: "P", 2: "C", 3: "1B", 4: "2B", 5: "3B", 6: "SS", 7: "LF", 8: "CF", 9: "RF"}

# Scorebook-style tags per plate-appearance outcome.
EVENT_TAG: dict[str, str] = {
    "Single": "1B",
    "Double": "2B",
    "Triple": "3B",
    "Home Run": "HR",
    "Walk": "BB",
    "Intent Walk": "IBB",
    "Strikeout": "K",
    "Strikeout - DP": "K-DP",
    "Strikeout Double Play": "K-DP",
    "Hit By Pitch": "HBP",
    "Sac Fly": "SF",
    "Sac Fly Double Play": "SF-DP",
    "Sac Bunt": "SH",
    "Sacrifice Bunt DP": "SH-DP",
    "Fielders Choice": "FC",
    "Fielders Choice Out": "FC",
    "Grounded Into DP": "GIDP",
    "Double Play": "DP",
    "Triple Play": "TP",
    "Field Error": "E",
    "Catcher Interference": "CI",
    "Batter Interference": "BI",
    "Fan Interference": "FI",
    "Groundout": "GO",
    "Flyout": "FO",
    "Lineout": "LO",
    "Pop Out": "PO",
    "Forceout": "FC",
    "Bunt Groundout": "GO",
    "Bunt Lineout": "LO",
    "Bunt Pop Out": "PO",
}

NOTABLE_KEYS = ["WP", "Balk", "HBP", "IBB", "SB", "CS", "E", "DP",
                "Disengagement violations", "Pitch timer violations",
                "Pickoffs", "Ejections"]

CSS_FILE = Path(__file__).resolve().parent / "newspaper.css"
LIBRARY = Path.home() / "games-attended"

# --- Small utilities --------------------------------------------------------

_SUFFIX_RE = re.compile(r"\b(Jr|Sr|II|III|IV)\.")


def fmt_date(iso: str) -> str:
    y, m, d = map(int, iso.split("-"))
    return _date(y, m, d).strftime("%B %-d, %Y")


def weekday(iso: str) -> str:
    y, m, d = map(int, iso.split("-"))
    return _date(y, m, d).strftime("%A")


def sun_event(lat: float, lon: float, d: _date, tz: ZoneInfo, zenith: float = 90.833,
              sunrise: bool = False) -> datetime | None:
    """NOAA sunrise/sunset on local date ``d``. zenith=90.833 is official sunset,
    96.0 is civil twilight end. Returns ``None`` at extreme latitudes."""
    N = d.timetuple().tm_yday
    lng_hour = lon / 15.0
    t = N + (((6 if sunrise else 18) - lng_hour) / 24.0)
    M = (0.9856 * t) - 3.289
    L = M + (1.916 * math.sin(math.radians(M))) + (0.020 * math.sin(math.radians(2 * M))) + 282.634
    L = L % 360
    RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L))))
    RA = RA % 360
    Lq = (L // 90) * 90
    RAq = (RA // 90) * 90
    RA = (RA + (Lq - RAq)) / 15.0
    sinDec = 0.39782 * math.sin(math.radians(L))
    cosDec = math.cos(math.asin(sinDec))
    cosH = (math.cos(math.radians(zenith)) - (sinDec * math.sin(math.radians(lat)))) / (cosDec * math.cos(math.radians(lat)))
    if cosH > 1 or cosH < -1:
        return None
    H = math.degrees(math.acos(cosH))
    if sunrise:
        H = 360 - H
    H = H / 15.0
    T = H + RA - (0.06571 * t) - 6.622
    UT = (T - lng_hour) % 24
    offset_hours = tz.utcoffset(datetime(d.year, d.month, d.day, 12)).total_seconds() / 3600
    local = (UT + offset_hours) % 24
    hh = int(local)
    mm = int(round((local - hh) * 60))
    if mm == 60:
        hh += 1
        mm = 0
    hh %= 24
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)


def parse_first_pitch(first_pitch_str: str, game_date_iso: str, tz: ZoneInfo) -> datetime | None:
    s = first_pitch_str.rstrip(". ").strip()
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).time()
            y, m, d = map(int, game_date_iso.split("-"))
            return datetime(y, m, d, t.hour, t.minute, tzinfo=tz)
        except ValueError:
            continue
    return None


def fmt_local_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_ip(ip) -> str:
    if ip is None or ip == "":
        return "0"
    s = str(ip)
    if "." in s:
        whole, frac = s.split(".", 1)
        return whole + {"0": "", "1": "⅓", "2": "⅔"}.get(frac[:1], "." + frac)
    return s


def ord_abbr(n: int) -> str:
    return ORDINAL.get(n, f"{n}th")


def ord_word(n: int) -> str:
    return ORDINAL_WORD.get(n, f"{n}th")


def short_name(full: str) -> str:
    parts = full.split()
    return parts[-1] if len(parts) > 1 else full


def last_name(full: str) -> str:
    parts = full.split()
    if len(parts) >= 2 and parts[-1] in ("Jr.", "Sr.", "II", "III", "IV"):
        return " ".join(parts[-2:])
    return parts[-1] if parts else full


def strip_period(s: str) -> str:
    return s.rstrip(". ").strip()


def first_sentence(s: str) -> str:
    s = s.strip()
    protected = _SUFFIX_RE.sub(lambda m: m.group(1) + "\u0000", s)
    m = re.match(r"^(.+?\.)(\s|$)", protected)
    out = m.group(1) if m else protected
    return out.replace("\u0000", ".")


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# --- CSV helpers ------------------------------------------------------------

def _cast(v: str):
    """Best-effort string → scalar for CSV reads. Empty string stays as ''.
    Integer-looking values become int; everything else stays as-is (we preserve
    the original text for fractional stats like AVG .247 and ERA 1.80 so the
    rendered tables match the live-feed output byte-for-byte)."""
    if v == "":
        return ""
    if v in ("True", "False"):
        return v == "True"
    if isinstance(v, str) and v.lstrip("-").isdigit():
        return int(v)
    return v


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{k: _cast(v) for k, v in row.items()} for row in csv.DictReader(f)]


# --- Dataset schemas --------------------------------------------------------

GAME_FIELDS = [
    "gamePk", "date", "game_type", "status", "venue", "venue_id", "city", "state",
    "lat", "lon", "elevation", "azimuth", "tz", "dayNight", "first_pitch",
    "sunrise", "sunset", "civil_twilight_end", "duration", "attendance", "weather",
    "wind", "umpires", "away_team", "away_team_id", "away_team_short",
    "home_team", "home_team_id", "home_team_short", "away_score", "home_score",
    "winning_pitcher", "losing_pitcher", "save_pitcher",
    "away_record", "home_record", "attended", "seat_section", "seat_row",
    "seat_number", "companions", "captivating_play_idx",
    # Additions beyond the spec, needed to render without the raw feed:
    "notable_events_json",
]
LINESCORE_FIELDS = ["inning", "away_runs", "home_runs"]
BATTING_FIELDS = ["team", "order", "name", "player_id", "position",
                  "ab", "r", "h", "rbi", "bb", "k", "hr", "avg_season",
                  "hr_season", "is_starter"]
PITCHING_FIELDS = ["team", "order", "name", "player_id", "decision",
                   "ip", "h", "r", "er", "bb", "k", "hr", "era_season", "pitches"]
PLAY_FIELDS = ["idx", "inning", "half", "batter", "batter_id", "pitcher",
               "pitcher_id", "event", "event_tag", "description", "balls",
               "strikes", "pitch_count", "away_score_after", "home_score_after",
               "is_scoring_play", "captivating_index", "rbi"]
PITCH_FIELDS = ["play_idx", "pitch_num", "type_code", "type_desc", "speed_mph",
                "spin_rpm", "call", "px", "pz", "ev_mph", "la_deg",
                "distance_ft", "trajectory", "hit_location", "hardness"]


def write_dataset(dirpath: Path, dataset: dict) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    write_csv(dirpath / "game.csv", GAME_FIELDS, [dataset["game"]])
    write_csv(dirpath / "linescore.csv", LINESCORE_FIELDS, dataset["linescore"])
    write_csv(dirpath / "batting.csv", BATTING_FIELDS, dataset["batting"])
    write_csv(dirpath / "pitching.csv", PITCHING_FIELDS, dataset["pitching"])
    write_csv(dirpath / "plays.csv", PLAY_FIELDS, dataset["plays"])
    if dataset.get("pitches"):
        write_csv(dirpath / "pitches.csv", PITCH_FIELDS, dataset["pitches"])


def read_dataset(dirpath: Path) -> dict:
    game_rows = read_csv(dirpath / "game.csv")
    if not game_rows:
        raise SystemExit(f"No game.csv in {dirpath}")
    return {
        "game": game_rows[0],
        "linescore": read_csv(dirpath / "linescore.csv"),
        "batting": read_csv(dirpath / "batting.csv"),
        "pitching": read_csv(dirpath / "pitching.csv"),
        "plays": read_csv(dirpath / "plays.csv"),
        "pitches": read_csv(dirpath / "pitches.csv"),
    }


# --- Statcast formatting ----------------------------------------------------

def statcast_line_for_play(play_idx: int, pitches: list[dict]) -> str | None:
    """Format the one-line Statcast annotation for a play from its pitch rows."""
    play_pitches = [p for p in pitches if p.get("play_idx") == play_idx]
    if not play_pitches:
        return None
    last_pitch = play_pitches[-1]
    hit_rows = [p for p in play_pitches if p.get("ev_mph") not in (None, "") or p.get("trajectory") or p.get("distance_ft") not in (None, "") or p.get("hardness")]
    hit = hit_rows[-1] if hit_rows else None

    bits: list[str] = []
    ptype = last_pitch.get("type_desc") or last_pitch.get("type_code")
    speed = last_pitch.get("speed_mph")
    spin = last_pitch.get("spin_rpm")
    if ptype and speed not in (None, ""):
        s = f"{ptype} {float(speed):.1f} mph"
        if spin not in (None, ""):
            s += f", {int(float(spin))} rpm"
        bits.append(s)
    elif speed not in (None, ""):
        bits.append(f"{float(speed):.1f} mph")

    if hit:
        ls = hit.get("ev_mph")
        la = hit.get("la_deg")
        dist = hit.get("distance_ft")
        traj = hit.get("trajectory")
        loc = hit.get("hit_location")
        hard = hit.get("hardness")
        parts: list[str] = []
        numeric_present = any(v not in (None, "") for v in (ls, la, dist))
        if ls not in (None, ""):
            parts.append(f"EV {float(ls):.1f} mph")
        if la not in (None, ""):
            parts.append(f"LA {float(la):.0f}°")
        if dist not in (None, ""):
            parts.append(f"{int(float(dist))} ft")
        if hard and (ls in (None, "")):
            parts.append(f"{str(hard).lower()}-hit")
        if traj:
            parts.append(str(traj).replace("_", " "))
        if loc not in (None, ""):
            try:
                parts.append(f"to {POS[int(float(loc))]}")
            except (ValueError, KeyError):
                parts.append(f"to {loc}")
        if parts:
            label = "Batted" if numeric_present else "Contact"
            bits.append(f"{label}: " + ", ".join(parts))

    return " · ".join(bits) if bits else None


# --- Renderer ---------------------------------------------------------------

def _top_batters(batting: list[dict], team_short: str, n: int = 3) -> list[dict]:
    rows = [b for b in batting if b.get("team") == team_short
            and b.get("ab") not in (None, "") and int(b.get("ab") or 0) > 0]
    rows.sort(key=lambda r: (int(r.get("h") or 0), int(r.get("rbi") or 0), int(r.get("hr") or 0)),
              reverse=True)
    return rows[:n]


def render_markdown(dataset: dict) -> tuple[str, dict]:
    g = dataset["game"]
    linescore = dataset["linescore"]
    batting = dataset["batting"]
    pitching = dataset["pitching"]
    plays = dataset["plays"]
    pitches = dataset["pitches"]

    away_name = g["away_team"]
    home_name = g["home_team"]
    away_short = g["away_team_short"]
    home_short = g["home_team_short"]
    away_r = int(g["away_score"] or 0)
    home_r = int(g["home_score"] or 0)
    winner_name = home_name if home_r > away_r else away_name
    loser_name = away_name if home_r > away_r else home_name
    w_score, l_score = (home_r, away_r) if home_r > away_r else (away_r, home_r)
    winner_short = home_short if home_r > away_r else away_short
    loser_short = away_short if home_r > away_r else home_short
    at_home = home_r > away_r

    wp = g.get("winning_pitcher") or ""
    lp = g.get("losing_pitcher") or ""
    sv = g.get("save_pitcher") or ""

    # Headline + subhead
    if l_score == 0:
        headline = f"{winner_short.upper()} BLANK {loser_short.upper()}, {w_score}–0"
    else:
        headline = f"{winner_short.upper()} TOP {loser_short.upper()}, {w_score}–{l_score}"

    w_side_short = winner_short
    tops = _top_batters(batting, w_side_short, 1)
    subhead_bits: list[str] = []
    if tops:
        t = tops[0]
        bit = f"{last_name(t['name'])} {t.get('h',0)}-for-{t.get('ab',0)}"
        if int(t.get("hr") or 0):
            bit += f", {t['hr']} HR"
        if int(t.get("rbi") or 0):
            bit += f", {t['rbi']} RBI"
        subhead_bits.append(bit)

    # Winning starter = first pitcher (order=1) of winning team
    w_pitchers = sorted([p for p in pitching if p.get("team") == winner_short],
                        key=lambda p: int(p.get("order") or 999))
    starter = w_pitchers[0] if w_pitchers else None
    if starter and starter["name"] == wp:
        subhead_bits.append(f"{last_name(wp)} fans {starter.get('k',0)} over {fmt_ip(starter.get('ip'))} IP")
    subhead = "### " + " · ".join(subhead_bits) if subhead_bits else ""

    venue = g.get("venue") or ""
    dateline = f"**{venue.upper() if venue else 'MLB'}, {fmt_date(g['date'])}.**"

    # --- AP lede ---
    verb = "blanked" if l_score == 0 else "defeated"
    day = weekday(g["date"])
    clauses: list[str] = []
    if tops:
        t = tops[0]
        hit_clause = f"{last_name(t['name'])} went {t.get('h',0)} for {t.get('ab',0)}"
        extras: list[str] = []
        if int(t.get("hr") or 0):
            extras.append("a home run")
        if int(t.get("rbi") or 0):
            extras.append(f"{t['rbi']} RBI")
        if extras:
            hit_clause += " with " + " and ".join(extras)
        clauses.append(hit_clause)
    if wp and starter and starter["name"] == wp:
        ip = fmt_ip(starter.get("ip"))
        runs = int(starter.get("r") or 0)
        ks = int(starter.get("k") or 0)
        if runs == 0:
            clauses.append(f"{last_name(wp)} tossed {ip} scoreless innings with {ks} strikeouts")
        else:
            clauses.append(f"{last_name(wp)} worked {ip} innings, striking out {ks}")
    who = " and ".join(clauses) if clauses else ""
    body = f"the {winner_name} {verb} the {loser_name}, {w_score}–{l_score}, on {day}"
    lede_sentence = f"{who} as {body}." if who else f"The {winner_name} {verb} the {loser_name}, {w_score}–{l_score}, on {day}."
    lede_para = f"{dateline} {lede_sentence}"

    # --- Takeaways ---
    bullets: list[str] = []
    # Pitching
    pit_parts: list[str] = []
    if wp:
        if starter and starter["name"] == wp:
            ip = fmt_ip(starter.get("ip"))
            line_ = (f"**{last_name(wp)} (W)** — {ip} IP, {starter.get('h',0)} H, "
                     f"{starter.get('r',0)} R, {starter.get('er',0)} ER, "
                     f"{starter.get('bb',0)} BB, {starter.get('k',0)} K")
            if starter.get("era_season") not in (None, "", "—"):
                line_ += f" · season ERA {starter['era_season']}"
            pit_parts.append(line_)
        else:
            pit_parts.append(f"**{last_name(wp)} (W)** in relief")
    if sv:
        pit_parts.append(f"{last_name(sv)} (S)")
    if lp:
        lp_row = next((p for p in pitching if p["name"] == lp), None)
        if lp_row:
            ip = fmt_ip(lp_row.get("ip"))
            pit_parts.append(f"**{last_name(lp)} (L)** — {ip} IP, {lp_row.get('er',0)} ER")
        else:
            pit_parts.append(f"**{last_name(lp)} (L)**")
    if pit_parts:
        bullets.append("**Pitching.** " + " · ".join(pit_parts) + ".")

    # Offense: HRs from plays (season-HR tag from batting.csv)
    batter_season_hr = {int(b["player_id"]): b.get("hr_season")
                        for b in batting if b.get("player_id") not in (None, "")}
    hrs: list[str] = []
    for pl in plays:
        if pl.get("event") == "Home Run":
            batter = last_name(pl["batter"])
            half = pl["half"]
            team = away_short if half == "top" else home_short
            bid = pl.get("batter_id")
            season_hr = batter_season_hr.get(int(bid)) if bid not in (None, "") else None
            tag = f"({season_hr})" if season_hr not in (None, "") else ""
            hrs.append(f"{batter} {tag}, {team}".strip())
    off_parts: list[str] = []
    if hrs:
        off_parts.append("HR: " + "; ".join(hrs))
    w_top = _top_batters(batting, winner_short, 2)
    hot = []
    for t in w_top:
        h = int(t.get("h") or 0)
        rbi = int(t.get("rbi") or 0)
        if h >= 2 or rbi >= 1:
            s = f"{last_name(t['name'])} {h}-for-{t.get('ab',0)}"
            if rbi:
                s += f", {rbi} RBI"
            hot.append(s)
    if hot:
        off_parts.append(f"{winner_short}: " + "; ".join(hot))
    l_top = _top_batters(batting, loser_short, 1)
    if l_top:
        t = l_top[0]
        h = int(t.get("h") or 0)
        rbi = int(t.get("rbi") or 0)
        if h >= 2 or rbi >= 1:
            off_parts.append(f"{loser_short}: {last_name(t['name'])} {h}-for-{t.get('ab',0)}")
    if off_parts:
        bullets.append("**Offense.** " + " · ".join(off_parts) + ".")

    # Turning point: biggest scoring inning for winner
    big_inn = None
    big_runs = 0
    for inn in linescore:
        n = inn.get("inning")
        if n in ("R", "H", "E"):
            continue
        r = int((inn.get("home_runs") if at_home else inn.get("away_runs")) or 0)
        if r > big_runs:
            big_runs = r
            big_inn = int(n)
    if big_inn and big_runs >= 2:
        bullets.append(f"**Turning point.** {winner_short} plated **{big_runs} in the {ord_abbr(big_inn)}** to break the game open.")
    elif big_inn:
        bullets.append(f"**Turning point.** {winner_short}'s decisive run came in the {ord_abbr(big_inn)}.")

    takeaways_block = "\n".join(f"- {b}" for b in bullets) if bullets else "_—_"

    # --- Scoring ---
    scoring_lines: list[str] = []
    for pl in plays:
        if not pl.get("is_scoring_play"):
            continue
        inn = int(pl["inning"])
        team_label = (away_short if pl["half"] == "top" else home_short).upper()
        desc = first_sentence((pl.get("description") or "").strip()).replace("\n", " ")
        a = int(pl.get("away_score_after") or 0)
        h = int(pl.get("home_score_after") or 0)
        score_str = f"{away_short} {a}, {home_short} {h}"
        marker = "⚾ " if pl.get("event") == "Home Run" else ""
        scoring_lines.append(f"{marker}**{team_label} {ord_abbr(inn)}.** {desc} *{score_str}.*")
    scoring_block = "\n\n".join(scoring_lines) if scoring_lines else "_No scoring plays._"

    # --- Line score ---
    inning_rows = [i for i in linescore if i.get("inning") not in ("R", "H", "E")]
    max_inn = max(9, len(inning_rows))
    header = "| Team | " + " | ".join(str(i) for i in range(1, max_inn + 1)) + " | R | H | E |"
    sep = "|------|" + "|".join(["---"] * max_inn) + "|---|---|---|"

    def _totals(side: str) -> tuple[int, int, int]:
        r = next((row for row in linescore if row.get("inning") == "R"), {})
        h = next((row for row in linescore if row.get("inning") == "H"), {})
        e = next((row for row in linescore if row.get("inning") == "E"), {})
        key = f"{side}_runs"
        return (int(r.get(key) or 0), int(h.get(key) or 0), int(e.get(key) or 0))

    def _line_row(team_name: str, side: str) -> str:
        cells = []
        for i in range(1, max_inn + 1):
            inn = next((x for x in inning_rows if int(x["inning"]) == i), None)
            if inn is None:
                cells.append("–")
            else:
                v = inn.get(f"{side}_runs")
                cells.append(str(v) if v not in (None, "") else "–")
        r, h, e = _totals(side)
        return f"| {team_name} | " + " | ".join(cells) + f" | **{r}** | {h} | {e} |"

    line_table = "\n".join([header, sep, _line_row(away_name, "away"), _line_row(home_name, "home")])

    # --- Play-by-play game log ---
    inning_order: list[tuple[int, str]] = []
    by_half: dict[tuple[int, str], list[dict]] = {}
    for pl in plays:
        inn = int(pl["inning"])
        half = pl["half"]
        key = (inn, half)
        if key not in by_half:
            by_half[key] = []
            inning_order.append(key)
        if (pl.get("description") or "").strip():
            by_half[key].append(pl)

    log_sections: list[str] = []
    for (inn, half) in inning_order:
        half_plays = by_half.get((inn, half), [])
        if not half_plays:
            continue
        bat_team = away_short if half == "top" else home_short
        pit_team = home_short if half == "top" else away_short
        inning_glyph = "▲" if half == "top" else "▼"
        header_line = f"### {inning_glyph} {half.capitalize()} {ord_abbr(inn)} — {bat_team} batting · {pit_team} pitching"
        items: list[str] = []
        last_pitcher: str | None = None
        for i, pl in enumerate(half_plays, 1):
            batter = last_name(pl["batter"])
            pitcher = last_name(pl["pitcher"])
            event = (pl.get("event") or "").strip() or "Play"
            desc = (pl.get("description") or "").strip().replace("\n", " ")
            pitch_count = int(pl.get("pitch_count") or 0)
            b = int(pl.get("balls") or 0)
            s = int(pl.get("strikes") or 0)
            meta_bits = [f"count {b}-{s}"]
            if pitch_count:
                meta_bits.append(f"{pitch_count} pitch{'es' if pitch_count != 1 else ''}")
            scored = bool(pl.get("is_scoring_play"))
            a = int(pl.get("away_score_after") or 0)
            h = int(pl.get("home_score_after") or 0)

            if last_pitcher and pitcher != last_pitcher:
                items.append(f"    *— Pitching change: {last_pitcher} → {pitcher}. —*")
            last_pitcher = pitcher

            event_marker = " ⚾" if event == "Home Run" else ""
            tag = pl.get("event_tag") or ""
            tag_prefix = f"`{tag}` " if tag else ""
            row = f"{i}. {tag_prefix}**{batter}** (vs. {pitcher}) — *{event}.*{event_marker} {desc}"
            if scored:
                row += f" **[{away_short} {a}, {home_short} {h}]**"
            row += f"  \n    ↳ " + ", ".join(meta_bits)
            sc = statcast_line_for_play(int(pl["idx"]), pitches)
            if sc:
                row += f"  \n    ↳ {sc}"
            items.append(row)
        log_sections.append(header_line + "\n\n" + "\n".join(items))
    game_log = "\n\n".join(log_sections) if log_sections else "_No play-by-play data available._"

    # --- Box score tables ---
    def bat_table(team_short: str) -> str:
        rows = ["| Player | Pos | AB | R | H | RBI | BB | K | AVG |",
                "|--------|-----|----|----|----|-----|----|----|-----|"]
        totals = dict(ab=0, r=0, h=0, rbi=0, bb=0, k=0)
        team_rows = sorted([b for b in batting if b.get("team") == team_short],
                           key=lambda b: int(b.get("order") or 999))
        for b in team_rows:
            if b.get("ab") in (None, ""):
                continue
            name = b["name"]
            pos = b.get("position") or ""
            avg = b.get("avg_season") if b.get("avg_season") not in (None, "") else "—"
            rows.append(f"| {name} | {pos} | {b.get('ab',0)} | {b.get('r',0)} | {b.get('h',0)} | {b.get('rbi',0)} | {b.get('bb',0)} | {b.get('k',0)} | {avg} |")
            totals["ab"] += int(b.get("ab") or 0)
            totals["r"] += int(b.get("r") or 0)
            totals["h"] += int(b.get("h") or 0)
            totals["rbi"] += int(b.get("rbi") or 0)
            totals["bb"] += int(b.get("bb") or 0)
            totals["k"] += int(b.get("k") or 0)
        rows.append(f"| **Totals** | | **{totals['ab']}** | **{totals['r']}** | **{totals['h']}** | **{totals['rbi']}** | **{totals['bb']}** | **{totals['k']}** | |")
        return "\n".join(rows)

    def pit_table(team_short: str) -> str:
        rows = ["| Pitcher | IP | H | R | ER | BB | K | HR | ERA |",
                "|---------|----|----|----|----|----|----|----|-----|"]
        team_rows = sorted([p for p in pitching if p.get("team") == team_short],
                           key=lambda p: int(p.get("order") or 999))
        for p in team_rows:
            if p.get("ip") in (None, ""):
                continue
            name = p["name"]
            tag = p.get("decision") or ""
            label = f"{name}{' ('+tag+')' if tag else ''}"
            era = p.get("era_season") if p.get("era_season") not in (None, "") else "—"
            rows.append(f"| {label} | {fmt_ip(p.get('ip'))} | {p.get('h',0)} | {p.get('r',0)} | {p.get('er',0)} | {p.get('bb',0)} | {p.get('k',0)} | {p.get('hr',0)} | {era} |")
        return "\n".join(rows)

    # --- At a glance ---
    att = g.get("attendance") or "—"
    dur = g.get("duration") or "—"
    wx = g.get("weather") or "—"
    wind = g.get("wind") or ""
    first_pitch = g.get("first_pitch") or "—"
    umps = g.get("umpires") or "—"
    at_glance = (
        f"| | |\n|---|---|\n"
        f"| **Final** | {winner_name} {w_score}, {loser_name} {l_score} |\n"
        f"| **Venue** | {venue} · Att. {att} |\n"
        f"| **Time** | {dur} · First pitch {first_pitch} |\n"
        f"| **Weather** | {wx}{(' · Wind ' + str(wind)) if wind else ''} |\n"
        f"| **Winning pitcher** | {wp or '—'} |\n"
        f"| **Losing pitcher** | {lp or '—'} |\n"
        + (f"| **Save** | {sv} |\n" if sv else "")
        + f"| **Umpires** | {umps} |\n"
        f"| **Records** | {away_short} {g.get('away_record','')} at {home_short} {g.get('home_record','')} |"
    )

    # --- Atmosphere ---
    atmosphere_rows: list[str] = []
    lat = g.get("lat")
    lon = g.get("lon")
    tz_id = g.get("tz")
    day_night = g.get("dayNight") or ""
    game_date_iso = g["date"]

    sunrise_dt = sunset_dt = civil_end_dt = None
    if lat not in (None, "") and lon not in (None, "") and tz_id:
        tz = ZoneInfo(tz_id)
        y, m, d = map(int, game_date_iso.split("-"))
        gd = _date(y, m, d)
        sunrise_dt = sun_event(float(lat), float(lon), gd, tz, sunrise=True)
        sunset_dt = sun_event(float(lat), float(lon), gd, tz, sunrise=False)
        civil_end_dt = sun_event(float(lat), float(lon), gd, tz, zenith=96.0, sunrise=False)
        fp_dt = parse_first_pitch(first_pitch, game_date_iso, tz) if first_pitch and first_pitch != "—" else None
        sunlight_phrase = ""
        if sunset_dt and fp_dt:
            delta_min = (sunset_dt - fp_dt).total_seconds() / 60
            if delta_min > 60:
                sunlight_phrase = f"Day game — full sunlight through the early innings (sunset {fmt_local_time(sunset_dt)})"
            elif delta_min > 10:
                sunlight_phrase = f"Late-afternoon start — sun still up at first pitch, setting at {fmt_local_time(sunset_dt)}; middle innings slide into dusk"
            elif delta_min > -30:
                sunlight_phrase = f"Twilight start — first pitch right around sunset ({fmt_local_time(sunset_dt)})"
            else:
                sunlight_phrase = f"Night game — sun already down at first pitch (sunset was {fmt_local_time(sunset_dt)})"
            if civil_end_dt:
                sunlight_phrase += f". Full dark around {fmt_local_time(civil_end_dt)}"
            sunlight_phrase += "."
        elif sunset_dt:
            sunlight_phrase = f"Sunset {fmt_local_time(sunset_dt)} local."
        atmosphere_rows.append(f"- **Time of day.** {sunlight_phrase or day_night.title()}")
        if sunrise_dt:
            atmosphere_rows.append(
                f"- **Sun.** Rise {fmt_local_time(sunrise_dt)} · Set {fmt_local_time(sunset_dt) if sunset_dt else '—'}"
                + (f" · Civil twilight ends {fmt_local_time(civil_end_dt)}" if civil_end_dt else "")
            )
    elif day_night:
        atmosphere_rows.append(f"- **Time of day.** {day_night.title()} game.")

    if wx and wx != "—":
        wx_lower = str(wx).lower()
        if "dome" in wx_lower or "roof closed" in wx_lower:
            wx_glyph = "⛨"
        elif "snow" in wx_lower:
            wx_glyph = "❄"
        elif "rain" in wx_lower or "drizzle" in wx_lower or "shower" in wx_lower:
            wx_glyph = "☂"
        elif "cloud" in wx_lower or "overcast" in wx_lower:
            wx_glyph = "☁"
        elif "clear" in wx_lower or "sunny" in wx_lower or "fair" in wx_lower:
            wx_glyph = "☀"
        else:
            wx_glyph = "•"
        atmosphere_rows.append(f"- **Weather.** {wx_glyph} {wx}" + (f" · Wind {wind}" if wind else ""))
    if g.get("city") and g.get("state"):
        atmosphere_rows.append(
            f"- **Where.** {venue}, {g['city']}, {g['state']} · elevation {g.get('elevation','—') or '—'} ft · field azimuth {g.get('azimuth','—') or '—'}°"
        )
    crowd_bits = []
    if att and att != "—":
        crowd_bits.append(f"attendance {att}")
    if first_pitch and first_pitch != "—":
        crowd_bits.append(f"first pitch {first_pitch}")
    if dur and dur != "—":
        crowd_bits.append(f"game time {dur}")
    if crowd_bits:
        atmosphere_rows.append(f"- **Crowd.** " + " · ".join(crowd_bits))

    # Standouts from pitches.csv + plays
    longest_hr = None
    hardest_hit = None
    fastest_pitch = None
    hrs_list: list[tuple[str, str, int]] = []
    play_by_idx = {int(p["idx"]): p for p in plays}
    for pl in plays:
        if pl.get("event") == "Home Run":
            batter = last_name(pl["batter"])
            inn = int(pl["inning"])
            team_s = away_short if pl["half"] == "top" else home_short
            hrs_list.append((batter, team_s, inn))
    for pi in pitches:
        play = play_by_idx.get(int(pi.get("play_idx") or -1))
        if not play:
            continue
        batter = last_name(play["batter"])
        pitcher = last_name(play["pitcher"])
        evt = play.get("event") or ""
        inn = int(play["inning"])
        half = play["half"]
        dist = pi.get("distance_ft")
        ev = pi.get("ev_mph")
        sp = pi.get("speed_mph")
        ptype = pi.get("type_desc") or ""
        if dist not in (None, "") and evt == "Home Run":
            d_ = float(dist)
            if not longest_hr or d_ > longest_hr[0]:
                longest_hr = (d_, batter, inn, half)
        if ev not in (None, ""):
            e_ = float(ev)
            if not hardest_hit or e_ > hardest_hit[0]:
                hardest_hit = (e_, batter, evt or "Ball in play")
        if sp not in (None, ""):
            s_ = float(sp)
            if not fastest_pitch or s_ > fastest_pitch[0]:
                fastest_pitch = (s_, pitcher, ptype, batter)

    standouts: list[str] = []
    if longest_hr:
        d_, batter, inn, half = longest_hr
        team_s = away_short if half == "top" else home_short
        standouts.append(f"Longest HR: **{batter}** ({team_s}), {int(d_)} ft in the {ord_abbr(inn)}")
    if hardest_hit:
        e_, batter, evt = hardest_hit
        standouts.append(f"Hardest-hit ball: **{batter}**, {e_:.1f} mph ({evt})")
    if fastest_pitch:
        sp, pitcher, ptype, _ = fastest_pitch
        ptype_s = f" {ptype}" if ptype else ""
        standouts.append(f"Fastest pitch: **{pitcher}**, {sp:.1f} mph{ptype_s}")
    if not standouts and hrs_list:
        standouts.append("HRs: " + "; ".join(f"**{b}** ({t}), {ord_abbr(i)}" for b, t, i in hrs_list))
    if standouts:
        label = "Statcast standouts" if (longest_hr or hardest_hit or fastest_pitch) else "Notables"
        atmosphere_rows.append(f"- **{label}.** " + " · ".join(standouts) + ".")

    atmosphere_block = "\n".join(atmosphere_rows)

    # --- Moment of the game ---
    moment_idx = g.get("captivating_play_idx")
    moment_block = "_—_"
    if moment_idx not in (None, ""):
        pl = play_by_idx.get(int(moment_idx))
        if pl:
            ci = int(pl.get("captivating_index") or 0)
            inn = int(pl["inning"])
            half = pl["half"]
            half_word = "top" if half == "top" else "bottom"
            batter = last_name(pl["batter"])
            pitcher = last_name(pl["pitcher"])
            desc = (pl.get("description") or "").strip()
            b = int(pl.get("balls") or 0)
            s_ = int(pl.get("strikes") or 0)
            sc = None
            play_pitches = [p for p in pitches if int(p.get("play_idx") or -1) == int(pl["idx"])]
            for pi in play_pitches:
                if pi.get("ev_mph") not in (None, ""):
                    parts = [f"{float(pi['ev_mph']):.1f} mph exit velocity"]
                    if pi.get("la_deg") not in (None, ""):
                        parts.append(f"{float(pi['la_deg']):.0f}° launch angle")
                    if pi.get("distance_ft") not in (None, ""):
                        parts.append(f"{int(float(pi['distance_ft']))} ft")
                    sc = ", ".join(parts)
            moment_block = (
                f"> _\u201c{desc}\u201d_\n>\n"
                f"> — **{batter}** vs. **{pitcher}**, {half_word} of the {ord_abbr(inn)}, {b}-{s_} count."
            )
            if sc:
                moment_block += f" {sc}."
            moment_block += f" *(MLB captivating index: {ci}/100.)*"

    # --- Attended banner + personal notes ---
    attended = bool(g.get("attended"))
    seat_line = ""
    companion_line = ""
    if attended:
        seat_parts = []
        if g.get("seat_section"):
            seat_parts.append(f"Section **{g['seat_section']}**")
        if g.get("seat_row"):
            seat_parts.append(f"Row **{g['seat_row']}**")
        if g.get("seat_number"):
            seat_parts.append(f"Seat **{g['seat_number']}**")
        if seat_parts:
            seat_line = " · ".join(seat_parts)
        if g.get("companions"):
            companion_line = "With: " + str(g["companions"])

    attended_banner = ""
    if attended:
        bits = ["🎟 *Attended.*"]
        if seat_line:
            bits.append(seat_line)
        if companion_line:
            bits.append(companion_line)
        attended_banner = "\n\n> " + " · ".join(bits) + "\n"

    personal_notes_block = ""
    if attended:
        personal_notes_block = (
            "## PERSONAL NOTES\n\n"
            "<!-- Add your memories, impressions, and anything the box score can't capture. -->\n\n"
            "_\u2014_\n\n"
        )

    # --- How it happened ---
    w_side_key = "home" if at_home else "away"
    scoring_innings = []
    for inn in inning_rows:
        r = int((inn.get(f"{w_side_key}_runs")) or 0)
        if r > 0:
            scoring_innings.append((int(inn["inning"]), r))
    if scoring_innings:
        parts = [f"{r} in the {ord_abbr(n)}" for n, r in scoring_innings]
        if len(parts) > 1:
            how_it_happened = f"{winner_short} scored " + ", ".join(parts[:-1]) + f", and {parts[-1]}."
        else:
            how_it_happened = f"{winner_short} scored {parts[0]}."
    else:
        how_it_happened = ""

    # --- Notes block ---
    notes_lines: list[str] = []
    notable_json = g.get("notable_events_json")
    if notable_json:
        try:
            notable = json.loads(notable_json) if isinstance(notable_json, str) else notable_json
            for k in NOTABLE_KEYS:
                if notable.get(k):
                    notes_lines.append(f"- **{k}:** {notable[k]}")
        except (ValueError, TypeError):
            pass

    # --- Frontmatter ---
    frontmatter = (
        "---\n"
        f"date: {game_date_iso}\n"
        f"gamePk: {g.get('gamePk','')}\n"
        f"venue: {venue}\n"
        f"away_team: {away_name}\n"
        f"home_team: {home_name}\n"
        f"away_team_short: {away_short}\n"
        f"home_team_short: {home_short}\n"
        f"final_away: {away_r}\n"
        f"final_home: {home_r}\n"
        f"attended: {'true' if attended else 'false'}\n"
        + (f"seat: {seat_line.replace('**','')}\n" if seat_line else "")
        + "---\n"
    )

    md = f"""{frontmatter}
# {headline}
{subhead}
{attended_banner}
{lede_para}

---

## AT A GLANCE

{at_glance}

## KEY TAKEAWAYS

{takeaways_block}

## ATMOSPHERE

{atmosphere_block}

## ★ MOMENT OF THE GAME

{moment_block}

## HOW IT HAPPENED

{how_it_happened}

## SCORING

{scoring_block}

## LINE SCORE

{line_table}

## BOX SCORE — BATTING

**{away_name}**

{bat_table(away_short)}

**{home_name}**

{bat_table(home_short)}

## BOX SCORE — PITCHING

**{away_name}**

{pit_table(away_short)}

**{home_name}**

{pit_table(home_short)}

## NOTES

{chr(10).join(notes_lines) if notes_lines else '_No notable events logged._'}

## PLAY-BY-PLAY

{game_log}

{personal_notes_block}---

*Source: MLB Stats API, gamePk {g.get('gamePk','')}.*
"""
    out_meta = {
        "date": game_date_iso,
        "away_slug": slugify(away_short),
        "home_slug": slugify(home_short),
    }
    return md, out_meta


# --- HTML + INDEX (shared between report + render) -------------------------

def render_html(md_path: Path, body_class: str = ""):
    import shutil
    import subprocess
    import sys
    if not shutil.which("pandoc"):
        print("pandoc not found; skipping HTML render. `brew install pandoc` to enable.", file=sys.stderr)
        return None
    html_path = md_path.with_suffix(".html")
    css = CSS_FILE.read_text() if CSS_FILE.exists() else ""
    cmd = [
        "pandoc",
        "--from=gfm+yaml_metadata_block+smart",
        "--to=html5",
        "--standalone",
        "--wrap=preserve",
        "--variable=document-css=false",
        "--metadata", "title= ",
        "--metadata", "lang=en",
        str(md_path),
        "-o", str(html_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"pandoc failed: {e.stderr}", file=sys.stderr)
        return None
    html = html_path.read_text()
    html = re.sub(
        r"<style>\s*/\*\s*Default styles provided by pandoc\..*?</style>",
        "", html, count=1, flags=re.DOTALL,
    )
    html = re.sub(r'<h1 class="title">[^<]*</h1>\s*', "", html, count=1)
    style_block = f"<style>\n{css}\n</style>"
    html = html.replace("</head>", f"{style_block}\n</head>", 1)
    if body_class:
        html = html.replace("<body>", f'<body class="{body_class}">', 1)
    html_path.write_text(html)
    return html_path


def rebuild_index(library: Path) -> None:
    entries: list[dict] = []
    for p in sorted(library.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        txt = p.read_text().splitlines()
        if not txt or txt[0].strip() != "---":
            continue
        meta: dict[str, str] = {}
        for ln in txt[1:]:
            if ln.strip() == "---":
                break
            if ":" in ln:
                k, v = ln.split(":", 1)
                meta[k.strip()] = v.strip()
        if meta:
            meta["_file"] = p.name
            entries.append(meta)
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    lines = ["# Games Attended", "",
             f"*{len(entries)} game{'s' if len(entries) != 1 else ''} in the log.*", "",
             "| Date | Matchup | Final | Venue | Notes |",
             "|------|---------|-------|-------|-------|"]
    for e in entries:
        matchup = f"{e.get('away_team','?')} at {e.get('home_team','?')}"
        final = f"{e.get('home_team_short','?')} {e.get('final_home','?')}, {e.get('away_team_short','?')} {e.get('final_away','?')}"
        notes = e.get("seat", "")
        html_sibling = (library / e["_file"]).with_suffix(".html")
        link_target = html_sibling.name if html_sibling.exists() else e["_file"]
        lines.append(f"| {e.get('date','')} | [{matchup}]({link_target}) | {final} | {e.get('venue','')} | {notes} |")
    idx_md = library / "INDEX.md"
    idx_md.write_text("\n".join(lines) + "\n")
    if any((library / e["_file"]).with_suffix(".html").exists() for e in entries):
        render_html(idx_md, body_class="index")
