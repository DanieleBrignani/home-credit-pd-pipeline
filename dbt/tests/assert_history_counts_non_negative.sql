select sk_id_curr from {{ ref('applicant_history_features') }}
where bureau_n_credits < 0 or prev_n_applications < 0 or inst_n_records < 0 or cc_n_cards < 0 or pos_n_loans < 0
   or prev_n_approved + prev_n_refused > prev_n_applications
