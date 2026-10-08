-- grain: 1 row / (SK_ID_BUREAU, MONTHS_BALANCE). MONTHS_BALANCE <= 0 = months up to the application.
select "SK_ID_BUREAU"::bigint as sk_id_bureau, "MONTHS_BALANCE"::int as months_balance, "STATUS" as status
from {{ source('raw', 'bureau_balance') }}
