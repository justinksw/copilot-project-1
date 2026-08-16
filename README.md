# Nexus Watch

A dependency-free League of Legends esports dashboard for schedules, live matches, and results.

## Run locally

Open `index.html` in a browser, or serve the folder with any static server:

```bash
python3 server.py
```

Times are rendered in Hong Kong time (`Asia/Hong_Kong`, UTC+8). The dashboard uses a local proxy that requests Leaguepedia's rendered `parse&prop=text` output and extracts the same `matchlist-content-wrapper` rows shown on the LCK tournament page. The proxy caches results for 10 minutes, avoiding browser CORS and Cargo API rate limits.

Leaguepedia is a community-maintained source and is useful for historical context and match links; it should not be treated as the sole source for time-sensitive live scores.

The verified LCK entries for August 16, 2026 are sourced from [LCK 2026 Rounds 3-4](https://lol.fandom.com/wiki/LCK/2026_Season/Rounds_3-4): T1 0–2 Gen.G and KRX 1–2 BRO.

## Data model

The refresh flow follows the useful parts of `AndyDanger/live-lol-esports`:

- The current LCK season index is discovered automatically from the Hong Kong calendar year, with a previous-season fallback until the new Leaguepedia season page is published. The active stage is selected as the earliest stage with an upcoming match. All discovered stage pages are loaded so completed rounds remain available as history. The rendered match list includes scores, times, teams, team logos, best-of, and the source page. Logo requests are served through the local proxy so the browser does not depend on direct Fandom CDN access.
- Official LCK team logos are stored locally in `assets/team-logos/` at higher resolution; Leaguepedia logo URLs remain a fallback for teams not yet included locally.
- Clicking a match card loads the linked official game IDs and expands per-game champion, player, KDA, gold, creep score, and item details. The panel collapses when the card is clicked again.
- Finished game panels identify the winning team from the final game snapshot and highlight it in the summary.
- Detail loading skips unplayed games, probes the latest completed snapshot first, and fetches champion/item and live-stat feeds concurrently.
- Each request loads a 29-day Hong Kong-centered window, so moving one or more days backward or forward can use the same response and cache.
- Leaguepedia's LCK tournament page uses `AutoMatches` to render its match rows; the proxy extracts those same rows.
- Up to 500 normalized matches are cached in `localStorage`, allowing previously loaded results to remain available as historical data.
- A production version should replace browser storage with a server database. The detail panel fetches a snapshot on demand; continuous live polling is intentionally out of scope for now.

Automatic progression covers the discovered LCK stage pages for the current season. When the calendar year changes, the server checks the new `LCK/{year}_Season` page and updates the match history, active stage, and hero subtitle as soon as Leaguepedia publishes it.