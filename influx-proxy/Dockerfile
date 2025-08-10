# syntax=docker/dockerfile:1.6
FROM caddy:2-alpine

# Build args for templating the Caddyfile
ARG DOMAIN
ARG EMAIL

# Persist Caddy state (certs, etc.) in container writable layer
ENV XDG_DATA_HOME=/data \
    XDG_CONFIG_HOME=/config

# Create the Caddyfile during build so no bind mounts are needed
RUN <<EOF
set -eu
cat > /etc/caddy/Caddyfile <<'CADDY'
{
	email EMAIL_PLACEHOLDER
}

DOMAIN_PLACEHOLDER {
	encode gzip zstd
	log {
		output stdout
		format console
	}
	reverse_proxy http://database:8086 {
		header_up Host {host}
		header_up X-Forwarded-For {remote}
		header_up X-Forwarded-Proto {scheme}
	}
}
CADDY
# Replace placeholders with build args without risking shell expansion issues
sed -i "s|EMAIL_PLACEHOLDER|${EMAIL}|g" /etc/caddy/Caddyfile
sed -i "s|DOMAIN_PLACEHOLDER|${DOMAIN}|g" /etc/caddy/Caddyfile
EOF

# Expose HTTPS and HTTP
EXPOSE 443 80

# Default command
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
