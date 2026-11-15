#!/usr/bin/env sh
# Generate a self-signed TLS certificate for the local Docker stack.
#
#   scripts/gen-certs.sh            # writes deploy/certs/localhost.{crt,key} if missing
#   scripts/gen-certs.sh --force    # regenerate
#
# The certificate is for localhost and 127.0.0.1 only and is valid for 365
# days. Browsers will warn until you trust it; never use it outside a laptop.
# The files are git-ignored.
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
out="$here/deploy/certs"
mkdir -p "$out"

if [ "${1:-}" != "--force" ] && [ -f "$out/localhost.crt" ] && [ -f "$out/localhost.key" ]; then
  echo "certificates already exist in $out (use --force to regenerate)"
  exit 0
fi

command -v openssl >/dev/null 2>&1 || { echo "openssl is required" >&2; exit 1; }

# MSYS (Git Bash on Windows) rewrites arguments that start with "/"; the
# subject must reach openssl unchanged, so path conversion is off and the
# output files are named relative to the certificate folder.
cd "$out"
MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
  -keyout localhost.key -out localhost.crt 2>/dev/null

chmod 644 "$out/localhost.crt"
# nginx loads the key in its root master process, so owner-only is enough.
chmod 600 "$out/localhost.key"
echo "wrote $out/localhost.crt and $out/localhost.key"
