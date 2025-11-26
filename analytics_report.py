#!/usr/bin/env python3
# analytics_report.py — Canonical Digest Report (tuple-key format)
import os, json, ast, datetime
from collections import defaultdict

DIGEST_PATH = "data/reports/digest_context_latest.json"

def _print_header(title: str):
    print("\n" + "="*80)
    print(f"{title.upper()}")
    print("="*80)

def _print_rows(rows, cols):
    if not rows:
        print("⚠️  No data found.\n")
        return
    widths = [max(len(str(r.get(c,''))) for r in rows) for c in cols]
    widths = [max(w, len(c)) for w,c in zip(widths,cols)]
    fmt = "  ".join([f"{{:{w}}}" for w in widths])
    print(fmt.format(*cols))
    print("  ".join(["─"*w for w in widths]))
    for r in rows:
        print(fmt.format(*[r.get(c,'') for c in cols]))
    print()

def load_digest():
    if not os.path.exists(DIGEST_PATH):
        print(f"[analytics] missing {DIGEST_PATH}")
        return []
    with open(DIGEST_PATH) as f:
        raw = json.load(f)
    rows = []
    for k,v in raw.items():
        try:
            t = ast.literal_eval(k)
            # expected: (letter, venue, country, fav_rank, mto)
            if len(t) == 5:
                letter, venue, country, fav_rank, mto = t
            else:
                # fallback if structure changes
                letter, venue, country, fav_rank, mto = (list(t)+["unk"]*5)[:5]
            rows.append({
                "letter": letter,
                "venue": venue,
                "country": country,
                "fav_rank": fav_rank,
                "mto_band": mto,
                "pnl": float(v or 0.0)
            })
        except Exception:
            continue
    return rows

def aggregate(rows, keys):
    out = defaultdict(lambda: {"pnl":0.0,"n":0})
    for r in rows:
        key = tuple(r.get(k,"unk") for k in keys)
        out[key]["pnl"] += float(r.get("pnl") or 0.0)
        out[key]["n"] += 1
    res=[]
    for k,v in out.items():
        row = dict(zip(keys,k))
        row["pnl"]=round(v["pnl"],2)
        row["trades"]=v["n"]
        res.append(row)
    return res

# ───────────────────────────────────────────────────────────────
def section_letters(rows):
    _print_header("▶ Letter Performance")
    data = aggregate(rows,["letter"])
    data.sort(key=lambda r:r["pnl"],reverse=True)
    _print_rows(data,["letter","pnl","trades"])

def section_venues(rows):
    _print_header("▶ Venue Performance")
    data = aggregate(rows,["venue"])
    data.sort(key=lambda r:r["pnl"],reverse=True)
    _print_rows(data,["venue","pnl","trades"])

def section_favrank(rows):
    _print_header("▶ Favourite Rank Performance")
    data = aggregate(rows,["fav_rank"])
    data.sort(key=lambda r:r["pnl"],reverse=True)
    _print_rows(data,["fav_rank","pnl","trades"])

def section_mto(rows):
    _print_header("▶ Minutes-to-Off Performance")
    data = aggregate(rows,["mto_band"])
    data.sort(key=lambda r:r["pnl"],reverse=True)
    _print_rows(data,["mto_band","pnl","trades"])

def section_letter_venue(rows):
    _print_header("▶ Letter × Venue")
    data = aggregate(rows,["letter","venue"])
    data.sort(key=lambda r:r["pnl"],reverse=True)
    _print_rows(data,["letter","venue","pnl","trades"])

def section_letter_fav(rows):
    _print_header("▶ Letter × FavRank")
    data = aggregate(rows,["letter","fav_rank"])
    data.sort(key=lambda r:r["pnl"],reverse=True)
    _print_rows(data,["letter","fav_rank","pnl","trades"])

def section_letter_mto(rows):
    _print_header("▶ Letter × MTO Band")
    data = aggregate(rows,["letter","mto_band"])
    data.sort(key=lambda r:r["pnl"],reverse=True)
    _print_rows(data,["letter","mto_band","pnl","trades"])

# ───────────────────────────────────────────────────────────────
def main():
    print("=== CANONICAL DIGEST REPORT ===")
    print(f"[source] {DIGEST_PATH}")
    print(f"[generated] {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    rows = load_digest()
    if not rows:
        print("⚠️ No canonical digest data found.\n")
        return

    total_pnl = sum(r["pnl"] for r in rows)
    print(f"Total canonical P&L: £{total_pnl:.2f}\n")

    section_letters(rows)
    section_venues(rows)
    section_favrank(rows)
    section_mto(rows)
    section_letter_venue(rows)
    section_letter_fav(rows)
    section_letter_mto(rows)

    print("\n[analytics] Report complete.\n")

if __name__=="__main__":
    main()
