import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter
import sqlite3
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from daily_config import fetch_available_budget
from config_paths import DB_PATH


def generate_signal_status_report(self):
    time.sleep(300)  # Delay reporting by 5 minutes to allow full system launch

    while True:
        try:
            if self.session_active:
                os.system("cls" if os.name == "nt" else "clear")

            now = datetime.utcnow().strftime("%H:%M:%S")
            print("🧠 SIGNAL MEMORY ENGINE STATUS (Updated: {} UTC)".format(now))

            # === DAILY OVERVIEW ===
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(DISTINCT marketId), COUNT(*) FROM bets
                WHERE substr(marketStartTime, 1, 10) = ?
            """, (datetime.utcnow().strftime("%Y-%m-%d"),))
            mkt_row = cursor.fetchone()
            total_today_markets = mkt_row[0] if mkt_row else 0
            total_today_runners = mkt_row[1] if mkt_row else 0

            if not hasattr(self, "_first_oc1_target_time"):
                cursor.execute("""
                    SELECT MIN(marketStartTime) FROM bets
                    WHERE substr(marketStartTime, 1, 10) = ?
                """, (datetime.utcnow().strftime("%Y-%m-%d"),))
                next_start = cursor.fetchone()[0]

                if next_start:
                    try:
                        mkt_time = datetime.fromisoformat(next_start.replace("Z", "+00:00"))
                        self._first_oc1_target_time = mkt_time - timedelta(minutes=80)
                    except:
                        self._first_oc1_target_time = None
                else:
                    self._first_oc1_target_time = None
            conn.close()

            print("📋 DAILY OVERVIEW")
            print("-" * 56)
            print(f"🗓 Races Scheduled Today:   {total_today_markets}")
            print(f"🏇 Runners Scheduled:       {total_today_runners}")

            print("\n🎯 RUNNER TIER SUMMARY")
            print("-" * 56)
            tier_counter = {"active": 0, "passive": 0, "ignored": 0}
            for market_id, runners in self.live_markets.items():
                for selection_id in runners:
                    snapshot = self.get_static_snapshot().get((market_id, selection_id), {}).get("snapshot", {})
                    tier = snapshot.get("tier")
                    if tier in tier_counter:
                        tier_counter[tier] += 1

            print(f"✅ Active Runners:          {tier_counter['active']}")
            print(f"⏸ Passive Runners:         {tier_counter['passive']}")
            print(f"❌ Ignored Runners:         {tier_counter['ignored']}")


            if hasattr(self, "_first_oc1_target_time") and self._first_oc1_target_time:
                try:
                    delta = self._first_oc1_target_time - datetime.now(timezone.utc)
                    if delta.total_seconds() > 0:
                        hours, remainder = divmod(int(delta.total_seconds()), 3600)
                        minutes = remainder // 60
                        time_to_oc1 = f"{hours}h {minutes}m"
                    else:
                        time_to_oc1 = "✅ < live"
                except Exception:
                    time_to_oc1 = "n/a"
            else:
                time_to_oc1 = "n/a"
              
            # === OC BAND COUNTDOWNS ===
            print("\n⏱ OC BAND COUNTDOWNS")
            print("-" * 56)
            for i in range(0, 8):
                label = f"OC{i}"
                if hasattr(self, "_first_oc1_target_time") and self._first_oc1_target_time:
                    target_time = self._first_oc1_target_time + timedelta(minutes=i * 20)
                    delta_minutes = (target_time - datetime.now(timezone.utc)).total_seconds() / 60


                    if delta_minutes > -5:
                        hours, remainder = divmod(int(delta_minutes * 60), 3600)
                        minutes = remainder // 60
                        if delta_minutes > 0:
                            print(f"{label} → Starts in {hours}h {minutes}m    ⏱ Waiting...")
                        else:
                            print(f"{label} → < 5m grace          ⏱ Waiting...")
                    else:
                        found = False
                        for market_id, runners in self.live_markets.items():
                            for selection_id in runners:
                                story = self.get_runner_story(market_id, selection_id)
                                chapters = story.get("chapters", [])
                                if any(c.get("oc_label") == label for c in chapters):
                                    found = True
                                    break
                            if found:
                                break
                        status = "✅ Data in bets" if found else "❌ Not found"
                        print(f"{label} → {status}")
                else:
                    print(f"{label} → n/a")


            print(f"⏲ Time Until First OC1:    {time_to_oc1}")
            print("-" * 56)

            # === LIVE SYSTEM STATS ===
            total_markets = len(self.live_markets)
            total_runners = sum(len(runners) for runners in self.live_markets.values())
            story_complete = 0
            exploratory = partial = full = 0
            confidence_levels = []
            scalp_log = []

            for market_id, runners in self.live_markets.items():
                for selection_id in runners:
                    story = self.get_runner_story(market_id, selection_id)
                    chapters = story.get("chapters", [])
                    if not chapters:
                        continue
                    story_complete += 1
                    latest = chapters[-1]
                    conf = latest.get("confidence", 0.0)
                    confidence_levels.append(conf)
                    label = latest.get("oc_label", "OC?")
                    if conf < 0.55:
                        exploratory += 1
                        scalp_log.append(f"{label} 🧪 {selection_id:<20} Conf: {conf:.2f} → exploratory")
                    elif conf < 0.7:
                        partial += 1
                        scalp_log.append(f"{label} ⚡ {selection_id:<20} Conf: {conf:.2f} → partial")
                    else:
                        full += 1
                        scalp_log.append(f"{label} 🎯 {selection_id:<20} Conf: {conf:.2f} → full")

            print(f"🟢 Markets Tracked:         {total_markets}")
            print(f"🔁 Runners Evaluated:       {total_runners}")
            print(f"📘 Runner Stories Complete: {story_complete}")
            print(f"🎯 Scalp Triggers Today:    {self.counter.get('scalps_fired', 0)}")

            print("\n📊 BLUEPRINT MATCHING")
            print("-" * 56)
            print(f"🧪 Exploratory Matches:     {exploratory}")
            print(f"⚡ Partial Matches:         {partial}")
            print(f"🎯 Full Blueprint Matches:  {full}")
            if confidence_levels:
                print(f"Highest Confidence So Far: {max(confidence_levels):.2f}")

            print("\n🏇 RUNNER SNAPSHOTS (Top 5)")
            print("-" * 56)
            for line in scalp_log[:5]:
                print(line)


            # === BUDGET STATUS ===
            print("\n💰 BUDGET STATUS")
            print("-" * 56)
            available = fetch_available_budget()
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT marketId, odds, stake FROM bets
                WHERE status = 'unmatched' AND side = 'LAY'
            """)
            rows = cursor.fetchall()
            conn.close()

            liability_by_market = defaultdict(float)
            for market_id, odds, stake in rows:
                liab = max(0, (odds - 1) * stake)
                if liab > liability_by_market[market_id]:
                    liability_by_market[market_id] = liab
            total_risk = sum(liability_by_market.values())
            util_pct = (total_risk / available) * 100 if available else 0

            print(f"Available Funds:          £{available:.2f}")
            print(f"Current Risk (Max Liab):  £{total_risk:.2f}")
            print(f"Budget Utilization:       {util_pct:.1f}%")
            print(f"Scalps Fired This Hour:   {self.counter.get('scalps_fired', 0)}")
            print(f"Scalps Blocked by Budget: {self.counter.get('scalp_blocked', 0)}")

            # === SYSTEM HEALTH ===
            print("\n✅ SYSTEM HEALTH")
            print("-" * 56)
            if story_complete >= 1:
                print("All systems running. OC data flowing. Blueprint signals active.")
            else:
                print("⚠️  Low chapter coverage. Waiting for more OC bands to complete.")

            print("\n⏱ SYSTEM READINESS DIAGNOSTICS")
            print("-" * 56)
            # Gather chapters per runner once, safely
            chapters_by_runner = []
            for market_id, selection_ids in self.live_markets.items():
                for selection_id in selection_ids:
                    story = self.get_runner_story(market_id, selection_id)
                    chapters = story.get("chapters", [])
                    if chapters:
                        chapters_by_runner.extend(chapters)

            readiness = {
                "OC Bands": any(len(c.get("oc_band", [])) > 0 for c in chapters_by_runner),
                "Confidence": any(c.get("confidence", 0) >= 0.52 for c in chapters_by_runner),
                "Blueprints": any(c.get("blueprint_match") for c in chapters_by_runner),
                "RAM Snapshots": total_runners > 0,
                "Liability Check": total_risk <= available,
                "Story Chapters": story_complete > 0
            }

            for label, ok in readiness.items():
                status = "✅" if ok else "❌"
                print(f"{label:<20} {status}")

            time.sleep(2)

        except Exception as e:
            print(f"❌ Error in status report: {e}")
            time.sleep(2)
