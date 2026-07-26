################################################################################
#
# captdriver
#
################################################################################

CAPTDRIVER_VERSION = 62719249ac34633338be54bc74beddd0e7003d38
CAPTDRIVER_SITE = $(call github,mounaiban,captdriver,$(CAPTDRIVER_VERSION))
CAPTDRIVER_LICENSE = GPL-2.0
CAPTDRIVER_LICENSE_FILES = COPYING
CAPTDRIVER_DEPENDENCIES = cups
CAPTDRIVER_AUTORECONF = YES

# Use the staging cups-config (AC_PATH_PROG would otherwise pick up the host's
# and cross-compile against host CUPS). Install the filter into the CUPS filter
# dir via --bindir (upstream uses bin_PROGRAMS).
CAPTDRIVER_CONF_ENV = CUPS_CONFIG=$(STAGING_DIR)/usr/bin/cups-config
CAPTDRIVER_CONF_OPTS = --bindir=/usr/lib/cups/filter

# Upstream 'make install' installs only the rastertocapt filter; the .drv->PPD
# step is a separate manual target. Generate the Canon LBP PPDs from the shipped
# canon-lbp.drv with the host ppdc (plain arch-neutral text) and install them.
define CAPTDRIVER_INSTALL_PPDS
	cd $(@D) && LC_ALL=C PATH="/usr/bin:$$PATH" ppdc src/canon-lbp.drv -d ./ppd
	rm -f $(TARGET_DIR)/usr/share/Makefile.in
	mkdir -p $(TARGET_DIR)/usr/share/cups/model/captdriver
	cd $(@D)/ppd && for f in *.ppd; do \
		$(INSTALL) -m 0644 $$f $(TARGET_DIR)/usr/share/cups/model/captdriver/ || exit 1 ; \
	done
endef
CAPTDRIVER_POST_INSTALL_TARGET_HOOKS += CAPTDRIVER_INSTALL_PPDS

$(eval $(autotools-package))
