select
    order_id || '-' || product_id                                as order_item_id,
    order_id,
    product_id,
    quantity,
    unit_price_cents / 100.0                                     as unit_price,
    discount_pct,
    round(quantity * unit_price_cents / 100.0, 2)                as gross_amount,
    round(quantity * unit_price_cents * (100 - discount_pct) / 10000.0, 2) as net_amount
from {{ source('raw', 'order_items') }}
