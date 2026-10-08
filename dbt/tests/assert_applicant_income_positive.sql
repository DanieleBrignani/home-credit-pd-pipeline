{{ config(severity='warn') }}
select sk_id_curr from {{ ref('stg_application') }} where amt_income_total <= 0 or amt_credit <= 0
