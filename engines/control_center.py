import os
import subprocess
from rich import print

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📦 SIGNAL MEMORY CONTROL CENTER v2
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

menu = {
    "🛠️ MANUAL DEV WORKFLOW": [
        ("1", "[magenta]Check current branch status", "check_current_branch"),
        ("2", "[magenta]Switch to desired branch", "switch_to_branch"),
        ("3", "[magenta]Stage and Commit all changes", "git_commit_all"),
        ("4", "[magenta]Push current branch to GitHub", "git_push_branch"),
        ("5", "[magenta]Create PR from local changes (manual edits)", "create_local_pr"),
        ("6", "[cyan]Open merged PR in browser (manual)", "open_latest_pr_url"),
        ("7", "[cyan]Pull merged Dev branch into local", "pull_merged_dev"),
        ("8", "[blue]Merge dev → main (once tested stable)", "git_merge_dev_to_main"),
        ("9", "[red]Force main = dev (overwrite main with current dev state)", "force_main_to_dev"),
        ("10", "[magenta]Create & push Git tag", "git_create_tag")

    ],

    "🚀 RUN SYSTEM (End of Day)": [
        ("A", "[green]Launch GUI (new terminal)", "launch_gui"),
        ("B", "[green]Run Replay (ask days/epochs, new terminal)", "launch_replay"),
        ("C", "[green]Run Digest (new terminal)", "launch_digest")
    ],

    "🧹 GIT CLEANUP + BACKUPS": [
        ("16", "[white]Show Codex-changed files (last diff)", "git_diff_codex"),
        ("17", "[white]Check repo cleanliness (git status)", "cli_check_git_status"),
        ("18", "[white]Manual backup before tool launch", "create_git_snapshot")
    ],

    "❌ EXIT": [
        ("0", "Exit Control Center", "exit_program")
    ]
}

def launch_simulator():
    command = f'cd "{os.getcwd()}" && python3 simulator.py'
    open_in_new_terminal(command)

def launch_gui_streamlit():
    command = f'cd "{os.getcwd()}" && streamlit run gui_streamlit.py'
    open_in_new_terminal(command)

def launch_strategy_mastery():
    command = f'cd "{os.getcwd()}" && python3 strategy_mastery_engine.py'
    open_in_new_terminal(command)

def launch_live_dashboard():
    command = f'cd "{os.getcwd()}" && python3 live_dashboard.py'
    open_in_new_terminal(command)

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: control_center.py
# 🔎 SEARCH: def launch_live_dashboard():
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

def launch_gui():
    command = f'cd "{os.getcwd()}" && python3 gui/GUI.py'
    open_in_new_terminal(command)

def launch_replay():
    days = input("📅 How many days? ").strip() or "1"
    epochs = input("🔁 How many epochs? ").strip() or "1"
    command = f'cd "{os.getcwd()}" && python3 replay.py --days {days} --epochs {epochs}'
    open_in_new_terminal(command)

def launch_digest():
    command = f'cd "{os.getcwd()}" && python3 replay_digest.py'
    open_in_new_terminal(command)


def check_current_branch():
    branch = subprocess.check_output(["git", "branch", "--show-current"]).decode().strip()
    print(f"\n🌿 Current branch: [bold yellow]{branch}[/bold yellow]")
    if branch == "main":
        print("[red]⚠️ You are on main. Switch to dev before committing.")
    elif branch == "dev":
        print("[green]✅ You are on the correct branch (dev).")
    else:
        print("[magenta]Note: You are on a custom branch.")

def switch_to_branch():
    # Check for uncommitted changes
    result = subprocess.run(["git", "diff-index", "--quiet", "HEAD", "--"])
    if result.returncode != 0:
        print("\n[red]⚠️ You have uncommitted changes.")
        action = input("Would you like to [s]tash, [c]ommit, or [a]bort? ").strip().lower()
        if action == "s":
            subprocess.run(["git", "stash"])
            print("[yellow]💾 Changes stashed.")
        elif action == "c":
            git_commit_all()
            print("[green]✅ Changes committed.")
        elif action == "a":
            print("[red]❌ Branch switch aborted.")
            return

    branch = input("🔀 Enter the branch name to switch to: ").strip()
    if branch:
        result = subprocess.run(["git", "checkout", branch])
        if result.returncode == 0:
            print(f"[green]✅ Switched to branch: {branch}")
        else:
            print("[red]❌ Failed to switch branches.")

