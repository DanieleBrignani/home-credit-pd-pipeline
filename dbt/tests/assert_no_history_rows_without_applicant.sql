-- fan-out / orphan guard: history aggregates may only exist for known applicants.
-- The Home Credit history tables cover BOTH application_train and application_test clients, so "known" = train U test.
with known as (
    select "SK_ID_CURR"::bigint as sk_id_curr from {{ source('raw', 'application_train') }}
    union
    select "SK_ID_CURR"::bigint from {{ source('raw', 'application_test') }}
),
agg as (
    select sk_id_curr from {{ ref('int_installments_agg') }}
    union all select sk_id_curr from {{ ref('int_bureau_agg') }}
    union all select sk_id_curr from {{ ref('int_previous_agg') }}
    union all select sk_id_curr from {{ ref('int_credit_card_agg') }}
    union all select sk_id_curr from {{ ref('int_pos_cash_agg') }}
)
select agg.sk_id_curr from agg left join known using (sk_id_curr) where known.sk_id_curr is null
