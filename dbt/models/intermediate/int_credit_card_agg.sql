-- grain out: 1 row / SK_ID_CURR. Source grain: 1 row / (card SK_ID_PREV, month).
-- Temporal guard: months_balance < 0 (months before the application month).
select
    sk_id_curr,
    count(distinct sk_id_prev)                                              as cc_n_cards,
    count(*)                                                                as cc_n_months,
    avg(amt_balance / nullif(amt_credit_limit_actual, 0))                   as cc_mean_utilization,
    max(amt_balance / nullif(amt_credit_limit_actual, 0))                   as cc_max_utilization,
    avg(sk_dpd)                                                             as cc_mean_dpd,
    max(sk_dpd)                                                             as cc_max_dpd,
    avg((sk_dpd_def > 0)::int)                                              as cc_share_months_dpd_def,
    sum(amt_drawings_atm_current) / nullif(sum(amt_drawings_current), 0)    as cc_atm_drawing_share
from {{ ref('stg_credit_card') }}
where months_balance < 0
group by sk_id_curr
