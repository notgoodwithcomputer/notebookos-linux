################################################################################
#
# splix
#
################################################################################

SPLIX_VERSION = 9bde257882a1ebcf15a97ff4685d34053383e3f8
SPLIX_SITE = $(call github,OpenPrinting,splix,$(SPLIX_VERSION))
SPLIX_LICENSE = GPL-2.0+
SPLIX_LICENSE_FILES = COPYING
SPLIX_DEPENDENCIES = cups jbigkit zlib

# splix hardcodes CC/CXX=gcc. Override only the toolchain via make args (NOT
# CFLAGS/CXXFLAGS, which the Makefile builds up with += from pkg-config and
# must not be clobbered). DISABLE_JBIG=0 links -ljbig85 (from jbigkit) so the
# QPDL raster is compressed the way the printers expect.
SPLIX_MAKE_OPTS = \
	CC="$(TARGET_CC)" \
	CXX="$(TARGET_CXX)" \
	LINKER="$(TARGET_CXX)" \
	DISABLE_JBIG=0 \
	V=1

# The repo commits only a subset of English/French PPDs and its install loop
# expects pt_BR variants that were never generated. Instead we regenerate every
# model's English base PPD from the .drv.in templates with the host ppdc (PPDs
# are plain, arch-neutral text) and install them ourselves -- no dependency on
# the brittle upstream install target, and no recode/localization needed.
define SPLIX_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) $(SPLIX_MAKE_OPTS) -C $(@D)
	chmod +x $(@D)/ppd/compile.sh
	cd $(@D)/ppd && for drv in samsung dell xerox lexmark toshiba hp; do \
		PATH="/usr/bin:$$PATH" ./compile.sh $$drv.drv.in -I . -d ./ || exit 1 ; \
	done
endef

define SPLIX_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/optimized/rastertoqpdl \
		$(TARGET_DIR)/usr/lib/cups/filter/rastertoqpdl
	$(INSTALL) -D -m 0755 $(@D)/optimized/pstoqpdl \
		$(TARGET_DIR)/usr/lib/cups/filter/pstoqpdl
	mkdir -p $(TARGET_DIR)/usr/share/cups/model/splix
	cd $(@D)/ppd && for f in $$(ls *.ppd | \
		grep -vE 'fr\.ppd$$|pt\.ppd$$|pt_BR\.ppd$$'); do \
		$(INSTALL) -m 0644 $$f $(TARGET_DIR)/usr/share/cups/model/splix/ || exit 1 ; \
	done
endef

$(eval $(generic-package))
