Command: echo "Reply with exactly the word OK and nothing else." | claude -p \
  --model claude-sonnet-4-6 --tools "" --setting-sources "" \
  --no-session-persistence --output-format json > haiku_probe_raw.json
Purpose: record per-model token attribution for a minimal call whose entire
reply is "OK", to characterise auxiliary Haiku usage in CLI records.
Limitation: this probe shows a usage PATTERN consistent with fixed
CLI-internal auxiliary activity; the CLI's internal routing could not be
independently inspected.
