#!/bin/bash
# Job 8: Keyboard vs Non-Keyboard Ratio
# DER WICHTIGSTE CHECK! Prueft ob wir wirklich Keyboards sammeln
# und nicht wieder Fashion-Muell reinkommt
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
cd "$PROJECT_DIR" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') === KEYBOARD RATIO ==="

# Check titles for keyboard keywords
docker-compose exec -T db psql -U postgres -d keeper -c "
  SELECT
    'Keyboard-related' as kategorie,
    COUNT(*) as anzahl
  FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '6 hours'
    AND (
      LOWER(title) LIKE '%keyboard%'
      OR LOWER(title) LIKE '%tastatur%'
      OR LOWER(title) LIKE '%mechanisch%'
      OR LOWER(title) LIKE '%mechanical%'
      OR LOWER(title) LIKE '%cherry%'
      OR LOWER(title) LIKE '%logitech%k%'
      OR LOWER(title) LIKE '%razer%'
      OR LOWER(title) LIKE '%corsair%k%'
      OR LOWER(title) LIKE '%keycap%'
      OR LOWER(title) LIKE '%switch%'
      OR LOWER(title) LIKE '%qwertz%'
    )
  UNION ALL
  SELECT
    'TOTAL' as kategorie,
    COUNT(*) as anzahl
  FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '6 hours';
" 2>/dev/null

# Show suspicious non-keyboard titles
echo "--- Verdaechtige Nicht-Keyboard Deals (letzte 6h) ---"
docker-compose exec -T db psql -U postgres -d keeper -c "
  SELECT asin, LEFT(title, 60) as titel, current_price
  FROM collected_deals
  WHERE collected_at > NOW() - INTERVAL '6 hours'
    AND LOWER(title) NOT LIKE '%keyboard%'
    AND LOWER(title) NOT LIKE '%tastatur%'
    AND LOWER(title) NOT LIKE '%mechanisch%'
    AND LOWER(title) NOT LIKE '%mechanical%'
    AND LOWER(title) NOT LIKE '%cherry%'
    AND LOWER(title) NOT LIKE '%logitech%'
    AND LOWER(title) NOT LIKE '%razer%'
    AND LOWER(title) NOT LIKE '%corsair%'
    AND LOWER(title) NOT LIKE '%keycap%'
    AND LOWER(title) NOT LIKE '%qwertz%'
  ORDER BY collected_at DESC
  LIMIT 10;
" 2>/dev/null

echo ""
