#!/usr/bin/env bash
# Sourced by the vm-*.sh helpers to resolve VictoriaMetrics credentials.
#
# Exports:
#   VM_BASE_URL  base URL of the VM API (no trailing slash)
#   VM_TOKEN     bearer token
#
# Each value is resolved from, in order:
#   1. An already-set environment variable — so the scripts also work in
#      containers, CI, and fresh clones that have no .env files at all.
#   2. The first candidate .env file that defines the key.
#
# .env files are not committed, so in a git worktree they only exist in the main
# worktree root. git-common-dir is shared across all worktrees; its parent is
# the canonical checkout.

_vm_env_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if _vm_common_dir="$(git -C "$_vm_env_script_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
  VM_ENV_DIR="$(cd "$_vm_common_dir/.." && pwd)"
else
  VM_ENV_DIR="$(cd "$_vm_env_script_dir/.." && pwd)"
fi

_vm_python_envs=(
  "$VM_ENV_DIR/fetcher-core/python/.env.local"
  "$VM_ENV_DIR/fetcher-core/python/.env"
)
_vm_proxy_envs=(
  "$VM_ENV_DIR/https-proxy/.env.local"
  "$VM_ENV_DIR/https-proxy/.env"
)

# _vm_read_env <key> <file>...  — echo the first non-empty value found, else nothing.
_vm_read_env() {
  local key="$1"; shift
  local file value
  for file in "$@"; do
    [[ -f "$file" ]] || continue
    value="$(grep -m1 "^${key}=" "$file" | cut -d'=' -f2-)"
    # Strip surrounding quotes, which are legal in .env files.
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    if [[ -n "$value" ]]; then
      printf '%s\n' "$value"
      return 0
    fi
  done
  return 0
}

VM_TOKEN="${INFLUX_TOKEN:-$(_vm_read_env INFLUX_TOKEN "${_vm_python_envs[@]}")}"

if [[ -z "${VM_BASE_URL:-}" ]]; then
  _vm_domain="${PROXY_DOMAIN:-$(_vm_read_env PROXY_DOMAIN "${_vm_proxy_envs[@]}")}"
  if [[ -n "$_vm_domain" ]]; then
    VM_BASE_URL="https://${_vm_domain}"
  else
    _vm_host="${INFLUX_HOST:-$(_vm_read_env INFLUX_HOST "${_vm_python_envs[@]}")}"
    VM_BASE_URL="${_vm_host%/}"
  fi
else
  VM_BASE_URL="${VM_BASE_URL%/}"
fi

if [[ -z "$VM_BASE_URL" || -z "$VM_TOKEN" ]]; then
  {
    echo "Error: could not resolve VictoriaMetrics credentials."
    echo
    [[ -z "$VM_BASE_URL" ]] && echo "  missing endpoint — set VM_BASE_URL, PROXY_DOMAIN, or INFLUX_HOST"
    [[ -z "$VM_TOKEN" ]] && echo "  missing token    — set INFLUX_TOKEN"
    echo
    echo "Either export them, or provide them in one of:"
    printf '  %s\n' "${_vm_python_envs[@]}" "${_vm_proxy_envs[@]}"
    echo
    echo "Note: .env files are not committed, so a fresh clone has none of them."
  } >&2
  exit 1
fi

export VM_BASE_URL VM_TOKEN
