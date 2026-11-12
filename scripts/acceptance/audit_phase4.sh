#!/usr/bin/env bash
# Phase 4 生产验收只读审计。只执行 PostgreSQL SELECT，不触发任务或修改生产状态。
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "用法：$0 YYYY-MM-DD [YYYY-MM-DD ...]" >&2
  exit 2
fi

dates_sql=""
for day in "$@"; do
  if [[ ! "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "非法日期：$day" >&2
    exit 2
  fi
  [[ -z "$dates_sql" ]] || dates_sql+=", "
  dates_sql+="DATE '$day'"
done

cd /opt/quant
compose=(docker compose -f compose.yml -f compose.server.yml)

echo "Phase 4 read-only audit"
echo "dates: $*"
echo
echo "[scheduled jobs and attempts]"
"${compose[@]}" exec -T postgres psql -X -U quant -d quant -P pager=off -c "
WITH
  days(trade_date) AS (SELECT unnest(ARRAY[$dates_sql]::date[])),
  expected(job_name) AS (
    VALUES ('daily_execute'), ('daily_report'), ('daily_data'), ('daily_signal'),
           ('market_extras'), ('stock_data'), ('market_data')
  ),
  attempts AS (
    SELECT mode, job_name, trade_date, count(*) AS count,
           string_agg(attempt || ':' || result, ', ' ORDER BY attempt) AS detail
    FROM job_attempts
    WHERE mode = 'paper' AND trade_date IN ($dates_sql)
    GROUP BY mode, job_name, trade_date
  )
SELECT d.trade_date,
       e.job_name,
       COALESCE(r.status, 'MISSING') AS status,
       COALESCE(a.count, 0) AS attempts,
       COALESCE(a.detail, '') AS attempt_detail,
       to_char(r.started_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI:SS') AS started_et,
       to_char(r.finished_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI:SS') AS finished_et,
       COALESCE(r.error, '') AS error
FROM days d
CROSS JOIN expected e
LEFT JOIN job_runs r
  ON r.mode = 'paper' AND r.trade_date = d.trade_date AND r.job_name = e.job_name
LEFT JOIN attempts a
  ON a.mode = r.mode AND a.trade_date = r.trade_date AND a.job_name = r.job_name
ORDER BY d.trade_date, e.job_name;" < /dev/null

echo
echo "[unexpected paper jobs]"
"${compose[@]}" exec -T postgres psql -X -U quant -d quant -P pager=off -c "
SELECT trade_date, job_name, status,
       to_char(started_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI:SS') AS started_et,
       COALESCE(error, '') AS error
FROM job_runs
WHERE mode = 'paper'
  AND trade_date IN ($dates_sql)
  AND job_name NOT IN (
    'daily_execute', 'daily_report', 'daily_data', 'daily_signal',
    'market_extras', 'stock_data', 'market_data'
  )
ORDER BY trade_date, job_name;" < /dev/null

echo
echo "[container evidence by ET calendar day]"
"${compose[@]}" exec -T postgres psql -X -U quant -d quant -P pager=off -c "
SELECT (ts AT TIME ZONE 'America/New_York')::date AS day_et,
       name,
       count(*) AS samples,
       min(COALESCE((extra->>'restarts')::int, 0)) AS min_restarts,
       max(COALESCE((extra->>'restarts')::int, 0)) AS max_restarts,
       count(DISTINCT NULLIF(extra->>'started_at', '')) AS distinct_starts,
       string_agg(DISTINCT COALESCE(extra->>'state', 'unknown'), ', ' ORDER BY COALESCE(extra->>'state', 'unknown')) AS states
FROM system_metrics
WHERE kind = 'container'
  AND (ts AT TIME ZONE 'America/New_York')::date IN ($dates_sql)
GROUP BY day_et, name
ORDER BY day_et, name;" < /dev/null

echo
echo "[metric coverage]"
"${compose[@]}" exec -T postgres psql -X -U quant -d quant -P pager=off -c "
SELECT (ts AT TIME ZONE 'America/New_York')::date AS day_et,
       kind,
       count(*) AS samples,
       min(ts AT TIME ZONE 'America/New_York') AS first_et,
       max(ts AT TIME ZONE 'America/New_York') AS last_et
FROM system_metrics
WHERE (ts AT TIME ZONE 'America/New_York')::date IN ($dates_sql)
GROUP BY day_et, kind
ORDER BY day_et, kind;" < /dev/null
