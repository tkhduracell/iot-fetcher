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

	log default {
		output stdout
		format console
	}
}

{$PROXY_DOMAIN} {

	reverse_proxy /api/v2/* {$INFLUX_TARGET} {
		header_up Host {host}
		header_up X-Forwarded-For {remote}
		header_up X-Forwarded-Proto {scheme}
	}

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
CADDYFILE
EOF

RUN ["caddy", "fmt", "/etc/caddy/Caddyfile", "--overwrite"]

# Expose HTTPS and HTTP
EXPOSE 443 80

# Default command
CMD [ "/bin/sh", "-c", "cat /etc/caddy/Caddyfile && caddy run --config /etc/caddy/Caddyfile --adapter caddyfile" ]
