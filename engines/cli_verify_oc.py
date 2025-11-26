from sqlite_utils import Database
from rich import print
import os
import json

# Locate the database
db_path = os.path.join("data", "bets.db")
db = Database(db_path)

print("\n[bold green]OC Snapshot Coverage Check[/bold green]")

try:
    # Loop through OC0 to OC7
    for band in range(8):
        oc_key = f"OC{band}"
        count = 0

        # Read each snapshot row and check if the OC band exists inside the JSON
        for row in db["runner_ram_snapshots"].rows:
            try:
                snapshot_json = json.loads(row["snapshot"])
                if oc_key in snapshot_json:
                    count += 1
            except Exception as e:
                print(f"[yellow]⚠️ Error parsing snapshot JSON: {e}[/yellow]")

        status = "[green]✅" if count > 0 else "[red]❌"
        print(f"{oc_key} → {status} ({count} rows found)")
except Exception as e:
    print(f"[red]❌ Failed to query runner_ram_snapshots: {e}[/red]")
