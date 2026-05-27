"""Constants for Teltonika Extended integration."""

DOMAIN = "teltonika_extended"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_SCAN_INTERVAL = 30

# Coordinator data keys
KEY_SYSTEM     = "system"
KEY_MOBILE     = "mobile"
KEY_GPS        = "gps"
KEY_WAN        = "wan"
KEY_DATA_USAGE = "data_usage"
KEY_WIRELESS   = "wireless"
KEY_FIRMWARE   = "firmware"

# Service names
SERVICE_BACKUP  = "backup_config"
SERVICE_RESTORE = "restore_config"

# Backup storage path (relative to /config)
BACKUP_DIR = "teltonika_backups"
