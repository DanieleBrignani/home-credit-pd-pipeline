-- shares / rates must lie in [0,1]; any row returned is a failure
select sk_id_curr from {{ ref('applicant_history_features') }}
where bureau_share_active not between 0 and 1
   or bureau_bal_dpd_month_share not between 0 and 1
   or prev_refusal_rate not between 0 and 1
   or inst_late_share not between 0 and 1
   or inst_underpaid_share not between 0 and 1
   or cc_share_months_dpd_def not between 0 and 1
   or pos_share_months_dpd_def not between 0 and 1
