select
    id                          as product_id,
    name                        as product_name,
    lower(category)             as category,
    unit_price_cents / 100.0    as unit_price,
    is_active
from {{ source('raw', 'products') }}
