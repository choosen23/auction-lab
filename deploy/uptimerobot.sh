#!/bin/sh
# UptimeRobot monitor for auctionlab.dev -- the copy of record, same idea as
# auction-lab.caddy: UptimeRobot keeps no config file, so this script IS the
# configuration. Run it once (or again after wiping the account) with a
# Main API key from https://dashboard.uptimerobot.com/integrations:
#
#   UPTIMEROBOT_API_KEY=u...-... ./deploy/uptimerobot.sh
#
# Creates one keyword monitor: alerts if "Auction walkthrough" (the page
# <title>) stops appearing at https://auctionlab.dev -- catches the app being
# down AND the vhost serving the wrong thing, which a plain HTTP 200 check
# would miss. 5-minute interval (free-plan floor). Alert contacts are the
# account's defaults; manage those in the dashboard.
set -eu

: "${UPTIMEROBOT_API_KEY:?set UPTIMEROBOT_API_KEY -- create a Main API key at https://dashboard.uptimerobot.com/integrations}"

api() {
	endpoint=$1; shift
	curl -sf -X POST "https://api.uptimerobot.com/v2/$endpoint" \
		-d "api_key=$UPTIMEROBOT_API_KEY" -d format=json "$@"
}

# ponytail: create-if-missing by friendly name, no update logic -- edit in the
# dashboard or delete + rerun if the check itself needs to change.
if api getMonitors -d search=auctionlab.dev | grep -q '"url":"https://auctionlab.dev"'; then
	echo "monitor for auctionlab.dev already exists, nothing to do"
	exit 0
fi

api newMonitor \
	-d friendly_name=auctionlab.dev \
	-d url=https://auctionlab.dev \
	-d type=2 \
	-d keyword_type=2 \
	--data-urlencode "keyword_value=Auction walkthrough" \
	-d interval=300
echo
echo "created -- status at https://dashboard.uptimerobot.com/monitors"
