-- grain: 1 row / SK_ID_CURR that has any history. Also the ONLINE feature lookup table used by the API.
-- Count-type features are 0-filled downstream only where "no history" genuinely means zero events;
-- ratios stay NULL (unknown) so the model can use missingness. Done in features.py, not here.
select
    a.sk_id_curr,
    b.bureau_n_credits, b.bureau_n_active, b.bureau_share_active, b.bureau_n_credit_types,
    b.bureau_oldest_credit_days, b.bureau_newest_credit_days, b.bureau_sum_credit, b.bureau_sum_debt,
    b.bureau_debt_to_credit, b.bureau_sum_overdue, b.bureau_max_overdue_ever, b.bureau_n_currently_overdue,
    b.bureau_sum_prolong, b.bureau_bal_months_observed, b.bureau_bal_dpd_month_share, b.bureau_bal_worst_status,
    p.prev_n_applications, p.prev_n_approved, p.prev_n_refused, p.prev_refusal_rate, p.prev_n_consumer_loans,
    p.prev_n_revolving_loans, p.prev_avg_amt_application, p.prev_avg_credit_to_application, p.prev_avg_cnt_payment,
    p.prev_avg_rate_down_payment, p.prev_last_decision_days, p.prev_first_decision_days,
    i.inst_n_records, i.inst_n_due_unpaid, i.inst_late_share, i.inst_mean_days_late, i.inst_max_days_late,
    i.inst_mean_payment_ratio, i.inst_underpaid_share, i.inst_late_share_last12m,
    c.cc_n_cards, c.cc_n_months, c.cc_mean_utilization, c.cc_max_utilization, c.cc_mean_dpd, c.cc_max_dpd,
    c.cc_share_months_dpd_def, c.cc_atm_drawing_share,
    s.pos_n_loans, s.pos_max_dpd, s.pos_share_months_dpd_def
from {{ ref('stg_application') }} a
left join {{ ref('int_bureau_agg') }}        b using (sk_id_curr)
left join {{ ref('int_previous_agg') }}      p using (sk_id_curr)
left join {{ ref('int_installments_agg') }}  i using (sk_id_curr)
left join {{ ref('int_credit_card_agg') }}   c using (sk_id_curr)
left join {{ ref('int_pos_cash_agg') }}      s using (sk_id_curr)
