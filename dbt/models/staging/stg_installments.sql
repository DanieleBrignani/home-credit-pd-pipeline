-- grain: 1 row / payment record (SK_ID_PREV, NUM_INSTALMENT_NUMBER, NUM_INSTALMENT_VERSION, payment)
select
    "SK_ID_PREV"::bigint            as sk_id_prev,
    "SK_ID_CURR"::bigint            as sk_id_curr,
    "NUM_INSTALMENT_NUMBER"::int    as num_instalment_number,
    "DAYS_INSTALMENT"::numeric      as days_instalment,
    "DAYS_ENTRY_PAYMENT"::numeric   as days_entry_payment,
    "AMT_INSTALMENT"::numeric       as amt_instalment,
    "AMT_PAYMENT"::numeric          as amt_payment
from {{ source('raw', 'installments_payments') }}
