select
    i.order_item_id,
    i.order_id,
    i.product_id,
    p.product_name,
    p.category,
    i.quantity,
    i.gross_amount,
    i.net_amount
from {{ ref('stg_order_items') }} i
join {{ ref('stg_products') }} p using (product_id)
