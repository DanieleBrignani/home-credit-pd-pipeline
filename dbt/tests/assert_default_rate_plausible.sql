-- the labeled population default rate must be a plausible credit-portfolio rate; guards against a flipped/corrupt TARGET.
-- (Home Credit published rate is ~8%; fixtures are ~10-12%.)
select avg(target) as rate from {{ ref('stg_application') }}
having avg(target) not between 0.02 and 0.25
