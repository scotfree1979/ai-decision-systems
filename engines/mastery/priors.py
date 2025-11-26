from __future__ import annotations
import json
from engines.config_paths import connect_db

def upsert_prior(conn, bin_key: str, p1_ab, p2_ab, p3_ab, pfill_ab, mae_mean, mae_std):
    conn.execute("""
        INSERT INTO mastery_priors (
          bin_key, prior_p1_alpha, prior_p1_beta, prior_p2_alpha, prior_p2_beta,
          prior_p3_alpha, prior_p3_beta, prior_fill_alpha, prior_fill_beta,
          prior_mae_mean, prior_mae_std, source
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'PRIOR')
        ON CONFLICT(bin_key) DO UPDATE SET
          prior_p1_alpha=excluded.prior_p1_alpha,
          prior_p1_beta=excluded.prior_p1_beta,
          prior_p2_alpha=excluded.prior_p2_alpha,
          prior_p2_beta=excluded.prior_p2_beta,
          prior_p3_alpha=excluded.prior_p3_alpha,
          prior_p3_beta=excluded.prior_p3_beta,
          prior_fill_alpha=excluded.prior_fill_alpha,
          prior_fill_beta=excluded.prior_fill_beta,
          prior_mae_mean=excluded.prior_mae_mean,
          prior_mae_std=excluded.prior_mae_std,
          updated_at=datetime('now')
    """, (bin_key, *p1_ab, *p2_ab, *p3_ab, *pfill_ab, mae_mean, mae_std))

def seed_simple_prior(bin_key: str) -> None:
    with connect_db(ro=False) as conn:
        upsert_prior(conn, bin_key,
                     (20.0, 20.0), (15.0, 25.0), (10.0, 30.0),
                     (30.0, 20.0), 1.5, 0.7)
        conn.commit()
