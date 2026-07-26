################################################################################
#
# vbam
#
################################################################################

VBAM_VERSION = 2.1.4
VBAM_SITE = $(call github,visualboyadvance-m,visualboyadvance-m,v$(VBAM_VERSION))
VBAM_LICENSE = GPL-2.0+
VBAM_LICENSE_FILES = COPYING
VBAM_DEPENDENCIES = sdl2 libpng zlib libgl libglu host-pkgconf

# The SDL frontend only ("vbam"); no wxWidgets GUI, no link cable (SFML),
# no ffmpeg recording, no x86 ASM cores (portable C core), no NLS.
VBAM_CONF_OPTS = \
	-DCMAKE_BUILD_TYPE=Release \
	-DENABLE_SDL=ON \
	-DENABLE_WX=OFF \
	-DENABLE_LINK=OFF \
	-DENABLE_FFMPEG=OFF \
	-DENABLE_LIRC=OFF \
	-DENABLE_NLS=OFF \
	-DENABLE_DEBUGGER=ON \
	-DENABLE_ASM_CORE=OFF \
	-DENABLE_ASM_SCALERS=OFF \
	-DENABLE_MMX=OFF \
	-DENABLE_LTO=OFF \
	-DVBAM_STATIC=OFF

# Two in-tree fixups before configuring:
#  * VBA-M 2.1.4's SDL frontend uses the SDL1-era modifier name KMOD_META,
#    which SDL2 renamed to KMOD_GUI.
#  * CMakeLists hardcodes -fopenmp for GCC; this toolchain has no libgomp, so
#    strip it. The OpenMP pragmas simply run serially without it.
define VBAM_SDL2_COMPAT
	$(SED) 's/KMOD_META/KMOD_GUI/g' $(@D)/src/sdl/SDL.cpp
	$(SED) '/-fopenmp/d' $(@D)/CMakeLists.txt
endef
VBAM_POST_PATCH_HOOKS += VBAM_SDL2_COMPAT

$(eval $(cmake-package))
