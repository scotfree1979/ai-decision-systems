# ✅ Rebuilt Streamlit Dashboard with Clean Layout and Fixed Indent Error

import streamlit as st
from streamlit_autorefresh import st_autorefresh
from datetime import datetime, timedelta
from database_hijack_monitor import enqueue_read
from daily_config import fetch_available_budget
from upgrade_import_patch import get_session_token, set_session_token
from utils.api_tools import fetch_live_odds
import json
import pandas as pd
import requests

# 🔁 Refresh Intervals
st_autorefresh(interval=1000, key="refresh_top")

# ===============================
# 🔐 Session Token Input (Optional Manual Override)
# ===============================
if 'token_applied' not in st.session_state:
    st.session_state['token_applied'] = False

session_token = get_session_token()

if not session_token:
    st.error("❌ No session token found. Please enter it below.")
    with st.form("token_form"):
        manual_token = st.text_input("🔑 Enter Session Token", key="manual_token")
        submitted = st.form_submit_button("Submit Token")
        if submitted and manual_token:
            set_session_token(manual_token)
            st.session_state['token_applied'] = True
            st.success("✅ Session token applied.")
            st.rerun()
else:
    st.success("✅ Session token is active.")

# ===============================
# 📦 Data Loading Helpers
# ===============================
range_options = {
    "Today": 0,
    "Yesterday": 1,
    "Last 7 Days": 7,
    "Last 30 Days": 30,
    "Last 90 Days": 90
}

def load_bets(date_filter):
    days_back = range_options[date_filter]
    start_date = datetime.utcnow().date() - timedelta(days=days_back) if days_back else datetime.utcnow().date()
    df = enqueue_read("SELECT * FROM bets")
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df['marketStartTime'] = pd.to_datetime(df['marketStartTime'], errors='coerce')
    df = df.dropna(subset=['marketStartTime'])
    df['minutes_to_off'] = ((df['marketStartTime'] - pd.Timestamp.utcnow()).dt.total_seconds() / 60).round(0).astype('Int64')
    df['meta_json'] = df['meta_json'].apply(lambda x: json.loads(x) if x else {})
    df['swing_pct'] = df['meta_json'].apply(lambda x: x.get('volatility_swing_pct', None))
    df['anchor_odds'] = df['meta_json'].apply(lambda x: x.get('anchor_odds', None))
    df['status_tag'] = df['status']  # Just text label
    df['status_flag'] = df['status'].apply(
        lambda x: "🟢" if x == 'fully_enriched'
        else "🟡" if x == 'awaiting_signal'
        else "🔴" if x == 'missed'
        else "📦" if x == 'processing'
        else "✅" if x == 'approved'
        else "🏁" if x == 'settled'
        else ""
    )
    df['placement_issue'] = df.apply(lambda row: '❌ Not Placed' if row['status'] == 'approved' and row.get('placed', 1) == 0 else '', axis=1)
    df = df[df['marketStartTime'].dt.date >= start_date]
    return df

# ===============================
# 💸 Matched Stake Fetcher
# ===============================
def fetch_total_matched_stake():
    try:
        session_token = get_session_token()
        if not session_token:
            return 0.0
        headers = {
            'X-Application': 'CZHojduNWa3kxWIn',
            'X-Authentication': session_token,
            'Content-Type': 'application/json'
        }
        payload = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listCurrentOrders",
            "params": {},
            "id": 1
        }
        response = requests.post("https://api.betfair.com/exchange/betting/json-rpc/v1", headers=headers, json=payload)
        data = response.json()
        matched = [float(order['sizeMatched']) for order in data.get('result', {}).get('currentOrders', []) if order['sizeMatched'] > 0]
        return sum(matched)
    except Exception as e:
        print(f"⚠️ Failed to fetch matched stake: {e}")
        return 0.0

