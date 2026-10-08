-- grain: 1 row / (SK_ID_PREV, MONTHS_BALANCE)
select
    "SK_ID_PREV"::bigint      as sk_id_prev,
    "SK_ID_CURR"::bigint      as sk_id_curr,
    "MONTHS_BALANCE"::int     as months_balance,
    "SK_DPD"::int             as sk_dpd,
    "SK_DPD_DEF"::int         as sk_dpd_def
from {{ source('raw', 'POS_CASH_balance') }}
