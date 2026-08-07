################################################################################
#
# ippusb
#
# Driverless (IPP Everywhere) printing over USB. Source lives in-tree under
# package/ippusb/src — there is nothing to download.
#
################################################################################

IPPUSB_VERSION = 1.0
IPPUSB_SITE = $(TOPDIR)/package/ippusb/src
IPPUSB_SITE_METHOD = local
IPPUSB_LICENSE = GPL-2.0+
IPPUSB_DEPENDENCIES = cups libusb

# cups-config from staging carries the cross include/lib paths; libusb is a
# plain pkg-config dependency.
IPPUSB_CFLAGS = \
	$(TARGET_CFLAGS) -Wall -Wextra -Wno-unused-parameter -D_GNU_SOURCE \
	-I$(STAGING_DIR)/usr/include

IPPUSB_LDFLAGS = \
	$(TARGET_LDFLAGS) -L$(STAGING_DIR)/usr/lib -lusb-1.0 -lcups

define IPPUSB_BUILD_CMDS
	$(TARGET_CC) $(IPPUSB_CFLAGS) -o $(@D)/ippusb $(@D)/ippusb.c \
		$(IPPUSB_LDFLAGS)
endef

# cupsd runs a backend as root only when it is owned by root and not
# world-readable; 0700 is the mode the stock usb backend uses and is required
# here too, since claiming a USB interface needs privilege.
define IPPUSB_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0700 $(@D)/ippusb \
		$(TARGET_DIR)/usr/lib/cups/backend/ippusb
endef

$(eval $(generic-package))
