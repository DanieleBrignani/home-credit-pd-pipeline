-- grain out: 1 row / SK_ID_CURR. Source grain: 1 row / SK_ID_PREV (N:1 to applicant).
-- Temporal guard: decision made before the current application (days_decision < 0).
-- Only decision-time attributes are used; realised-outcome date fields are not even staged.
select
    sk_id_curr,
    count(*)                                                                    as prev_n_applications,
    count(*) filter (where name_contract_status = 'Approved')                   as prev_n_approved,
    count(*) filter (where name_contract_status = 'Refused')                    as prev_n_refused,
    (count(*) filter (where name_contract_status = 'Refused'))::numeric / count(*) as prev_refusal_rate,
    count(*) filter (where name_contract_type = 'Consumer loans')               as prev_n_consumer_loans,
    count(*) filter (where name_contract_type = 'Revolving loans')              as prev_n_revolving_loans,
    avg(amt_application)                                                        as prev_avg_amt_application,
    avg(amt_credit / nullif(amt_application, 0)) filter (where name_contract_status = 'Approved')
                                                                                as prev_avg_credit_to_application,
    avg(cnt_payment)                                                            as prev_avg_cnt_payment,
    avg(rate_down_payment)                                                      as prev_avg_rate_down_payment,
    max(days_decision)                                                          as prev_last_decision_days,
    min(days_decision)                                                          as prev_first_decision_days
from {{ ref('stg_previous_application') }}
where days_decision < 0
group by sk_id_curr
