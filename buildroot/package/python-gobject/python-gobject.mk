################################################################################
#
# python-gobject
#
################################################################################

PYTHON_GOBJECT_VERSION_MAJOR = 3.42
PYTHON_GOBJECT_VERSION = $(PYTHON_GOBJECT_VERSION_MAJOR).2
PYTHON_GOBJECT_SOURCE = pygobject-$(PYTHON_GOBJECT_VERSION).tar.xz
PYTHON_GOBJECT_SITE = https://download.gnome.org/sources/pygobject/$(PYTHON_GOBJECT_VERSION_MAJOR)
PYTHON_GOBJECT_LICENSE = LGPL-2.1+
PYTHON_GOBJECT_LICENSE_FILES = COPYING
PYTHON_GOBJECT_INSTALL_STAGING = YES
PYTHON_GOBJECT_DEPENDENCIES = \
	gobject-introspection \
	host-pkgconf \
	libglib2 \
	python3 \
	python-pycairo \
	cairo

# The gi-cairo foreign bridge (gi._gi_cairo) is REQUIRED: without it, GTK's
# "draw" signal cannot marshal its cairo.Context to Python handlers, so every
# Gtk.DrawingArea renders blank (games, illustrator, sequencer, media, etc.).
# Buildroot's python-pycairo installs py3cairo.h / py3cairo.pc only to target,
# not staging, so we stage them here (exactly as pycairo's setup.py would) and
# enable -Dpycairo. Header name/path and .pc Cflags mirror pycairo upstream.
define PYTHON_GOBJECT_STAGE_PYCAIRO
	$(INSTALL) -d $(STAGING_DIR)/usr/include/pycairo
	$(INSTALL) -m 0644 $(PYTHON_PYCAIRO_DIR)/cairo/pycairo.h \
		$(STAGING_DIR)/usr/include/pycairo/py3cairo.h
	$(INSTALL) -d $(STAGING_DIR)/usr/lib/pkgconfig
	printf 'prefix=/usr\nName: Pycairo\nDescription: Python 3 bindings for cairo\nVersion: %s\nCflags: -I$${prefix}/include/pycairo\nLibs:\n' \
		'$(PYTHON_PYCAIRO_VERSION)' \
		> $(STAGING_DIR)/usr/lib/pkgconfig/py3cairo.pc
endef
PYTHON_GOBJECT_PRE_CONFIGURE_HOOKS += PYTHON_GOBJECT_STAGE_PYCAIRO

PYTHON_GOBJECT_CONF_OPTS += \
	-Dpycairo=enabled \
	-Dtests=false

# A sysconfigdata_name must be manually specified or the resulting .so
# will have a x86_64 prefix, which causes "import gi" to fail.
# A pythonpath must be specified or the host python path will be used resulting
# in a "not a valid python" error.
PYTHON_GOBJECT_CONF_ENV += \
	_PYTHON_SYSCONFIGDATA_NAME=$(PKG_PYTHON_SYSCONFIGDATA_NAME) \
	PYTHONPATH=$(PYTHON3_PATH)

$(eval $(meson-package))
