#!/usr/bin/env python3
import os, sys, subprocess

MENU = {
    "1": {
        "title": "Analytics",
        "subs": {
            "a": "Canonical Analytics Report",

        },
    },
    "2": {
        "title": "Learning",
        "subs": {
            "a": "Run Replay",
            "b": "Replay Digest",
            "c": "Replay Report → Raw loader",
            "d": "Consolidate to Posteriors",
            "e": "Reconcile Settlements”",
        },
    },
    "3": {"title": "Execution Monitor [planned]", "subs": {}},

    "4": {"title": "Data Health [planned]", "subs": {}},
    "5": {"title": "Strategy Sandbox [planned]", "subs": {}},
    "6": {"title": "Reports & Exports [planned]", "subs": {}},
    "7": {"title": "System Tools [planned]", "subs": {}},
}


def show_menu():
    RESET = "\033[0m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    ORANGE = "\033[38;5;208m"
    GREY = "\033[90m"
    UNDER = "\033[4m"

    print("\n=== Trading Hub ===")

    # Analytics section
    print(f"{BLUE}{UNDER}1. Analytics{RESET}")
    print("   a. Canonical Analytics Report")

    print("──────────────────────────────────────────────")

    # Learning section
    print(f"{GREEN}{UNDER}2. Learning{RESET}")
    print("   a. Run Replay")
    print("   b. Replay Digest")
    print("   c. Replay Report → Raw loader")
    print("   d. Consolidate to Posteriors")
    print("   e. Reconcile Settlements")
    print("   f. Train ML Model (Playbooks)")
    print("   g. Build Playbooks Dataset")
    print("   h. Smart Mastery Reinforcement")
    print("   i. Smart Mastery Training")
    print("──────────────────────────────────────────────")

    # Execution Monitor
    print(f"{ORANGE}{UNDER}3. Learning Execution Monitor{RESET}")
    print("   a. Full Learning Run (Replay → Digest → ML → Consolidate)")
    print("   b. Digest → ML → Consolidate Only")
    print("   c. Resume Replay (from specific day)")


    # Future sections (3–7)

    print(f"{GREY}4. Data Health [planned]{RESET}")
    print(f"{GREY}5. Strategy Sandbox [planned]{RESET}")
    print(f"{GREY}6. Reports & Exports [planned]{RESET}")
    print(f"{GREEN}{UNDER}7. System Tools{RESET}")
    print("   a. Run DB Repair (AutoScalp DB Doctor)")
    print("   b. Sprint")
    print("──────────────────────────────────────────────")


    # Quit
    print(" q. Quit")
    print("──────────────────────────────────────────────")



def run_inline(command: str):
    print(f"\n[inline] Running: {command}")
    os.system(command)
    print("[inline] Finished.\n")


def run_new_tab(command: str):
    print(f"\n[new tab] Launching: {command}")
    osa = f'''
    tell application "Terminal"
        activate
        do script "{command}"
    end tell
    '''
    subprocess.run(["osascript", "-e", osa])

import glob

def _latest_replay_json():
    files = glob.glob(os.path.join(PROJECT_DIR, "data/replay_report_*.json"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def _resume_args():
    latest = _latest_replay_json()
    if not latest:
        print("[resume] No replay_report JSON found")
        return None
    # you could parse file name timestamp or open JSON to detect last 'day'
    return ["--resume", latest]

def _osascript_run(cmd: str):
    # Escape backslashes and quotes for AppleScript
    cmd_escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
    osa = f'tell application "Terminal" to do script "{cmd_escaped}"'
    subprocess.run(["osascript", "-e", osa])



import subprocess

PROJECT_DIR = "~/Dev/analytics_beta_dev"   # adjust if your path differs

def handle_choice(choice: str):
    # ─────────────────────── Analytics Section ───────────────────────
    if choice == "1a":  # Canonical Analytics Report
        print("[analytics] Canonical digest report")
        cmd = f"cd {PROJECT_DIR} && python3 analytics_report.py; exec bash"
        _osascript_run(cmd)

    elif choice == "1b":  # 7-day trend
        print("[analytics] 7-day trend")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "sqlite3 data/autoscalp_gui.db "
            "\"SELECT date(updated_at) AS day, "
            "ROUND(SUM(net_pnl),2) AS pnl, COUNT(*) AS rows "
            "FROM mastery_posteriors "
            "WHERE date(updated_at) >= date('now','-7 day','utc') "
            "GROUP BY day ORDER BY day;\"; exec bash"
        )
        _osascript_run(cmd)

    elif choice == "1c":  # 30-day trend
        print("[analytics] 30-day trend")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "sqlite3 data/autoscalp_gui.db "
            "\"SELECT date(updated_at) AS day, "
            "ROUND(SUM(net_pnl),2) AS pnl, COUNT(*) AS rows "
            "FROM mastery_posteriors "
            "WHERE date(updated_at) >= date('now','-30 day','utc') "
            "GROUP BY day ORDER BY day;\"; exec bash"
        )
        _osascript_run(cmd)

    elif choice == "1d":  # Letter performance
        print("[analytics] Letter performance")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "sqlite3 data/autoscalp_gui.db "
            "\"SELECT SUBSTR(bin_key,1,1) AS letter, "
            "ROUND(SUM(net_pnl),2) AS pnl, COUNT(*) AS trades, "
            "ROUND(AVG(weight_applied),3) AS avg_weight "
            "FROM mastery_posteriors "
            "WHERE date(updated_at) >= date('now','-7 day','utc') "
            "GROUP BY letter ORDER BY pnl DESC;\"; exec bash"
        )
        _osascript_run(cmd)

    elif choice == "1e":  # Distance bands / Race types
        print("[analytics] Distance bands / Race types")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "sqlite3 data/autoscalp_gui.db "
            "\"SELECT distance_band, race_type, "
            "ROUND(SUM(net_pnl),2) AS pnl, COUNT(*) AS trades "
            "FROM mastery_posteriors "
            "WHERE date(updated_at) >= date('now','-30 day','utc') "
            "GROUP BY distance_band, race_type ORDER BY pnl DESC;\"; exec bash"
        )
        _osascript_run(cmd)

    elif choice == "1f":  # Confidence trend
        print("[analytics] Confidence trend")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "sqlite3 data/autoscalp_gui.db "
            "\"SELECT date(updated_at) AS day, "
            "ROUND(AVG(weight_applied),4) AS avg_weight, COUNT(*) AS samples "
            "FROM mastery_posteriors "
            "WHERE date(updated_at) >= date('now','-30 day','utc') "
            "GROUP BY day ORDER BY day;\"; exec bash"
        )
        _osascript_run(cmd)

    elif choice == "1g":  # Bias drift
        print("[analytics] Bias drift (surface × race type)")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "sqlite3 data/autoscalp_gui.db "
            "\"SELECT surface_type, race_type, "
            "ROUND(SUM(net_pnl),2) AS pnl, ROUND(AVG(weight_applied),3) AS avg_weight, "
            "COUNT(*) AS n "
            "FROM mastery_posteriors "
            "WHERE date(updated_at) >= date('now','-30 day','utc') "
            "GROUP BY surface_type, race_type ORDER BY pnl DESC;\"; exec bash"
        )
        _osascript_run(cmd)

    elif choice == "1h":  # Trainer / Jockey influence
        print("[analytics] Trainer / Jockey influence")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "sqlite3 data/autoscalp_gui.db "
            "\"SELECT trainer, jockey, ROUND(SUM(net_pnl),2) AS pnl, COUNT(*) AS trades "
            "FROM mastery_posteriors "
            "WHERE date(updated_at) >= date('now','-30 day','utc') "
            "  AND trainer IS NOT NULL "
            "GROUP BY trainer, jockey ORDER BY pnl DESC LIMIT 20;\"; exec bash"
        )
        _osascript_run(cmd)

    elif choice == "1i":  # Distance × Going
        print("[analytics] Distance × Going interaction")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "sqlite3 data/autoscalp_gui.db "
            "\"SELECT distance_band, going, "
            "ROUND(SUM(net_pnl),2) AS pnl, COUNT(*) AS trades "
            "FROM mastery_posteriors "
            "WHERE date(updated_at) >= date('now','-30 day','utc') "
            "GROUP BY distance_band, going ORDER BY pnl DESC LIMIT 20;\"; exec bash"
        )
        _osascript_run(cmd)

    elif choice == "1j":  # Day-of-week pattern
        print("[analytics] Day-of-week pattern")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "sqlite3 data/autoscalp_gui.db "
            "\"SELECT day_of_week, ROUND(SUM(net_pnl),2) AS pnl, "
            "COUNT(*) AS trades, ROUND(AVG(weight_applied),3) AS avg_weight "
            "FROM mastery_posteriors "
            "WHERE date(updated_at) >= date('now','-30 day','utc') "
            "GROUP BY day_of_week ORDER BY pnl DESC;\"; exec bash"
        )
        _osascript_run(cmd)
    # Learning menu
