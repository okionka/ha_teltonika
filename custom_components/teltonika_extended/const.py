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
KEY_BACKUP_STATUS = "backup_status"

# Service names
SERVICE_BACKUP  = "backup_config"
SERVICE_RESTORE = "restore_config"

# Backup storage path (relative to /config)
BACKUP_DIR = "teltonika_backups"

KEY_INTERFACES = "interfaces"

CONF_MAX_BACKUPS = "max_backups"
DEFAULT_MAX_BACKUPS = 5

CONF_SIDEBAR_PANEL = "sidebar_panel"
DEFAULT_SIDEBAR_PANEL = True

CONF_EXTERNAL_URL   = "external_url"
CONF_EXTERNAL_TITLE = "external_title"
CONF_EXTERNAL_ICON  = "external_icon"
CONF_EXTERNAL_PANEL = "external_panel"
DEFAULT_EXTERNAL_PANEL = False
DEFAULT_EXTERNAL_ICON  = "mdi:web"
