-- The ingestion job sometimes loads the same payment twice; keep the latest load per payment id.
select
    id                                  as payment_id,
    order_id,
    method,
    amount_cents / 100.0                as amount,
    cast(paid_at as timestamp)          as paid_at
from {{ source('raw', 'payments') }}
qualify row_number() over (partition by id order by cast(_loaded_at as timestamp) desc) = 1