# === PATCH START ===
# 📍 TARGET: TradingHub.py:handle_choice("2a")
# 📆 PATCHED: 2025-10-14Z — interactive replay launcher (days/epochs/start-balance)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif choice == "2a":   # Run Replay (interactive)
        print("[launch] Learning → Run Replay (interactive mode)")

        # ask the user for parameters
        days = input("Enter number of days to replay (e.g. 2): ").strip() or "2"
        epochs = input("Enter number of epochs (e.g. 365): ").strip() or "365"
        start_balance = input("Enter starting balance (e.g. 665): ").strip() or "665"

        cmd = (
            f"cd {PROJECT_DIR} && "
            f"caffeinate -dimsu python3 engines/replay_runner.py --days {days} --epochs {epochs} --start-balance {start_balance}; exec bash"
        )

        osa = f'''
        tell application "Terminal"
            activate
            do script "{cmd}"
        end tell
        '''
        subprocess.run(["osascript", "-e", osa])
# === PATCH END ===


    elif choice == "2b":   # Replay Digest
        print("[launch] Learning → Replay Digest")
        subprocess.run([
            "osascript", "-e",
            f'tell application "Terminal" to do script "cd {PROJECT_DIR} && python3 replay_digest.py; exec bash"'
        ])

    elif choice == "2c":   # Replay Report → Raw loader
        print("[launch] Learning → Replay Report → Raw loader")
        subprocess.run([
            "osascript", "-e",
            f'tell application "Terminal" to do script "cd {PROJECT_DIR} && python3 replay_report_to_posteriors.py; exec bash"'
        ])

    elif choice == "2d":   # Consolidate to Posteriors
        print("[launch] Learning → Consolidate to Posteriors")
        subprocess.run([
            "osascript", "-e",
            f'tell application "Terminal" to do script "cd {PROJECT_DIR} && python3 engines/consolidate_posteriors.py; exec bash"'
        ])
