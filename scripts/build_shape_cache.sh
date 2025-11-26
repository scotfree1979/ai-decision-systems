#!/bin/bash
# ───────────────────────────────────────────────
# Smart local cache builder for v7_shape_summary
# Builds v7_shape_summary_cache inside data/autoscalp_gui.db
# Always re-links canonical view for downstream scripts
# ───────────────────────────────────────────────

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${ROOT}/data/autoscalp_gui.db"

MIN_FREE_GB=40     # stop if less than this
SAFE_FREE_GB=80    # full build threshold
SAMPLE_FRACTION=5  # default sample percentage

echo "📦 Building v7_shape_summary_cache → $DB"
FREE_GB=$(df -g . | tail -1 | awk '{print $4}')
echo "💾 Free space: ${FREE_GB} GB"

# build cache
if (( FREE_GB < MIN_FREE_GB )); then
  echo "❌ Not enough free space (<${MIN_FREE_GB} GB). Aborting."
  exit 1
elif (( FREE_GB < SAFE_FREE_GB )); then
  echo "⚠️ Low space — running ${SAMPLE_FRACTION}% SAMPLE cache build"
  FRACTION=$SAMPLE_FRACTION
  sqlite3 "$DB" <<SQL
  DROP TABLE IF EXISTS v7_shape_summary_cache;
  CREATE TABLE v7_shape_summary_cache AS
  SELECT * FROM v7_shape_summary
  WHERE ABS(RANDOM() % (100 / ${FRACTION})) = 0;
  CREATE INDEX IF NOT EXISTS idx_shape_midsid
    ON v7_shape_summary_cache(marketId, selectionId);
SQL
else
  echo "✅ Plenty of space — building FULL cache"
  sqlite3 "$DB" <<'SQL'
  DROP TABLE IF EXISTS v7_shape_summary_cache;
  CREATE TABLE v7_shape_summary_cache AS
  SELECT * FROM v7_shape_summary;
  CREATE INDEX IF NOT EXISTS idx_shape_midsid
    ON v7_shape_summary_cache(marketId, selectionId);
SQL
fi

# verification summary
echo "🧾 Verifying cache..."
sqlite3 "$DB" <<'SQL'
.mode column
.headers on
SELECT COUNT(*) AS total_rows FROM v7_shape_summary_cache;
SELECT marketId, COUNT(*) AS rows_per_market
  FROM v7_shape_summary_cache
 GROUP BY marketId
 ORDER BY rows_per_market DESC
 LIMIT 5;
SQL

# ensure canonical view points to cache
echo "🔗 Linking cache back to canonical view v7_shape_summary..."
sqlite3 "$DB" "DROP VIEW IF EXISTS v7_shape_summary; CREATE VIEW v7_shape_summary AS SELECT * FROM v7_shape_summary_cache;"

echo "✅ Cache build and verification complete."
