#!/bin/bash
# Test script matching Nextcloud documentation

NC_URL="https://community.winning.com.cn:8080/nextcloud"
TOKEN="76hpkyv9"
SECRET="0d45cc887d3961e6a77c3813503c10b0a0dfa61810271247a572d13c3ddb36ca4f3e480d327f1777776030eaedee083947ac709479d48783fe31afe469cce6eb"
MESSAGE="Hello World"

# Build JSON body
BODY="{\"message\":\"${MESSAGE}\"}"

# Generate a random header and signature
RANDOM_HEADER=$(openssl rand -hex 32)
MESSAGE_TO_SIGN="${RANDOM_HEADER}${BODY}"
SIGNATURE=$(echo -n "${MESSAGE_TO_SIGN}" | openssl dgst -sha256 -hmac "${SECRET}" | cut -d' ' -f2)

echo "Random: ${RANDOM_HEADER}"
echo "Body: ${BODY}"
echo "Message to sign: ${MESSAGE_TO_SIGN}"
echo "Signature: ${SIGNATURE}"
echo ""
echo "Sending request..."

# Send the message
curl -X POST "${NC_URL}/ocs/v2.php/apps/spreed/api/v1/bot/${TOKEN}/message" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "OCS-APIRequest: true" \
  -H "X-Nextcloud-Talk-Bot-Random: ${RANDOM_HEADER}" \
  -H "X-Nextcloud-Talk-Bot-Signature: ${SIGNATURE}" \
  -d "${BODY}" \
  -v