# === PATCH START ===
# 📍 TARGET: TradingHub.py:handle_choice("2e")
# 📆 PATCHED: 2025-10-15Z — interactive Fetch & Reconcile settlements (days prompt)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif choice == "2e":  # Fetch & Reconcile
        print("[launch] Learning → Fetch & Reconcile Settlements")
        try:
            days = input("Enter number of days to fetch & reconcile (e.g. 2): ").strip()
            if not days or not days.isdigit():
                print("[input] Please enter a valid number of days.")
                return
            print(f"[settlements] Fetching and reconciling last {days} day(s)…")

            cmd = (
                f"cd {PROJECT_DIR} && "
                f"python3 engines/live/settlements.py fetch --since-days {days} && "
                f"python3 engines/live/settlements.py reconcile ; exec bash"
            )
            osa = f'''
            tell application "Terminal"
                activate
                do script "{cmd}"
            end tell
            '''
            subprocess.run(["osascript", "-e", osa])
        except KeyboardInterrupt:
            print("\n[abort] User cancelled.")
# === PATCH END ===
# === PATCH START: Learning + Execution Monitor Integration ====================
# 📍 TARGET: engines/control_center.py
# 📆 PATCHED: 2025-10-17T11:00Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # --- Learning additions ----------------------------------------------------
    elif choice == "2f":   # Train ML Model
        print("[launch] Learning → Train ML Model (Playbooks-based)")
        subprocess.run([
            "osascript", "-e",
            f'tell application "Terminal" to do script "cd {PROJECT_DIR} && python3 engines/ml/ml_training_engine.py --days 90; exec bash"'
        ])

    elif choice == "2g":   # Build Playbooks Dataset
        print("[launch] Learning → Build Playbooks Dataset")
        subprocess.run([
            "osascript", "-e",
            f'tell application "Terminal" to do script "cd {PROJECT_DIR} && python3 engines/playbooks/playbooks_builder.py; exec bash"'
        ])

