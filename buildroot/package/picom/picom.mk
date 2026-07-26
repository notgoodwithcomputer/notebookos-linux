################################################################################
#
# picom
#
################################################################################

PICOM_VERSION = v10.2
PICOM_SITE = $(call github,yshui,picom,$(PICOM_VERSION))
PICOM_LICENSE = MIT, MPL-2.0
PICOM_LICENSE_FILES = LICENSES/MIT LICENSES/MPL-2.0

# xrender backend (software rendering: no GL), no docs/dbus/regex to keep the
# dependency surface small on this offline appliance.
PICOM_DEPENDENCIES = host-pkgconf \
	libxcb xcb-util xcb-util-image xcb-util-renderutil \
	pixman libconfig libev uthash

PICOM_CONF_OPTS = \
	-Dwith_docs=false \
	-Dopengl=false \
	-Ddbus=false \
	-Dregex=false \
	-Dconfig_file=true

$(eval $(meson-package))
