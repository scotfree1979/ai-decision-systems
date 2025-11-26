# AutoScalp System Flow

```text
                         ┌──────────────────┐
                         │      GUI         │
                         │  (Step 1 creds,  │
                         │   control loop)  │
                         └────────┬─────────┘
                                  │
                                  ▼
                         ┌──────────────────┐
                         │ autoscalp_gui.db │
                         │ (AUTO: dashboard │
                         │  orders, decisions) 
                         └───────┬──────────┘
                                 │
                ┌────────────────┴────────────────┐
                │                                 │
                ▼                                 ▼
       ┌──────────────────┐              ┌──────────────────┐
       │    Feeder        │              │  Live Router     │
       │ (schedule, odds, │              │ (parent + child  │
       │  KPIs → AUTO +   │              │  placement)      │
       │   BETS DB)       │              └───┬──────────────┘
       └──────┬───────────┘                  │
              │                              │ place/cancel
              ▼                              ▼
      ┌──────────────────┐           ┌──────────────────┐
      │     bets.db      │           │   Betfair API     │
      │ (BETS: anchors,  │◄─────────▶│ (SportsAPING,     │
      │  oc_series,      │           │  Account, SSO)    │
      │  mastery,        │           └──────────────────┘
      │  order_events)   │
      └──────┬───────────┘
             │
             ▼
    ┌──────────────────┐
    │   Settlements    │
    │ (listCleared,    │
    │  reconcile)      │
    └──────┬───────────┘
           │ updates
           ▼
    ┌──────────────────┐
    │   Dashboard UI   │
    │ (markets, runners│
    │  tiles, orders   │
    │  tape)           │
    └──────────────────┘

Overwatcher → emits stop-loss events → Mastery Policy → feeds back into Live Router (new child/stop-loss placement).
```

---

### 📌 Notes
- **GUI**: single source of creds → saved into `app_kv`.  
- **Feeder**: writes schedules, odds, KPIs into AUTO + BETS DB.  
- **Live Router**: places parents & children (hedges, stop-loss, green-up).  
- **Overwatcher/Mastery**: drive stop-loss/green-up decisions.  
- **Settlements**: reconciles with Betfair (`listClearedOrders`).  
- **Dashboard UI**: reads from AUTO DB (`dashboard_markets`, `dashboard_runners`, `dashboard_tiles`, `dashboard_orders_tape`).  
