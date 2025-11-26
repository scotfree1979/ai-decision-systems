from sqlite_utils import Database
from rich import print
import os

db_path = os.path.join("data", "bets.db")
db = Database(db_path)

print("\n[bold green]Bet Placement Check[/bold green]")

try:
    count = db["bets"].count
    status = "[green]✅" if count > 0 else "[red]❌"
    print(f"Bets Fired → {status} ({count} bets placed)")
except Exception as e:
    print(f"[red]❌ Error accessing bets table: {e}[/red]")
