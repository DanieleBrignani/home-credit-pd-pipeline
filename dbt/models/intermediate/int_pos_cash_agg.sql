-- grain out: 1 row / SK_ID_CURR. Source grain: 1 row / (loan SK_ID_PREV, month). Temporal guard: months_balance < 0.
select
    sk_id_curr,
    count(distinct sk_id_prev)             as pos_n_loans,
    max(sk_dpd)                            as pos_max_dpd,
    avg((sk_dpd_def > 0)::int)             as pos_share_months_dpd_def
from {{ ref('stg_pos_cash') }}
where months_balance < 0
group by sk_id_curr
