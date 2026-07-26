################################################################################
#
# jbigkit
#
################################################################################

JBIGKIT_VERSION = 2.1
JBIGKIT_SITE = https://www.cl.cam.ac.uk/~mgk25/jbigkit/download
JBIGKIT_LICENSE = GPL-2.0+
JBIGKIT_LICENSE_FILES = COPYING
JBIGKIT_INSTALL_STAGING = YES
# Static-only helper library consumed at build time (e.g. by splix); nothing
# needs to land on the target.
JBIGKIT_INSTALL_TARGET = NO

# The upstream Makefile hardcodes host "ar"/"ranlib" in its archive rules, so
# compile just the objects with the cross toolchain and build the archives
# ourselves with $(TARGET_AR). This also skips the host-run test programs.
define JBIGKIT_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) $(TARGET_CONFIGURE_OPTS) -C $(@D)/libjbig \
		jbig.o jbig85.o jbig_ar.o
	cd $(@D)/libjbig && $(TARGET_AR) rcs libjbig.a jbig.o jbig_ar.o
	cd $(@D)/libjbig && $(TARGET_AR) rcs libjbig85.a jbig85.o jbig_ar.o
endef

define JBIGKIT_INSTALL_STAGING_CMDS
	$(INSTALL) -D -m 0644 $(@D)/libjbig/libjbig.a $(STAGING_DIR)/usr/lib/libjbig.a
	$(INSTALL) -D -m 0644 $(@D)/libjbig/libjbig85.a $(STAGING_DIR)/usr/lib/libjbig85.a
	$(INSTALL) -D -m 0644 $(@D)/libjbig/jbig.h $(STAGING_DIR)/usr/include/jbig.h
	$(INSTALL) -D -m 0644 $(@D)/libjbig/jbig85.h $(STAGING_DIR)/usr/include/jbig85.h
	$(INSTALL) -D -m 0644 $(@D)/libjbig/jbig_ar.h $(STAGING_DIR)/usr/include/jbig_ar.h
endef

$(eval $(generic-package))
