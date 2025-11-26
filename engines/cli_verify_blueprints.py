from sqlite_utils import Database
from rich import print
import os

db_path = os.path.join("data", "bets.db")
db = Database(db_path)

print("\n[bold green]Blueprint Match Check[/bold green]")

try:
    count = db["blueprint_pnl_log"].count
    status = "[green]✅" if count > 0 else "[red]❌"
    print(f"Blueprint Matches → {status} ({count} entries)")
except Exception as e:
    print(f"[red]❌ Error accessing blueprint_pnl_log table: {e}[/red]")
