#!/usr/bin/env bash
set -euo pipefail

MARK="0x132"
TABLE="132"
SRC_NET="172.19.0.0/16"
GATEWAY="10.129.0.1"
EGRESS_DEV="eth0"
EGRESS_SRC="10.129.0.32"
COMMENT="wb-repricer-card-egress"

# Route only packets explicitly marked by the mangle rule through the new WB egress IP.
ip route replace default via "$GATEWAY" dev "$EGRESS_DEV" src "$EGRESS_SRC" table "$TABLE"
ip rule show | grep -q "fwmark $MARK lookup $TABLE" || ip rule add fwmark "$MARK" table "$TABLE" priority "$TABLE"

# Refresh only our own rules; leave all other Docker/VPN/firewall rules untouched.
for chain in PREROUTING; do
  while iptables -t mangle -S "$chain" | grep -q -- "--comment $COMMENT"; do
    rule=$(iptables -t mangle -S "$chain" | grep -- "--comment $COMMENT" | head -n1 | sed 's/^-A /-D /')
    iptables -t mangle $rule
  done
done

mapfile -t CARD_IPS < <(getent ahostsv4 card.wb.ru | awk '{print $1}' | sort -u)
if [ "${#CARD_IPS[@]}" -eq 0 ]; then
  echo "card.wb.ru did not resolve" >&2
  exit 1
fi

for ip in "${CARD_IPS[@]}"; do
  iptables -t mangle -A PREROUTING \
    -s "$SRC_NET" -d "$ip/32" -p tcp --dport 443 \
    -m comment --comment "$COMMENT" \
    -j MARK --set-mark "$MARK"
done

ip route flush cache || true
printf 'WB repricer card egress via %s (%s): %s\n' "$EGRESS_DEV" "$EGRESS_SRC" "${CARD_IPS[*]}"