# ===============================
# ⏱️ Top Section: Live Clock + Enrichment
# ===============================
with st.container():
    st.markdown("## 🚦 Live Race Clock & Enrichment Tracker")
    clock_df = enqueue_read("SELECT marketStartTime, marketId, status, event_name, selectionId, horse_name FROM bets")
    clock_df['marketStartTime'] = pd.to_datetime(clock_df['marketStartTime'], errors='coerce')
    if clock_df['marketStartTime'].dt.tz is None:
        clock_df['marketStartTime'] = clock_df['marketStartTime'].dt.tz_localize('UTC')
    clock_df = clock_df.dropna(subset=['marketStartTime'])

    valid_times = clock_df['marketStartTime'].dt.tz_localize(None)
    future_races = valid_times[valid_times > datetime.utcnow()]
    earliest_start = future_races.min() if not future_races.empty else datetime.utcnow() + timedelta(hours=3)

    now = datetime.utcnow()
    enrich_cutoff = earliest_start - timedelta(minutes=85)
    time_to_race = max(0, int((earliest_start - now).total_seconds()))
    time_to_enrich = max(0, int((enrich_cutoff - now).total_seconds()))

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("⏱ Time to Next Race", str(timedelta(seconds=time_to_race)))
    col2.metric("⏱ Time to Enrichment", str(timedelta(seconds=time_to_enrich)))
    col3.metric("🕓 Betfair UTC", datetime.utcnow().strftime('%H:%M:%S'))
    col4.metric("🕔 Local Time", datetime.now().strftime('%H:%M:%S'))

    stages = [
        ("Pending → Upcoming", earliest_start - timedelta(minutes=90), 'upcoming'),
        ("Upcoming → Awaiting Signal", earliest_start - timedelta(minutes=20), 'awaiting_signal'),
        ("Awaiting Signal → Signal Assigned", earliest_start - timedelta(minutes=10), 'signal_assigned'),
        ("Signal Assigned → Fully Enriched", earliest_start - timedelta(minutes=5), 'fully_enriched')
    ]
    st.markdown("### 🧭 Enrichment Progress")
    cols = st.columns(len(stages))
    for i, (label, target_time, status_val) in enumerate(stages):
        if (clock_df['status'] == status_val).any():
            cols[i].success(f"✅ {label}")
        else:
            sec_remaining = int((target_time - now).total_seconds())
            if sec_remaining < 0:
                cols[i].error(f"❌ {label} missed")
            else:
                cols[i].warning(f"⏱ {label} – {str(timedelta(seconds=sec_remaining))}")

# ===============================
# 📊 Live Next Race Table (Updated to Preload First Race with Dropdown)
# ===============================
import time

