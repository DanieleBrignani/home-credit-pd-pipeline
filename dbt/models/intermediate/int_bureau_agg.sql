-- grain out: 1 row / SK_ID_CURR.  Path: bureau_balance (N per SK_ID_BUREAU) -> bureau (N per SK_ID_CURR) -> applicant.
-- Temporal guard: only credits opened on/before the application (days_credit <= 0) and balance months <= 0.
with bb as (
    select
        sk_id_bureau,
        count(*)                                                        as n_months,
        count(*) filter (where status in ('1','2','3','4','5'))         as n_dpd_months,
        max(case when status in ('1','2','3','4','5') then status::int end) as worst_status
    from {{ ref('stg_bureau_balance') }}
    where months_balance <= 0
    group by sk_id_bureau
),
b as (
    select * from {{ ref('stg_bureau') }} where days_credit <= 0
)
select
    b.sk_id_curr,
    count(*)                                                            as bureau_n_credits,
    count(*) filter (where b.credit_active = 'Active')                  as bureau_n_active,
    (count(*) filter (where b.credit_active = 'Active'))::numeric / count(*) as bureau_share_active,
    count(distinct b.credit_type)                                       as bureau_n_credit_types,
    min(b.days_credit)                                                  as bureau_oldest_credit_days,
    max(b.days_credit)                                                  as bureau_newest_credit_days,
    sum(b.amt_credit_sum)                                               as bureau_sum_credit,
    sum(b.amt_credit_sum_debt)                                          as bureau_sum_debt,
    sum(b.amt_credit_sum_debt) / nullif(sum(b.amt_credit_sum), 0)       as bureau_debt_to_credit,
    sum(b.amt_credit_sum_overdue)                                       as bureau_sum_overdue,
    max(b.amt_credit_max_overdue)                                       as bureau_max_overdue_ever,
    count(*) filter (where b.credit_day_overdue > 0)                    as bureau_n_currently_overdue,
    sum(b.cnt_credit_prolong)                                           as bureau_sum_prolong,
    sum(bb.n_months)                                                    as bureau_bal_months_observed,
    sum(bb.n_dpd_months)::numeric / nullif(sum(bb.n_months), 0)         as bureau_bal_dpd_month_share,
    max(bb.worst_status)                                                as bureau_bal_worst_status
from b
left join bb using (sk_id_bureau)
group by b.sk_id_curr
