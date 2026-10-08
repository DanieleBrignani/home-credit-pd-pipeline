-- grain: 1 row / SK_ID_PREV. DAYS_FIRST_DRAWING / DAYS_FIRST_DUE / DAYS_LAST_DUE* / DAYS_TERMINATION are NOT selected
-- (post-decision realised outcomes, may be future-dated; see docs/feature_lineage.md).
select
    "SK_ID_PREV"::bigint              as sk_id_prev,
    "SK_ID_CURR"::bigint              as sk_id_curr,
    "NAME_CONTRACT_TYPE"              as name_contract_type,
    "AMT_APPLICATION"::numeric        as amt_application,
    "AMT_CREDIT"::numeric             as amt_credit,
    "NAME_CONTRACT_STATUS"            as name_contract_status,
    "DAYS_DECISION"::int              as days_decision,
    "CNT_PAYMENT"::numeric            as cnt_payment,
    "RATE_DOWN_PAYMENT"::numeric      as rate_down_payment,
    "NAME_PORTFOLIO"                  as name_portfolio,
    "CODE_REJECT_REASON"              as code_reject_reason
from {{ source('raw', 'previous_application') }}
