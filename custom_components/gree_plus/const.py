"""Constants for the Gree+ integration."""

from datetime import timedelta
import logging

DOMAIN = "gree_plus"
LOGGER = logging.getLogger(__package__)

CONF_REGION = "region"

# Units push a full status snapshot after every change, so polling is only a
# safety net (and how we notice a unit that has quietly gone away).
POLL_INTERVAL = timedelta(seconds=60)

# Minimum time between re-reading device keys from the cloud.
KEY_REFRESH_COOLDOWN = 300
