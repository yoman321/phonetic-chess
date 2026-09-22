<!-- role: Build | model: gpt-5.6-sol | base: f3980c5998ba3954d5af77c2cd3967a87c89070c | date: 2026-09-21 -->

# Measure notation rescues after rollout

Run this read-only query later. Set `rollout_at` to the feature rollout time.
A local empty result is not a rescue-rate measurement.

```sql
\set rollout_at '2026-09-21 00:00:00+00'

WITH params AS (
    SELECT U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000'
        AS python_strip_chars
), accepted AS (
    SELECT
        a.raw_content::jsonb ->> 'uci' AS raw_uci,
        c.chosen_uci
    FROM llm_calls AS c
    JOIN llm_call_attempts AS a ON a.call_id = c.id
    WHERE c.outcome = 'ok'
      AND a.outcome = 'ok'
      AND c.created_at >= :'rollout_at'::timestamptz
), counts AS (
    SELECT
        count(*) AS accepted_moves,
        count(*) FILTER (
            WHERE btrim(raw_uci, python_strip_chars)
                IS DISTINCT FROM chosen_uci
        ) AS rescued_moves
    FROM accepted CROSS JOIN params
)
SELECT
    accepted_moves,
    rescued_moves,
    rescued_moves::numeric / NULLIF(accepted_moves, 0) AS rescue_rate
FROM counts;
```

The comparison trims the same surrounding Unicode whitespace as Python
`str.strip()`. That leaves SAN, annotation marks, uppercase promotion pieces,
and alternate castling UCI counted as rescues. Whitespace alone was already
accepted before this feature.

The logs keep successful committed moves only. They do not keep calls that
failed all three attempts. The true failure rate before this change cannot be
recovered from these tables.
