################################################################################
#
# brlaser
#
################################################################################

BRLASER_VERSION = 6
BRLASER_SITE = $(call github,pdewacht,brlaser,v$(BRLASER_VERSION))
BRLASER_LICENSE = GPL-2.0+
BRLASER_LICENSE_FILES = COPYING
BRLASER_DEPENDENCIES = cups

# cups-config from staging gives the cross cflags/libs; pin the install dirs to
# clean target paths so the filter + PPDs land in /usr/lib/cups and
# /usr/share/cups (not the staging paths cups-config would otherwise report).
BRLASER_CONF_OPTS = \
	-DCUPS_CONFIG=$(STAGING_DIR)/usr/bin/cups-config \
	-DCUPS_SERVER_BIN=/usr/lib/cups \
	-DCUPS_DATA_DIR=/usr/share/cups

$(eval $(cmake-package))
