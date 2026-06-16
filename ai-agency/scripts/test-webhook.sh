#!/usr/bin/env bash
# =============================================================
#  Phase 4 — fire a test "checkout abandoned" event at n8n.
#  Confirms the full flow: webhook -> wait -> LLM -> email -> log.
#
#  Usage:  ./test-webhook.sh <VPS_IP> [recipient_email]
#  Example: ./test-webhook.sh 203.0.113.5 you@gmail.com
#
#  Tip: delay_minutes is set to 1 so the email fires in ~1 min.
#       Set it to 60 to test the real abandoned-cart timing.
# =============================================================
set -euo pipefail

IP="${1:-YOUR_IP}"
EMAIL="${2:-you@example.com}"
URL="http://${IP}/webhook/abandoned-cart"

echo "POST -> ${URL}  (recipient: ${EMAIL})"

curl -sS -X POST "$URL" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "Sarah Chen",
    "customer_email": "'"$EMAIL"'",
    "customer_phone": "",
    "cart_items": [
      { "name": "Aurora Linen Shirt",  "price": "$49.00" },
      { "name": "Canvas Weekender Bag", "price": "$89.00" }
    ],
    "cart_total": "$138.00",
    "checkout_url": "https://demo-store.example/checkout/abc123",
    "delay_minutes": 1
  }'

echo
echo "Expect: {\"status\":\"scheduled\", ...}  -> email arrives in ~1 min."
echo "Verify log:  docker compose exec n8n cat /home/node/.n8n/cart_recovery_log.csv"
