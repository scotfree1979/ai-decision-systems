#!/bin/bash

set -e

echo "──────────────────────────────────────────────"
echo "  AutoScalp Cloud DB Repair Utility"
echo "  Safe zero-loss recovery using SQLite .clone"
echo "──────────────────────────────────────────────"
echo ""

### --- PATHS ---------------------------------------------------------------

CLOUD_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache"
CLOUD_ROOT_MASTERY="$HOME/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCloud/data"

DBS=(
  "$CLOUD_ROOT/autoscalp_gui_cache.db"
  "$CLOUD_ROOT/bets_cache.db"
  "$CLOUD_ROOT/settlements_cache.db"
  "$CLOUD_ROOT_MASTERY/mastery_cache.db"
)

### --- FUNCTIONS -----------------------------------------------------------

repair_db () {
    local DB="$1"
    local NAME=$(basename "$DB")
    local DIR=$(dirname "$DB")
    local FIXED="${DIR}/${NAME/.db/_fixed.db}"
    local BACKUP="${DIR}/${NAME/.db/_corrupted_backup_$(date +%Y%m%d-%H%M%S).db}"

    echo "▶ Repairing: $NAME"
    echo "   Location: $DB"

    if [ ! -f "$DB" ]; then
        echo "   ⚠️  File not found, skipping."
        return
    fi

    # Attempt clone
    echo "   → Cloning to temporary fixed database..."
    sqlite3 "$DB" ".clone \"$FIXED\"" 2>/tmp/clone_err.log || {
        echo "   ❌ Clone failed! Error logged at /tmp/clone_err.log"
        return
    }

    # Backup corrupted file
    echo "   → Backing up old DB → $BACKUP"
    mv "$DB" "$BACKUP"

    # Replace with repaired clone
    echo "   → Replacing with fixed DB"
    mv "$FIXED" "$DB"

    # Verify final structure
    echo "   → Verifying repaired DB structure..."
    sqlite3 "$DB" "PRAGMA integrity_check;" | grep -q "ok" && {
        echo "   ✅ $NAME repaired successfully."
        echo ""
        return
    }

    echo "   ❌ Integrity check FAILED on repaired file!"
    echo "   Original backup preserved at: $BACKUP"
    echo ""
}

### --- MAIN LOOP -----------------------------------------------------------

echo "Starting repair process…"
echo ""

for DB in "${DBS[@]}"; do
    repair_db "$DB"
done

echo "──────────────────────────────────────────────"
echo "  FINAL REPORT"
echo "──────────────────────────────────────────────"

FAILED=0

for DB in "${DBS[@]}"; do
    if [ -f "$DB" ]; then
        OUT=$(sqlite3 "$DB" "PRAGMA integrity_check;")
        if [[ "$OUT" == "ok" ]]; then
            echo "✔ $(basename "$DB") — OK"
        else
            echo "✖ $(basename "$DB") — FAILED (corruption remains!)"
            FAILED=1
        fi
    else
        echo "✖ $(basename "$DB") — NOT FOUND"
        FAILED=1
    fi
done

echo "──────────────────────────────────────────────"

if [ $FAILED -eq 0 ]; then
    echo "🎉 ALL CLOUD DATABASES REPAIRED SUCCESSFULLY."
    echo "   No malformed pages remain."
else
    echo "⚠️  Some databases are still corrupted. Check errors above."
fi

echo "──────────────────────────────────────────────"

