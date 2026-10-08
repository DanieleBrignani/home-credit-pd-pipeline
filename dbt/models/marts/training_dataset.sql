-- grain: 1 row / SK_ID_CURR (labeled). Application columns (pre-ratio) + history aggregates + target + split.
-- split is a deterministic hash bucket of SK_ID_CURR (+salt): independent of row order and re-runs, and
-- stable if rows are added. 'test' is never read by training/tuning code (enforced in train.py + tests).
{% set hist = adapter.get_relation(database=target.database, schema='marts', identifier='applicant_history_features') %}
{% set hist_cols = [] %}
{% if execute and hist is not none %}
  {% for c in adapter.get_columns_in_relation(hist) if c.name != 'sk_id_curr' %}{% do hist_cols.append(c.name) %}{% endfor %}
{% endif %}
with base as (
    select a.*,
           (('x' || substr(md5(a.sk_id_curr::text || '{{ var("split_salt") }}'), 1, 8))::bit(32)::bigint % 100) as bucket
    from {{ ref('stg_application') }} a
)
select
    base.*,
    case when bucket < {{ var('train_pct') }} then 'train'
         when bucket < {{ var('train_pct') }} + {{ var('valid_pct') }} then 'valid'
         else 'test' end as split
    {%- for c in hist_cols %}, h.{{ c }}{% endfor %}
from base
left join {{ ref('applicant_history_features') }} h on h.sk_id_curr = base.sk_id_curr