def git_commit_all():
    branch = subprocess.check_output(["git", "branch", "--show-current"]).decode().strip()
    if branch == "main":
        print("[red]❌ You are currently on [bold]main[/bold]. Please switch to [bold]dev[/bold] before committing.")
        return

    subprocess.run(["git", "add", "."])
    subprocess.run(["git", "commit", "-m", "Automated commit via Control Center"])


# NOTE: Remaining unchanged functions follow...
# (Already present in your canvas: launch_run_tool, launch_blueprint, etc.)

# NOTE: The rest of your logic remains exactly as it is from your previous canvas. Only `git_commit_all()` has been replaced to enforce the dev-branch workflow.

# 🧠 Add any future branch protections inside this function too.
# ✅ NEW FUNCTION: Open PR in browser if available
def open_latest_pr_url():
    result = subprocess.run(["gh", "pr", "view", "--json", "url", "-q", ".url"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        url = result.stdout.strip()
        print(f"[blue]Opening PR in browser: {url}")
        subprocess.run(["open", url])  # macOS only; replace with "xdg-open" for Linux
    else:
        print("[red]Could not retrieve latest PR URL. Make sure a PR exists.")

# ✅ Force overwrite main branch to match dev

def force_main_to_dev():
    print("\n[red]⚠️ This will hard-reset main to match dev. Proceed with caution!\n")
    confirm = input("Type 'confirm' to proceed: ").strip().lower()
    if confirm != "confirm":
        print("[yellow]Cancelled.")
        return

    try:
        subprocess.run(["git", "checkout", "main"])  # switch to main
        subprocess.run(["git", "reset", "--hard", "dev"])  # reset main to match dev
        subprocess.run(["git", "push", "origin", "main", "--force"])  # force push to remote
        print("[green]✅ main has been forcefully synced with dev.")
    except Exception as e:
        print(f"[red]❌ Failed to force sync main: {e}")
def clear_screen():
    os.system('clear')

def open_in_new_terminal(command):
    applescript = f'''
    tell application "Terminal"
        do script "{command}"
        activate
    end tell
    '''
    subprocess.run(["osascript", "-e", applescript])

def has_uncommitted_changes():
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    return bool(result.stdout.strip())


def sync_dev_branch():
    """Sync the local dev branch with origin/dev and finalize any pending merges."""
    # Ensure we're on the dev branch
    try:
        current_branch = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
    except subprocess.CalledProcessError as e:
        print(f"[red]Error determining current branch:[/red] {e}")
        return

    if current_branch != "dev":
        result = subprocess.run(["git", "checkout", "dev"], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[red]Failed to checkout dev branch:[/red] {result.stderr.strip()}")
            return
        else:
            print(result.stdout.strip())

    # Complete an in-progress merge if MERGE_HEAD exists
    merge_head = os.path.join(".git", "MERGE_HEAD")
    if os.path.exists(merge_head):
        result = subprocess.run([
            "git",
            "commit",
            "-m",
            "✅ Merge Codex PR into dev",
        ], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[red]Failed to finalize merge:[/red] {result.stderr.strip()}")
            return
        else:
            print("[green]Merge commit completed.[/green]")

    # Pull latest changes from origin/dev
    result = subprocess.run(["git", "pull", "origin", "dev"], capture_output=True, text=True)
    if result.returncode == 0:
        print("[green]Repository successfully synced with origin/dev.[/green]")
    else:
        print(f"[red]Failed to pull from origin/dev:[/red] {result.stderr.strip()}")


def custom_verify_git_structure():
    subprocess.run(["custom_verify_git_structure"])


def launch_run_tool():
    if has_uncommitted_changes():
        print("\n🚨 Uncommitted changes detected. Please commit and push changes before launching the tool.")
        return
    command = f'cd "{os.getcwd()}" && python3 run_tool.py'
    open_in_new_terminal(command)


def launch_blueprint():
    command = f'cd "{os.getcwd()}" && python3 blueprint.py'
    open_in_new_terminal(command)


def launch_pnl_log():
    command = f'cd "{os.getcwd()}" && python3 blueprint_pnl_log.py'
    open_in_new_terminal(command)


def cli_verify_oc():
    subprocess.run(["python3", os.path.join(os.getcwd(), "cli_verify_oc.py")])


def cli_verify_confidence():
    subprocess.run(["python3", os.path.join(os.getcwd(), "cli_verify_confidence.py")])


def cli_verify_blueprints():
    subprocess.run(["python3", os.path.join(os.getcwd(), "cli_verify_blueprints.py")])


def cli_verify_bets():
    subprocess.run(["python3", os.path.join(os.getcwd(), "cli_verify_bets.py")])


def git_commit_all():
    subprocess.run(["git", "add", "."])
    subprocess.run(["git", "commit", "-m", "Automated commit via Control Center"])


def git_push_branch():
    branch = subprocess.check_output(["git", "branch", "--show-current"]).decode().strip()
    subprocess.run(["git", "push", "origin", branch])


def git_merge_dev_to_main():
    subprocess.run(["git", "checkout", "main"])
    subprocess.run(["git", "merge", "dev"])


def create_git_snapshot():
    subprocess.run(["git", "add", "."])
    subprocess.run(["git", "commit", "-m", "🧱 Manual backup before tool launch"])


def git_diff_codex():
    subprocess.run(["git", "diff"])


def create_pull_request():
    """Open a PR from the latest codex/* branch to dev."""
    # Detect latest codex branch
    result = subprocess.run(
        ["git", "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads/codex/*"],
        capture_output=True, text=True
    )
    branch = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
    if not branch:
        print("[red]No codex branches found.[/red]")
        return

    has_gh = subprocess.run(["which", "gh"], capture_output=True).returncode == 0
    if has_gh:
        subprocess.run(["gh", "pr", "create", "--fill", "--base", "dev", "--head", branch])
    else:
        print("[yellow]GitHub CLI not installed. Please open a PR from branch '", branch, "' to 'dev' manually.[/yellow]", sep="")

# 🛠️ Add this function anywhere in your code (bottom is fine):
def create_local_pr():
    """Create a GitHub PR from current local dev branch."""
    has_gh = subprocess.run(["which", "gh"], capture_output=True).returncode == 0
    if not has_gh:
        print("[red]GitHub CLI not installed. Please install it to proceed.[/red]")
        return

    # Ensure dev branch
    branch = subprocess.check_output(["git", "branch", "--show-current"]).decode().strip()
    if branch != "dev":
        print(f"[red]You're not on 'dev' (current: {branch}). Please switch to dev.")
        return

    # Create new PR
    print("[blue]Creating pull request from local dev branch...")
    result = subprocess.run([
        "gh", "pr", "create", "--base", "dev", "--head", "dev", "--title", "Manual PR from dev", "--body", "Manual code edits – ready to merge."
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[red]PR creation failed:[/red] {result.stderr.strip()}")
    else:
        print("[green]✅ PR created successfully.[/green]")
        print(result.stdout.strip())

# 🧪 That's it. Now selecting 19 from the control panel will launch a PR from dev to dev.
# (Sounds odd, but GitHub accepts it as long as changes are pushed.)

# You can follow it with:
#   [17] to merge the PR
#   [18] to pull the new code locally

# ✅ Fully streamlined manual edit workflow
# → No need for Codex – just paste, edit, push, and run!



def merge_pull_request():
    """Merge the most recent open Codex PR into dev."""
    has_gh = subprocess.run(["which", "gh"], capture_output=True).returncode == 0
    if not has_gh:
        print("[red]GitHub CLI not installed. Please merge the PR manually on GitHub.[/red]")
        return

    pr_list = subprocess.run([
        "gh", "pr", "list", "--state", "open", "--search", "head:codex/ base:dev", "--sort", "created", "--limit", "1"
    ], capture_output=True, text=True)

    if pr_list.returncode != 0 or not pr_list.stdout.strip():
        print("[yellow]No open Codex PRs found.[/yellow]")
        return

    first_line = pr_list.stdout.strip().splitlines()[0]
    pr_number = first_line.split()[0]

    merge = subprocess.run(["gh", "pr", "merge", pr_number, "--merge", "--yes"], capture_output=True, text=True)
    print(merge.stdout.strip())
    if merge.returncode != 0:
        print(f"[red]Failed to merge PR #{pr_number}:[/red] {merge.stderr.strip()}")
    else:
        print(f"[green]Merged PR #{pr_number} successfully.[/green]")


def pull_merged_dev():
    """Pull the dev branch and finalize any pending merges."""
    result = subprocess.run(["git", "pull", "origin", "dev"], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print(f"[red]Failed to pull from origin/dev:[/red] {result.stderr.strip()}")
        return

    merge_head = os.path.join(".git", "MERGE_HEAD")
    if os.path.exists(merge_head):
        env = os.environ.copy()
        env["EDITOR"] = "nano"
        commit = subprocess.run(["git", "commit", "-m", "✅ Merge Codex changes from GitHub"], capture_output=True, text=True, env=env)
        if commit.returncode != 0:
            print(f"[red]Failed to finalize merge:[/red] {commit.stderr.strip()}")
        else:
            print("[green]Merge commit completed.[/green]")
def git_create_tag():
    """
    Create an annotated tag on the current HEAD and push it to GitHub.
    Flow:
      1) Ask for tag name (e.g. mastery-setup-2025-08-24)
      2) Optional tag message (defaults to name)
      3) Create tag locally and push it to origin
    """
    # Optional: show branch (just informative)
    try:
        branch = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
        print(f"\n🌿 Current branch: [bold yellow]{branch}[/bold yellow]")
    except Exception:
        branch = None

    tag = input("🧷 Enter tag name (e.g., mastery-setup-2025-08-24): ").strip()
    if not tag:
        print("[red]❌ Tag creation cancelled (empty name).")
        return

    # Basic validation (no whitespace, no leading refs/)
    if any(ch.isspace() for ch in tag) or tag.startswith("refs/"):
        print("[red]❌ Invalid tag name. Avoid spaces and 'refs/'.")
        return

    # Check if tag already exists
    exists = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                            capture_output=True, text=True)
    if exists.returncode == 0:
        print(f"[red]❌ Tag '[bold]{tag}[/bold]' already exists. Choose a different name.")
        return

    msg = input("📝 Enter tag message (optional, press Enter to reuse the name): ").strip() or tag

    # Create annotated tag
    make = subprocess.run(["git", "tag", "-a", tag, "-m", msg], capture_output=True, text=True)
    if make.returncode != 0:
        print(f"[red]❌ Failed to create tag:[/red] {make.stderr.strip()}")
        return

    # Push the single tag
    push = subprocess.run(["git", "push", "origin", tag], capture_output=True, text=True)
    if push.returncode != 0:
        print(f"[red]❌ Failed to push tag to origin:[/red] {push.stderr.strip()}")
        # Revert local tag if push failed, to avoid local-only surprises
        subprocess.run(["git", "tag", "-d", tag], capture_output=True, text=True)
        print("[yellow]🧹 Local tag deleted due to push failure.")
        return

    print(f"[green]✅ Tag '[bold]{tag}[/bold]' created and pushed to GitHub.[/green]")



def cli_check_git_status():
    subprocess.run(["git", "status"])


def exit_program():
    pass


def display_menu():
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print("📦 SIGNAL MEMORY CONTROL CENTER v2")
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    for section, items in menu.items():
        print(f"\n[bold]{section}[/bold]")
        for number, label, _ in items:
            print(f" {number}. {label}")
    print("")


def run_menu():
    while True:
        clear_screen()
        display_menu()
        choice = input("Enter choice: ").strip()
        selected = None
        for items in menu.values():
            for number, _label, command_name in items:
                if choice == number:
                    selected = command_name
                    break
            if selected:
                break
        if not selected:
            print("Invalid choice. Try again.")
            input("\nPress Enter to return to main menu...")
            continue

        if selected == "exit_program":
            break

        func = globals().get(selected)
        if callable(func):
            func()
        else:
            subprocess.run(selected, shell=True)
        input("\nPress Enter to return to main menu...")

if __name__ == "__main__":
    run_menu()

