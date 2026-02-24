#!/bin/sh
# Kibana Dashboard Auto-Import
# Waits for Kibana to be ready, then imports saved objects (data views + dashboards).

KIBANA_URL="http://kibana:5601"
NDJSON_FILE="/setup/saved_objects.ndjson"
MAX_RETRIES=60
RETRY_INTERVAL=5

echo "Waiting for Kibana at ${KIBANA_URL} ..."

retries=0
while [ "$retries" -lt "$MAX_RETRIES" ]; do
  status=$(curl -s -o /dev/null -w "%{http_code}" "${KIBANA_URL}/api/status")
  if [ "$status" = "200" ]; then
    echo "Kibana is ready (HTTP 200)."
    break
  fi
  retries=$((retries + 1))
  echo "  Attempt ${retries}/${MAX_RETRIES} — HTTP ${status}, retrying in ${RETRY_INTERVAL}s ..."
  sleep "$RETRY_INTERVAL"
done

if [ "$retries" -eq "$MAX_RETRIES" ]; then
  echo "ERROR: Kibana not ready after $((MAX_RETRIES * RETRY_INTERVAL))s. Giving up."
  exit 1
fi

echo "Importing saved objects from ${NDJSON_FILE} ..."

response=$(curl -s -w "\n%{http_code}" \
  -X POST "${KIBANA_URL}/api/saved_objects/_import?overwrite=true" \
  -H "kbn-xsrf: true" \
  --form file=@"${NDJSON_FILE}")

http_code=$(echo "$response" | tail -1)
body=$(echo "$response" | sed '$d')

if [ "$http_code" != "200" ]; then
  echo "ERROR: Import failed (HTTP ${http_code})"
  echo "$body"
  exit 1
fi

echo "Dashboards imported successfully!"
echo "$body"

# Post-import: Set time ranges on dashboards.
# Kibana's import migration strips timeFrom/timeTo attributes,
# so we must set them via the saved_objects API after import.
echo ""
echo "Setting dashboard time ranges (now-30d) ..."

for dashboard_id in deal-overview-dashboard price-monitor-dashboard keepa-pipeline-monitor; do
  update_response=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "${KIBANA_URL}/api/saved_objects/dashboard/${dashboard_id}" \
    -H "kbn-xsrf: true" \
    -H "Content-Type: application/json" \
    -d '{"attributes": {"timeFrom": "now-30d", "timeTo": "now"}}')
  if [ "$update_response" = "200" ]; then
    echo "  ${dashboard_id}: timeFrom=now-30d timeTo=now"
  else
    echo "  ${dashboard_id}: WARNING — HTTP ${update_response} (time range not set)"
  fi
done

echo "Setup complete."
exit 0
