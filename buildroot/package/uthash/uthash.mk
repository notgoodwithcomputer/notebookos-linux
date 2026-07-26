################################################################################
#
# uthash
#
################################################################################

UTHASH_VERSION = 2.3.0
UTHASH_SITE = $(call github,troydhanson,uthash,v$(UTHASH_VERSION))
UTHASH_LICENSE = BSD-1-Clause
UTHASH_LICENSE_FILES = LICENSE
UTHASH_INSTALL_STAGING = YES
UTHASH_INSTALL_TARGET = NO

# header-only: just drop the headers into the staging sysroot
define UTHASH_INSTALL_STAGING_CMDS
	$(INSTALL) -d $(STAGING_DIR)/usr/include
	$(INSTALL) -m 0644 $(@D)/src/uthash.h $(@D)/src/utlist.h \
		$(@D)/src/utarray.h $(@D)/src/utstring.h \
		$(@D)/src/utringbuffer.h $(@D)/src/utstack.h \
		$(STAGING_DIR)/usr/include/
endef

$(eval $(generic-package))
