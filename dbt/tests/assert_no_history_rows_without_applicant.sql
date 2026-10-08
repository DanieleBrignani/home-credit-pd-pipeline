-- fan-out / orphan guard: history aggregates may only exist for known applicants
select i.sk_id_curr from {{ ref('int_installments_agg') }} i
left join {{ ref('stg_application') }} a using (sk_id_curr) where a.sk_id_curr is null
