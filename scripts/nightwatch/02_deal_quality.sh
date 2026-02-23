#!/bin/bash
# Job 2: Deal Quality Analysis
# Analysiert die Qualitaet der gesammelten Deals:
# - Preis-Verteilung, Discount-Verteilung, Rating-Verteilung
# - Sind die Deals wirklich Keyboards oder Muell?
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
cd "$PROJECT_DIR" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') === DEAL QUALITY ==="

# Total deals in DB
docker-compose exec -T db psql -U postgres -d keeper -c "
  SELECT
    COUNT(*) as total_deals,
    ROUND(AVG(current_price)::numeric, 2) as avg_price,
    ROUND(AVG(discount_percent)::numeric, 1) as avg_discount,
    ROUND(AVG(rating)::numeric, 1) as avg_rating,
    MIN(current_price) as min_price,
    MAX(current_price) as max_price
  FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '6 hours';
" 2>/dev/null

# Price distribution buckets
echo "--- Preis-Verteilung (letzte 6h) ---"
docker-compose exec -T db psql -U postgres -d keeper -c "
  SELECT
    CASE
      WHEN current_price < 15 THEN '< 15 EUR (Billig)'
      WHEN current_price < 50 THEN '15-50 EUR (Budget)'
      WHEN current_price < 100 THEN '50-100 EUR (Mid)'
      WHEN current_price < 200 THEN '100-200 EUR (High)'
      ELSE '200+ EUR (Premium)'
    END as preisklasse,
    COUNT(*) as anzahl
  FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '6 hours'
  GROUP BY 1
  ORDER BY MIN(current_price);
" 2>/dev/null

# Top 5 deals by score
echo "--- Top 5 Deals (Score) ---"
docker-compose exec -T db psql -U postgres -d keeper -c "
  SELECT asin, LEFT(title, 50) as titel, current_price, discount_percent, deal_score
  FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '6 hours'
  ORDER BY deal_score DESC NULLS LAST
  LIMIT 5;
" 2>/dev/null

echo ""
