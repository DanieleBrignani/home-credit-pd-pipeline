-- grain: 1 row / SK_ID_BUREAU. DAYS_ENDDATE_FACT (realised end of credit) is deliberately NOT selected.
select
    "SK_ID_BUREAU"::bigint            as sk_id_bureau,
    "SK_ID_CURR"::bigint              as sk_id_curr,
    "CREDIT_ACTIVE"                   as credit_active,
    "CREDIT_TYPE"                     as credit_type,
    "DAYS_CREDIT"::int                as days_credit,
    "CREDIT_DAY_OVERDUE"::int         as credit_day_overdue,
    "AMT_CREDIT_MAX_OVERDUE"::numeric as amt_credit_max_overdue,
    "CNT_CREDIT_PROLONG"::int         as cnt_credit_prolong,
    "AMT_CREDIT_SUM"::numeric         as amt_credit_sum,
    "AMT_CREDIT_SUM_DEBT"::numeric    as amt_credit_sum_debt,
    "AMT_CREDIT_SUM_OVERDUE"::numeric as amt_credit_sum_overdue
from {{ source('raw', 'bureau') }}