# === PATCH START ===
# 📍 TARGET: TradingHub.py:handle_choice("2h")
# 📆 PATCHED: 2025-10-27Z — 2H Smart Mastery Reinforcement (Feedback Cycle + Train Mastery, with import shim)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif choice == "2h":
        print("[launch] Learning → 2H Smart Mastery Reinforcement (Feedback Cycle + Train Mastery)")

        cmd = (
            f"cd {PROJECT_DIR} && "
            # ensure repo root is importable before running either script
            "export PYTHONPATH=$(pwd):$PYTHONPATH && "
            "echo '\\n=== Step 1: Feedback Cycle (daily reinforcement) ===' && "
            "python3 engines/mastery/feedback_cycle.py && "
            "echo '\\n=== Step 2: Train Mastery Policy (reinforcement learning) ===' && "
            "python3 engines/mastery/train_mastery.py && "
            "echo '\\n=== 2H Smart Mastery Reinforcement complete ✅ ===' ; exec bash"
        )

        osa = f'''
        tell application "Terminal"
            activate
            do script "{cmd}"
        end tell
        '''
        subprocess.run(["osascript", "-e", osa])
# === PATCH END ===
# === PATCH START ===
# 📍 TARGET: TradingHub.py:handle_choice("2i")
# 📆 PATCHED: 2025-10-30Z — Interactive Smart Mastery Training (days/epochs/start-balance)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif choice == "2i":
        print("[launch] Learning → 2I Smart Mastery Training (v7 Simulation Loop)")

        # Ask for interactive parameters
        days = input("Enter number of replay days (e.g. 7): ").strip() or "7"
        epochs = input("Enter number of epochs per day (e.g. 20): ").strip() or "20"
        start_balance = input("Enter starting balance (e.g. 600): ").strip() or "600"

        # Build composite shell command (same pattern as 2a)
        cmd = (
            f"cd {PROJECT_DIR} && "
            "export PYTHONPATH=$(pwd):$PYTHONPATH && "
            "echo '\\n=== Step 1: Consolidate → v_mastery_training View ===' && "
            "python3 engines/consolidate_posteriors.py && "
            "echo '\\n=== Step 2: Train Mastery Policy (Random Forest) ===' && "
            "python3 engines/mastery/train_mastery.py && "
            "echo '\\n=== Step 3: Run Simulation Training Loop (Active Practice) ===' && "
            f"python3 engines/mastery/train_mastery_simulator.py --days {days} --epochs {epochs} --start-balance {start_balance} && "
            "echo '\\n=== Step 4: Consolidate Posteriors (Apply Simulated Updates) ===' && "
            "python3 engines/consolidate_posteriors.py && "
            "echo '\\n=== Smart Mastery Training Complete ✅ ===' ; exec bash"
        )

        # Open new Terminal tab and execute
        osa = f'''
        tell application "Terminal"
            activate
            do script "{cmd}"
        end tell
        '''
        subprocess.run(["osascript", "-e", osa])
# === PATCH END ===


    # --- Execution Monitor (Learning execution) --------------------------------
# === PATCH START ===
# 📍 TARGET: trading_hub.py:handle_choice("3a")
# 📆 PATCHED: 2025-10-27Z — simplified Smart Learning Cycle (no log files)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    elif choice == "3a":
        print("[launch] Smart Learning Cycle → Replay → Digest → ML → Mastery → Consolidate → Report")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "echo '\n=== Step 1: Replay Runner ===' && "
            "python3 engines/replay_runner.py --days 7 --epochs 50 --start-balance 600 && "
            "echo '\n=== Step 2: Replay Digest (Canonical PnL) ===' && "
            "python3 replay_digest.py && "
            "echo '\n=== Step 3: Train ML Model (Playbooks) ===' && "
            "python3 engines/ml/ml_training_engine.py --days 90 && "
            "echo '\n=== Step 3b: Train Mastery Policy (Unified Reinforcement Flow) ===' && "
            "python3 engines/mastery/train_mastery.py && "
            "echo '\n=== Step 4: Consolidate Posteriors ===' && "
            "python3 engines/consolidate_posteriors.py && "
            "echo '\n=== Step 5: Canonical Analytics Report ===' && "
            "python3 analytics_report.py && "
            "echo '\n=== Smart Learning Cycle complete. Model upgraded successfully. ==='; exec bash"
        )
        subprocess.run([
            "osascript", "-e",
            f'tell application \"Terminal\" to do script \"{cmd}\"'
        ])
