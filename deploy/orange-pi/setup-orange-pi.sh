#!/bin/sh

set -eu

SOURCE_BINARY="${1:-./paradosp}"
INSTALL_DIR="/opt/paradosp"
CONFIG_DIR="/etc/paradosp"
STATE_DIR="/var/lib/paradosp"
CONFIG_FILE="${CONFIG_DIR}/paradosp.conf"
SERVICE_FILE="/etc/systemd/system/paradosp.service"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo sh setup-orange-pi.sh ./paradosp" >&2
    exit 1
fi

if [ "$(uname -m)" != "aarch64" ]; then
    echo "This package requires a 64-bit ARM OS (uname -m must be aarch64)." >&2
    exit 1
fi

if [ ! -f "${SOURCE_BINARY}" ]; then
    echo "Cannot find paradosp file: ${SOURCE_BINARY}" >&2
    exit 1
fi

install -d -m 0755 "${INSTALL_DIR}" "${CONFIG_DIR}" "${STATE_DIR}"
install -m 0755 "${SOURCE_BINARY}" "${INSTALL_DIR}/paradosp.new"
mv -f "${INSTALL_DIR}/paradosp.new" "${INSTALL_DIR}/paradosp"

if [ ! -f "${CONFIG_FILE}" ]; then
    printf '%s\n' \
        'SAFELINK_CRAWL_URL = "https://safelinksystem.app/core/webhook/ultrasync/crawl"' \
        > "${CONFIG_FILE}"
fi
chmod 0666 "${CONFIG_FILE}"

cat > "${SERVICE_FILE}" <<'EOF'
[Unit]
Description=Paradox SP4000 SafeLink gateway
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/lib/paradosp
Environment=HOME=/var/lib/paradosp
Environment=XDG_CACHE_HOME=/var/lib/paradosp/.cache
ExecStart=/opt/paradosp/paradosp --config /etc/paradosp/paradosp.conf
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

HOME="${STATE_DIR}" XDG_CACHE_HOME="${STATE_DIR}/.cache" \
    "${INSTALL_DIR}/paradosp" --self-test

systemctl daemon-reload
systemctl enable paradosp.service
systemctl restart paradosp.service

echo "paradosp installed and started."
echo "Config: ${CONFIG_FILE}"
echo "API: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8082"
systemctl --no-pager --full status paradosp.service || true
