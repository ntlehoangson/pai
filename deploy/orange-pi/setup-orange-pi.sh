#!/bin/sh

set -eu

SOURCE_BINARY="${1:-./paradosp}"
INSTALL_DIR="/opt/paradosp"
CONFIG_DIR="/etc/paradosp"
STATE_DIR="/var/lib/paradosp"
LOG_DIR="/var/log/paradosp"
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

install -d -m 0755 "${INSTALL_DIR}" "${CONFIG_DIR}" "${STATE_DIR}" "${LOG_DIR}"
install -m 0755 "${SOURCE_BINARY}" "${INSTALL_DIR}/paradosp.new"
mv -f "${INSTALL_DIR}/paradosp.new" "${INSTALL_DIR}/paradosp"

if [ ! -f "${CONFIG_FILE}" ]; then
    printf '%s\n' \
        'CONNECTION_TYPE = "Serial"' \
        'SERIAL_PORT = "/dev/ttyS1"' \
        'SERIAL_BAUD = 9600' \
        'SERIAL_ENCRYPTED = False' \
        'PASSWORD = "0000"' \
        'SAFELINK_CRAWL_URL = "https://safelinksystem.app/core/webhook/ultrasync/crawl"' \
        'LOGGING_LEVEL_FILE = 20' \
        'LOGGING_FILE = "/var/log/paradosp/paradosp.log"' \
        'LOGGING_FILE_ROTATION = "daily"' \
        'LOGGING_FILE_COMPRESS = True' \
        'LOGGING_FILE_MAX_FILES = 30' \
        > "${CONFIG_FILE}"
fi

# Upgrade older installations without overwriting operator-owned values.
if ! grep -Eq '^[[:space:]]*LOGGING_FILE[[:space:]]*=' "${CONFIG_FILE}"; then
    printf '%s\n' 'LOGGING_FILE = "/var/log/paradosp/paradosp.log"' >> "${CONFIG_FILE}"
fi
if ! grep -Eq '^[[:space:]]*LOGGING_LEVEL_FILE[[:space:]]*=' "${CONFIG_FILE}"; then
    printf '%s\n' 'LOGGING_LEVEL_FILE = 20' >> "${CONFIG_FILE}"
fi
if ! grep -Eq '^[[:space:]]*LOGGING_FILE_ROTATION[[:space:]]*=' "${CONFIG_FILE}"; then
    printf '%s\n' 'LOGGING_FILE_ROTATION = "daily"' >> "${CONFIG_FILE}"
fi
if ! grep -Eq '^[[:space:]]*LOGGING_FILE_COMPRESS[[:space:]]*=' "${CONFIG_FILE}"; then
    printf '%s\n' 'LOGGING_FILE_COMPRESS = True' >> "${CONFIG_FILE}"
fi
if ! grep -Eq '^[[:space:]]*LOGGING_FILE_MAX_FILES[[:space:]]*=' "${CONFIG_FILE}"; then
    printf '%s\n' 'LOGGING_FILE_MAX_FILES = 30' >> "${CONFIG_FILE}"
fi
chmod 0600 "${CONFIG_FILE}"

if [ ! -c /dev/ttyS1 ]; then
    echo "WARNING: /dev/ttyS1 is not available. Enable overlays=uart5 and reboot before starting paradosp." >&2
fi

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
