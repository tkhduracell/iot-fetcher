# syntax=docker/dockerfile:1.6
FROM caddy:2-alpine

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

ha.{$PROXY_DOMAIN} {
	@denied not remote_ip 192.168.68.0/24 185.176.29.48
	abort @denied

	header {
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