st.markdown("### 🏇 Live Next Race Preview")
try:
    preview_df = enqueue_read("""
        SELECT marketId, selectionId, race_name, horse_name, odds, matched_liability, unmatched_liability, pnl, marketStartTime
        FROM bets
        WHERE marketStartTime >= datetime('now')
        ORDER BY marketStartTime ASC
    """)

    if not preview_df.empty:
        preview_df['marketStartTime'] = pd.to_datetime(preview_df['marketStartTime'], errors='coerce')

        # 🧠 Get unique races for dropdown
        race_options = preview_df[['marketId', 'race_name', 'marketStartTime']].drop_duplicates()
        race_options = race_options.sort_values('marketStartTime')
        race_options['label'] = race_options.apply(lambda row: f"{row['race_name']} @ {row['marketStartTime'].strftime('%H:%M')}", axis=1)

        selected_label = st.selectbox("Select Race", race_options['label'].tolist())
        selected_market_id = race_options[race_options['label'] == selected_label]['marketId'].values[0]

        # ⏱️ Limit refresh rate for odds updates
        if 'last_refresh' not in st.session_state:
            st.session_state['last_refresh'] = time.time()

        refresh_data = False
        if time.time() - st.session_state['last_refresh'] >= 20:
            st.session_state['last_refresh'] = time.time()
            refresh_data = True

        if refresh_data:
            # 🔁 Inject Live Odds Per Runner
            def extract_odds(row):
                try:
                    odds = fetch_live_odds(session_token, row['marketId'], row['selectionId'])
                    return pd.Series({
                        'Lay Odds': odds['lay'] if odds else None,
                        'Back Odds': odds['back'] if odds else None
                    })
                except:
                    return pd.Series({'Lay Odds': None, 'Back Odds': None})

            selected_df = preview_df[preview_df['marketId'] == selected_market_id].copy()
            selected_df[['Lay Odds', 'Back Odds']] = selected_df.apply(extract_odds, axis=1)

            max_odds = selected_df['Lay Odds'].max()
            selected_df['% of Max Odds'] = ((selected_df['Lay Odds'] / max_odds) * 100).round(1) if max_odds else None
            selected_df['Race Pos %'] = selected_df['Lay Odds'].apply(lambda x: min(round((1.01 / x) * 100, 1), 100) if x and x > 0 else None)

            st.session_state[f'cached_odds_df_{selected_market_id}'] = selected_df
        else:
            if f'cached_odds_df_{selected_market_id}' in st.session_state:
                selected_df = st.session_state[f'cached_odds_df_{selected_market_id}']
            else:
                selected_df = preview_df[preview_df['marketId'] == selected_market_id].copy()
                selected_df['Lay Odds'] = None
                selected_df['Back Odds'] = None
                selected_df['% of Max Odds'] = None
                selected_df['Race Pos %'] = None
            # st.info("⏳ Waiting for refresh window... (20s throttle)")  # hidden for clean display

        preview_rows = selected_df[[
            'race_name', 'horse_name', 'Lay Odds', 'Back Odds', 'Race Pos %', 'matched_liability', 'unmatched_liability', 'pnl'
        ]].rename(columns={
            'race_name': 'Meeting',
            'horse_name': 'Runner',
            'matched_liability': 'Matched',
            'unmatched_liability': 'Unmatched',
            'pnl': 'PnL'
        })

        st.dataframe(

    preview_rows[['Meeting', 'Runner', 'Lay Odds', 'Back Odds', 'Race Pos %', 'Matched', 'Unmatched', 'PnL']]
    .style.set_table_styles([
        {'selector': 'th', 'props': [('text-align', 'center')]},
        {'selector': 'td', 'props': [('text-align', 'center')]}
    ], overwrite=False)
    .set_properties(subset=['Race Pos %'], **{'min-width': '180px', 'max-width': '180px'})
    .format({
        'Lay Odds': lambda x: f"{x:.2f}" if pd.notnull(x) else '',
        'Back Odds': lambda x: f"{x:.2f}" if pd.notnull(x) else '',
        'Race Pos %': lambda x: f"▮ {x:.1f}%" if pd.notnull(x) else ''
    })
    ,
    use_container_width=True
)

    else:
        st.info("ℹ️ No upcoming race market could be matched. Waiting for database update.")

except Exception as e:
    st.warning(f"⚠️ Failed to load Live Preview: {e}")

# ✅ 1-Second Live Race Pos % Bar Refresh
    if 'last_racepos_update' not in st.session_state or time.time() - st.session_state['last_racepos_update'] >= 1:
        st.session_state['last_racepos_update'] = time.time()

        def update_race_pos(row):
            try:
                odds = fetch_live_odds(session_token, row['marketId'], row['selectionId'])
                lay = odds['lay'] if odds else None
                return min(round((1.01 / lay) * 100, 1), 100) if lay and lay > 0 else None
            except:
                return None

            selected_df['Race Pos %'] = selected_df.apply(update_race_pos, axis=1)
            selected_df['__rank__'] = selected_df['Lay Odds'].rank(method='min')

            def get_color(rank):
                if rank == 1:
                    return '#e63946'
                elif rank == 2:
                    return '#f4a261'
                elif rank == 3:
                    return '#2a9d8f'
                elif rank == 4:
                    return '#457b9d'
                else:
                    return '#bdbdbd'

            selected_df['__color__'] = selected_df['__rank__'].apply(lambda x: get_color(int(x)) if pd.notnull(x) else '#bdbdbd')

            st.dataframe(
                selected_df[['Runner', 'Race Pos %']]
                .style
                .apply(lambda col: [f'background-color: {c}' for c in selected_df['__color__']], subset=['Race Pos %'])
                .set_properties(subset=['Race Pos %'], **{'min-width': '180px', 'max-width': '180px'})
                .format({'Race Pos %': lambda x: f"▮ {x:.1f}%" if pd.notnull(x) else ''}),
                use_container_width=True
            )


