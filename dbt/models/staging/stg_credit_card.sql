-- grain: 1 row / (SK_ID_PREV, MONTHS_BALANCE)
select
    "SK_ID_PREV"::bigint                 as sk_id_prev,
    "SK_ID_CURR"::bigint                 as sk_id_curr,
    "MONTHS_BALANCE"::int                as months_balance,
    "AMT_BALANCE"::numeric               as amt_balance,
    "AMT_CREDIT_LIMIT_ACTUAL"::numeric   as amt_credit_limit_actual,
    "AMT_DRAWINGS_ATM_CURRENT"::numeric  as amt_drawings_atm_current,
    "AMT_DRAWINGS_CURRENT"::numeric      as amt_drawings_current,
    "SK_DPD"::int                        as sk_dpd,
    "SK_DPD_DEF"::int                    as sk_dpd_def
from {{ source('raw', 'credit_card_balance') }}
