-- One row per customer. Trims names, normalises email casing/whitespace and country codes.
select
    id                                   as customer_id,
    trim(full_name)                      as customer_name,
    lower(trim(email))                   as email,
    upper(coalesce(country, 'unknown'))  as country,
    cast(signup_date as date)            as signup_date,
    lower(segment)                       as segment
from {{ source('raw', 'customers') }}
