-- inspect_items.sql : checks to run BEFORE the customers/items fusion

\echo '--- 1. Is product_id unique in items? (dup_products > 0 -> collapse items first)'
SELECT count(*)                              AS rows,
       count(DISTINCT product_id)            AS products,
       count(*) - count(DISTINCT product_id) AS dup_products
FROM items;

\echo '--- 2. Repeated products: complementary (1 distinct value per column) or conflicting (>1)?'
SELECT product_id, count(*) AS n,
       count(DISTINCT category_id)   AS cat_ids,
       count(DISTINCT category_code) AS cat_codes,
       count(DISTINCT brand)         AS brands
FROM items
GROUP BY product_id
HAVING count(*) > 1
ORDER BY n DESC
LIMIT 10;

\echo '--- 3. Customer events with NO match in items (> 0 -> LEFT JOIN required)'
SELECT count(*) AS events_without_item
FROM customers c
WHERE NOT EXISTS (SELECT 1 FROM items i WHERE i.product_id = c.product_id);
