-- Normalises the five order statuses from their various raw spellings.
select
    id                                       as order_id,
    customer_id,
    cast(ordered_at as timestamp)            as ordered_at,
    cast(cast(ordered_at as timestamp) as date) as order_date,
    case lower(trim(status))
        when 'complete'  then 'completed'
        when 'canceled'  then 'cancelled'
        else lower(trim(status))
    end                                      as status,
    lower(trim(channel))                     as channel
from {{ source('raw', 'orders') }}
