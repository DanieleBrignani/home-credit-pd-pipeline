-- grain out: 1 row / SK_ID_CURR. Source grain: 1 row / payment record of a previous loan.
-- Temporal guard: installment due date on/before the application (days_instalment <= 0) and, when paid,
-- paid on/before the application (days_entry_payment <= 0). Rows due in the future are excluded.
-- days_late > 0 = paid after the due date.
with i as (
    select
        sk_id_curr,
        days_instalment,
        days_entry_payment,
        amt_instalment,
        amt_payment,
        days_entry_payment - days_instalment as days_late
    from {{ ref('stg_installments') }}
    where days_instalment <= 0
      and (days_entry_payment is null or days_entry_payment <= 0)
)
select
    sk_id_curr,
    count(*)                                                                as inst_n_records,
    count(*) filter (where days_entry_payment is null)                      as inst_n_due_unpaid,
    avg((days_late > 0)::int) filter (where days_entry_payment is not null) as inst_late_share,
    avg(greatest(days_late, 0)) filter (where days_entry_payment is not null) as inst_mean_days_late,
    max(days_late)                                                          as inst_max_days_late,
    avg(amt_payment / nullif(amt_instalment, 0))                            as inst_mean_payment_ratio,
    avg((amt_payment < amt_instalment - 0.01)::int)
        filter (where days_entry_payment is not null)                       as inst_underpaid_share,
    avg((days_late > 0)::int) filter (
        where days_entry_payment is not null and days_instalment > -365)    as inst_late_share_last12m
from i
group by sk_id_curr
