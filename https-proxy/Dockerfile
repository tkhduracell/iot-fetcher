# syntax=docker/dockerfile:1.6

# Build a Caddy binary with the MaxMind-format geolocation matcher compiled in,
# so /etc/caddy/Caddyfile can gate {$HA_DOMAIN} by country.
FROM caddy:2-builder-alpine AS builder
RUN xcaddy build --with github.com/porech/caddy-maxmind-geolocation

# DB-IP's Country Lite database is CC-BY 4.0 (no account/license key needed,
# unlike MaxMind's own GeoLite2 downloads) and ships in the same .mmdb format
# the module reads. Snapshots are published monthly; this pins to whatever
# month the image is built, so a rebuild is how the data gets refreshed.
FROM alpine:3.20 AS geoip
RUN apk add --no-cache curl && \
    curl -fSL -o /tmp/dbip.mmdb.gz "https://download.db-ip.com/free/dbip-country-lite-$(date +%Y-%m).mmdb.gz" && \
    gunzip /tmp/dbip.mmdb.gz && \
    mv /tmp/dbip.mmdb /dbip-country-lite.mmdb

FROM caddy:2-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY --from=geoip /dbip-country-lite.mmdb /etc/caddy/dbip-country-lite.mmdb

# Persist Caddy state (certs, etc.) in container writable layer
ENV XDG_DATA_HOME=/data \
    XDG_CONFIG_HOME=/config

# Create the Caddyfile during build so no bind mounts are needed
RUN <<EOF
set -eu
cat > /etc/caddy/Caddyfile <<'CADDYFILE'
{
	acme_ca https://acme.zerossl.com/v2/DV90
	email {$CERT_EMAIL}

	acme_eab {
    	key_id {$ZEROSSL_EAB_KID}
    	mac_key {$ZEROSSL_EAB_HMAC}
  	}

	log default {
		output stdout
		format console
	}
}

{$PROXY_DOMAIN} {

	reverse_proxy /arrow.flight.protocol.FlightService/* {$INFLUX_TARGET} {
		transport http {
			versions h2c
		}
	}

	reverse_proxy /health {$INFLUX_TARGET} {
		header_up Host {host}
		header_up X-Forwarded-For {remote}
		header_up X-Forwarded-Proto {scheme}
	}

	reverse_proxy /query* {$INFLUX_TARGET} {
		header_up Host {host}
		header_up X-Forwarded-For {remote}
		header_up X-Forwarded-Proto {scheme}
	}

	reverse_proxy /api/v1/* {$INFLUX_TARGET}

	reverse_proxy /api/v2/* {$INFLUX_TARGET}

	reverse_proxy /api/v3/* {$INFLUX_TARGET} {
		header_up Host {host}
		header_up X-Forwarded-For {remote}
		header_up X-Forwarded-Proto {scheme}
	}

	reverse_proxy /metrics/* {$API_TARGET} {
		header_up Host {host}
		header_up X-Forwarded-For {remote}
		header_up X-Forwarded-Proto {scheme}
	}

	reverse_proxy /upload /upload/* {$API_TARGET} {
		header_up Host {host}
		header_up X-Forwarded-For {remote}
		header_up X-Forwarded-Proto {scheme}
	}

}

{$AI_DOMAIN} {
	reverse_proxy http://ai-assistant:3001
}

{$HA_DOMAIN} {
	# Local LAN and the VPN subnet are always trusted. Anything else must
	# also geolocate to Sweden -- this is the fallback path for e.g. a phone
	# on mobile data with the VPN off, not a replacement for the VPN.
	@denied {
		not remote_ip 192.168.68.0/24 10.8.0.0/24 185.176.29.48
		not maxmind_geolocation {
			db_path "/etc/caddy/dbip-country-lite.mmdb"
			allow_countries SE
		}
	}
	abort @denied

	header {
		# Apply after the upstream response headers are written, otherwise
		# Home Assistant's own X-Frame-Options / X-Content-Type-Options are
		# appended alongside ours and each is sent twice.
		defer

		X-Frame-Options "SAMEORIGIN"
		X-Content-Type-Options "nosniff"
		Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
		X-XSS-Protection "1; mode=block"
	}

	reverse_proxy http://host.docker.internal:8123
}
CADDYFILE
EOF

RUN ["caddy", "fmt", "/etc/caddy/Caddyfile", "--overwrite"]

# Expose HTTPS and HTTP
EXPOSE 443 80

# Default command
CMD [ "/bin/sh", "-c", "cat /etc/caddy/Caddyfile && caddy run --config /etc/caddy/Caddyfile --adapter caddyfile" ]
