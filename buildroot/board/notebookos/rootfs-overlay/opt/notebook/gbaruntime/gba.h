/* gba.h — minimal Game Boy Advance hardware definitions for the Notebook OS
   GBA IDE runtime. Mode-3 bitmap rendering (240x160, 15-bit BGR555) with
   software sprite blitting, so a generated game needs no OBJ/tile/palette
   bookkeeping. */
#ifndef NB_GBA_H
#define NB_GBA_H

typedef unsigned char  u8;
typedef unsigned short u16;
typedef unsigned int   u32;
typedef signed short   s16;
typedef signed int     s32;

#define SCREEN_W 240
#define SCREEN_H 160

#define REG_DISPCNT (*(volatile u32*)0x04000000)
#define REG_VCOUNT  (*(volatile u16*)0x04000006)
#define REG_KEYINPUT (*(volatile u16*)0x04000130)

#define MODE3 0x0003
#define BG2_ON 0x0400

/* mode-3 framebuffer (legacy; the engine now renders in mode 0) */
#define VRAM ((volatile u16*)0x06000000)

/* ---- mode 0: tiled backgrounds + hardware OBJ sprites ---- */
#define MODE0        0x0000
#define BG0_ON       0x0100
#define BG1_ON       0x0200
#define OBJ_ON       0x1000
#define OBJ_1D_MAP   0x0040   /* OBJ tiles are stored linearly per sprite */

/* background control + scroll (BG0 is the tile layer) */
#define REG_BG0CNT  (*(volatile u16*)0x04000008)
#define REG_BG1CNT  (*(volatile u16*)0x0400000A)
#define REG_BG0HOFS (*(volatile u16*)0x04000010)
#define REG_BG0VOFS (*(volatile u16*)0x04000012)
#define REG_BG1HOFS (*(volatile u16*)0x04000014)
#define REG_BG1VOFS (*(volatile u16*)0x04000016)
/* BGxCNT: priority(0-1) | charblock(2-3) | mosaic(6) | 8bpp(7) |
   screenblock(8-12) | size(14-15). 4bpp size 3 = 64x64 tiles (512x512 px). */
#define BGCNT_CB(n)  ((n) << 2)
#define BGCNT_SB(n)  ((n) << 8)
#define BGCNT_4BPP   0x0000
#define BGCNT_SIZE(s) ((s) << 14)

/* palette memory: 256 BG entries then 256 OBJ entries (16 banks of 16 each) */
#define BG_PALETTE  ((volatile u16*)0x05000000)
#define OBJ_PALETTE ((volatile u16*)0x05000200)

/* VRAM tile/map blocks. Charblocks are 16 KiB (512 4bpp tiles); screenblocks
   are 2 KiB (a 32x32 map). OBJ tiles live at 0x06010000 (charblocks 4-5). */
#define CHARBLOCK(n)   ((volatile u16*)(0x06000000 + (n) * 0x4000))
#define SCREENBLOCK(n) ((volatile u16*)(0x06000000 + (n) * 0x0800))
#define OBJ_TILES      ((volatile u16*)0x06010000)

/* Object Attribute Memory: 128 sprites. attr0: y(0-7), mode(8-9), shape(14-15).
   attr1: x(0-8), size(14-15). attr2: tile(0-9), prio(10-11), pal bank(12-15). */
typedef struct { u16 attr0, attr1, attr2, fill; } OBJATTR;
#define OAM ((volatile OBJATTR*)0x07000000)
#define OBJ_HIDE     0x0200   /* attr0 mode bit: disable this object */
#define OBJ_SHAPE_SQ 0x0000
#define OBJ_SIZE(s)  ((u16)((s) << 14))
#define OBJ_TILE(n)  ((u16)((n) & 0x03FF))
#define OBJ_PALBANK(n) ((u16)((n) << 12))

/* BGR555 colour from 0..31 components */
#define RGB15(r, g, b) ((u16)((r) | ((g) << 5) | ((b) << 10)))
/* the sprite transparent colour key (magenta) — pixels of this value are not
   blitted, so sprites can have see-through areas without an alpha channel */
#define TRANSPARENT 0x7C1F

/* PSG sound registers */
#define REG_SOUND1CNT_L (*(volatile u16*)0x04000060)
#define REG_SOUND1CNT_H (*(volatile u16*)0x04000062)
#define REG_SOUND1CNT_X (*(volatile u16*)0x04000064)
#define REG_SOUND2CNT_L (*(volatile u16*)0x04000068)
#define REG_SOUND2CNT_H (*(volatile u16*)0x0400006C)
#define REG_SOUNDCNT_L  (*(volatile u16*)0x04000080)
#define REG_SOUNDCNT_H  (*(volatile u16*)0x04000082)
#define REG_SOUNDCNT_X  (*(volatile u16*)0x04000084)

/* key bits (KEYINPUT is active-low; the runtime inverts it) */
#define KEY_A      0x0001
#define KEY_B      0x0002
#define KEY_SELECT 0x0004
#define KEY_START  0x0008
#define KEY_RIGHT  0x0010
#define KEY_LEFT   0x0020
#define KEY_UP     0x0040
#define KEY_DOWN   0x0080
#define KEY_R      0x0100
#define KEY_L      0x0200

#endif
