from sqlite_utils import Database
from rich import print
import os

db_path = os.path.join("data", "bets.db")
db = Database(db_path)

print("\n[bold green]Confidence Scores Check[/bold green]")

try:
    count = db["confidence_scores"].count
    status = "[green]✅" if count > 0 else "[red]❌"
    print(f"Confidence Scores → {status} ({count} rows)")
except Exception as e:
    print(f"[red]❌ Error accessing confidence_scores table: {e}[/red]")