# === PATCH END ===




    elif choice == "3b":
        print("[launch] Execution Monitor → Digest → ML → Consolidate Only")
        cmd = (
            f"cd {PROJECT_DIR} && "
            "echo '\\n=== Step 1: Replay Digest ===' && "
            "python3 replay_digest.py && "
            "echo '\\n=== Step 2: Train ML Model ===' && "
            "python3 engines/ml/ml_training_engine.py --days 90 && "
            "echo '\\n=== Step 3: Consolidate Posteriors ===' && "
            "python3 engines/consolidate_posteriors.py && "
            "echo '\\n=== Partial learning cycle complete ===' ; exec bash"
        )
        subprocess.run([
            "osascript", "-e",
            f'tell application "Terminal" to do script "{cmd}"'
        ])
# === PATCH END ===



    elif choice == "3c":
        print("[launch] Execution Monitor → Resume Replay")
        resume_day = input("Enter resume day (e.g. 58, leave blank for auto): ").strip()
        epochs     = input("Enter total epochs (e.g. 100): ").strip()

        # defaults
        resume_arg = f"--resume-day {resume_day}" if resume_day else ""
        epochs_arg = f"--epochs {epochs}" if epochs else "--epochs 365"

        cmd = (
            f"cd {PROJECT_DIR} && "
            f"echo '=== Step 1: Resuming Replay {resume_arg} {epochs_arg} ===' && "
            f"python3 engines/replay_runner.py {resume_arg} {epochs_arg} && "
            "echo '=== Step 2: Consolidating outcomes ===' && "
            "python3 engines/consolidate_posteriors.py && "
            "echo '=== Step 3: Generating Digest report ===' && "
            "python3 replay_digest.py && "
            "echo '=== Resume pipeline complete ===' ; exec bash"
        )

        # escape quotes for AppleScript
        cmd_escaped = cmd.replace('"', '\\"')

        osa = f'''
        tell application "Terminal"
            activate
            do script "{cmd_escaped}"
        end tell
        '''
        subprocess.run(["osascript", "-e", osa])

    # === PATCH START ================================================================
    # 📍 TARGET: engines/control_center.py
    # 🔎 SEARCH: def control_center_main(
    # 📆 PATCHED: 2025-12-03 — Add menu entry “Run DB Repair”
    # ================================================================================

    # Insert this block INSIDE the main menu dispatcher, just after existing options:

# === PATCH START ============================================================
# 📍 TARGET: trading_hub.py:handle_choice("7a")
# 📆 PATCHED: 2025-12-03 — Proper System Tools → DB Repair integration
# ============================================================================

    elif choice == "7a":    # System Tools → DB Repair
        print("[system] Running full DB repair (AutoScalp DB Doctor)…")

        import subprocess, datetime
        from pathlib import Path

        # Correct project root
        root = Path(__file__).resolve().parent
        script = root / "scripts" / "db_repair_all.py"


        # timestamped logfile in data/db_repair_logs
        ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        logdir = root / "data" / "db_repair_logs"
        logdir.mkdir(parents=True, exist_ok=True)

        logdir.mkdir(exist_ok=True)
        logfile = logdir / f"repair-{ts}.log"

        print(f"[system] Log: {logfile}")

        try:
            with open(logfile, "w") as f:
                proc = subprocess.Popen(
                    ["python3", str(script)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
                for line in proc.stdout:
                    print(line, end="")
                    f.write(line)
            print("[system] DB Repair complete.")
        except Exception as e:
            print(f"[system] ERROR running DB repair: {e}")

            # -----------------------------------------------
            # Run LiveCache full-schema rebuilder
            # -----------------------------------------------
            print("\n[system] Rebuilding full LiveCache schema…")
            proc2 = subprocess.Popen(
                ["python3", str(rebuild_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in proc2.stdout:
                print(line, end="")
                f.write(line)

            print("\n[system] LiveCache Schema Rebuild complete.")

            print("\n[system] FULL DB FIX COMPLETE ✔️\n")

        except Exception as e:
            print(f"[system] ERROR in repair or rebuild: {e}")
# === PATCH END ============================================================

    else:
        print("Invalid option or not yet implemented.")

def main():
    while True:
        show_menu()
        choice = input("\nEnter choice (e.g. 1a, 2c, q): ").strip().lower()
        if choice == "q":
            print("Exiting Trading Hub.")
            break
        handle_choice(choice)


if __name__ == "__main__":
    main()