# ===============================
# 📊 Main Dashboard Section (KPIs + Tables)
# ===============================
date_filter = st.selectbox("📆 Filter Race Data By:", list(range_options.keys()), index=0)
bets_df = load_bets(date_filter)
if bets_df.empty:
    st.warning("⚠️ No race data returned. Check filters or DB.")
    st.stop()

st_autorefresh(interval=60000, key="refresh_bottom")
st.write("🕒 Dashboard last refreshed at:", datetime.utcnow().strftime("%H:%M:%S"))
st.title("📈 Race Tool System Overview")

col1, col2, col3 = st.columns(3)
col1.metric("Total Signals", len(bets_df))
col2.metric("System P&L", f"£{bets_df['pnl'].sum():.2f}")
col3.metric("Settled Bets", len(bets_df[bets_df['status'] == 'settled']))

col4, col5, col6 = st.columns(3)
col4.metric("Available Budget", f"£{fetch_available_budget():.2f}")
col5.metric("Total Staked", f"£{fetch_total_matched_stake():.2f}")
col6.metric("Projected Exposure", f"£{fetch_total_matched_stake() * 1.1:.2f}")

# ===============================
# 📊 Column Formatter with 2-Row Header
# ===============================
def format_dashboard_columns(df):
    # Define new column layout and labels
    column_order = [
        'stake', 'odds', 'bet_type', 'status_tag', 'horse_name', 'strategy_name', 'signal_type', 'bot_name',
        'race_name', 'market_name', 'anchor_odd', 'odds_check_1', 'odds_check_2', 'odds_check_3',
        'odds_check_4', 'odds_check_5', 'odds_check_6', 'customerOrderRef', 'test_mode', 'minutes_to_off',
        'status_flag', 'pnl', 'placed_at', 'result', 'bet_settled', 'matched_liability', 'unmatched_liability', 'placement_issue'
    ]

    display_cols = [
        'Stake', 'Odds', 'Bet Type', 'Status', 'Runner', 'Strategy', 'Signal', 'Bot',
        'Meeting', 'Market', 'A', '1', '2', '3', '4', '5', '6',
        'Unique Ref', 'Flow Counter', 'Minutes To Off', 'Status Flag',
        'PnL', 'Placed', 'Result', 'Bet Settled', 'Matched', 'Unmatched', 'Issues'
    ]

    df = df[column_order].rename(columns=dict(zip(column_order, display_cols)))

    multi_columns = [
        ('', col) if col not in ['A', '1', '2', '3', '4', '5', '6'] else ('Odds Checks', col)
        for col in df.columns
    ]
    df.columns = pd.MultiIndex.from_tuples(multi_columns)
    return df

# ===============================
# 📊 Display Tables
# ===============================
def show_df(df, title):
    st.subheader(title)
    formatted = format_dashboard_columns(df)
    st.dataframe(formatted.style.set_table_styles([
        {'selector': 'th','props': [('text-align', 'center')]},
        {'selector': 'td','props': [('text-align', 'center')]}
    ]), use_container_width=True)
    

show_df(bets_df[bets_df['status'] == 'settled'].sort_values('marketStartTime', ascending=False).head(15), "✅ Recently Completed Races")
show_df(bets_df[(bets_df['minutes_to_off'] <= 0) & (bets_df['minutes_to_off'] >= -15)], "🔴 In-Play Market Overview")
show_df(bets_df[(bets_df['minutes_to_off'] <= 20) & (bets_df['minutes_to_off'] > 0)], "🔥 Live Market Overview")
show_df(bets_df[(bets_df['status'] == 'upcoming') & (bets_df['minutes_to_off'] <= 90) & (bets_df['minutes_to_off'] > 20)], "⏳ Upcoming Races")
