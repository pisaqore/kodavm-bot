# kodavm-bot

Hourly job: YouTube stats -> `data/stats.json`, `data/history.json`, `data/mode.json`.
The site reads these files (raw.githubusercontent.com sends CORS headers).

## Setup
1. Repo -> Settings -> Secrets and variables -> Actions
   - Secrets: `YT_API_KEY`, `CHANNEL_ID`
   - Variables: `LAUNCH_DATE` (YYYY-MM-DD), `PAUSE` (`false`; `true` = kill switch)
2. Actions tab -> hourly-stats -> Run workflow (first test).
3. Quota: ~3 units per run, 72 units/day.

## Notes
- Public subscriber counts are rounded by YouTube (3 significant digits).
- "First 48h" is approximated by videos at least 2 days old.
- experiments.json / decisions.json arrive with the video bot (next step).
