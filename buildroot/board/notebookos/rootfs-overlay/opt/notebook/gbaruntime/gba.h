/* gba.h — Game Boy Advance hardware definitions for the Notebook OS GBA SDK
   runtime.

   The engine renders in mode 0: four tiled background layers plus 128 hardware
   OBJ sprites, so a generated game never touches a pixel itself. Layer use:

     BG1  priority 0   text / HUD (fixed to the screen)
     BG2  priority 0   dialogue-box and menu panels, behind the text
     OBJ  priority 1   sprites
     BG0  priority 2   the room's tile layer, hardware-scrolled by the camera
     BG3  priority 3   optional repeating parallax layer

   Sound uses all four PSG channels: square 1 + square 2 carry the music's lead
   and bass, the programmable wave channel plays tonal sound effects and the
   noise channel plays drums and percussive effects, so an effect never has to
   interrupt the music. */
#ifndef NB_GBA_H
#define NB_GBA_H

typedef unsigned char  u8;
typedef unsigned short u16;
typedef unsigned int   u32;
typedef signed char    s8;
typedef signed short   s16;
typedef signed int     s32;

#define SCREEN_W 240
#define SCREEN_H 160

#define REG_DISPCNT  (*(volatile u32*)0x04000000)
#define REG_DISPSTAT (*(volatile u16*)0x04000004)
#define REG_VCOUNT   (*(volatile u16*)0x04000006)
#define REG_KEYINPUT (*(volatile u16*)0x04000130)
/* Waitstate control: ROM prefetch + faster cartridge reads, and the SRAM
   access timing a save needs. */
#define REG_WAITCNT  (*(volatile u16*)0x04000204)
#define WAITCNT_FAST 0x4317   /* prefetch on, 3/1 ROM waits, safe 8-cycle SRAM */

#define MODE3 0x0003
#define BG2_ON 0x0400

/* mode-3 framebuffer (legacy; the engine now renders in mode 0) */
#define VRAM ((volatile u16*)0x06000000)

/* ---- mode 0: tiled backgrounds + hardware OBJ sprites ---- */
#define MODE0        0x0000
#define FORCE_BLANK  0x0080   /* blank the display while VRAM is being rebuilt */
#define BG0_ON       0x0100
#define BG1_ON       0x0200
#define BG3_ON       0x0800
#define OBJ_ON       0x1000
#define OBJ_1D_MAP   0x0040   /* OBJ tiles are stored linearly per sprite */

/* background control + scroll */
#define REG_BG0CNT  (*(volatile u16*)0x04000008)
#define REG_BG1CNT  (*(volatile u16*)0x0400000A)
#define REG_BG2CNT  (*(volatile u16*)0x0400000C)
#define REG_BG3CNT  (*(volatile u16*)0x0400000E)
#define REG_BG0HOFS (*(volatile u16*)0x04000010)
#define REG_BG0VOFS (*(volatile u16*)0x04000012)
#define REG_BG1HOFS (*(volatile u16*)0x04000014)
#define REG_BG1VOFS (*(volatile u16*)0x04000016)
#define REG_BG2HOFS (*(volatile u16*)0x04000018)
#define REG_BG2VOFS (*(volatile u16*)0x0400001A)
#define REG_BG3HOFS (*(volatile u16*)0x0400001C)
#define REG_BG3VOFS (*(volatile u16*)0x0400001E)
/* BGxCNT: priority(0-1) | charblock(2-3) | mosaic(6) | 8bpp(7) |
   screenblock(8-12) | size(14-15). 4bpp size 3 = 64x64 tiles (512x512 px). */
#define BGCNT_CB(n)  ((n) << 2)
#define BGCNT_SB(n)  ((n) << 8)
#define BGCNT_4BPP   0x0000
#define BGCNT_SIZE(s) ((s) << 14)

/* colour special effects: a whole-screen fade to black or white */
#define REG_BLDCNT   (*(volatile u16*)0x04000050)
#define REG_BLDALPHA (*(volatile u16*)0x04000052)
#define REG_BLDY     (*(volatile u16*)0x04000054)
#define BLD_ALL      0x003F   /* every layer + the backdrop as 1st target */
#define BLD_WHITE    0x0080   /* mode 2: brightness increase */
#define BLD_BLACK    0x00C0   /* mode 3: brightness decrease */

/* DMA channel 3 — general-purpose memory copy (OAM flush, VRAM uploads) */
#define REG_DMA3SAD (*(volatile u32*)0x040000D4)
#define REG_DMA3DAD (*(volatile u32*)0x040000D8)
#define REG_DMA3CNT (*(volatile u32*)0x040000DC)
#define DMA_ENABLE  0x80000000
#define DMA_32      0x04000000

/* palette memory: 256 BG entries then 256 OBJ entries (16 banks of 16 each) */
#define BG_PALETTE  ((volatile u16*)0x05000000)
#define OBJ_PALETTE ((volatile u16*)0x05000200)

/* VRAM tile/map blocks. Charblocks are 16 KiB (512 4bpp tiles); screenblocks
   are 2 KiB (a 32x32 map). OBJ tiles live at 0x06010000 (charblocks 4-5). */
#define CHARBLOCK(n)   ((volatile u16*)(0x06000000 + (n) * 0x4000))
#define SCREENBLOCK(n) ((volatile u16*)(0x06000000 + (n) * 0x0800))
#define OBJ_TILES      ((volatile u16*)0x06010000)

/* Object Attribute Memory: 128 sprites. attr0: y(0-7), affine(8), hide/dbl(9),
   mode(10-11), mosaic(12), 8bpp(13), shape(14-15). attr1: x(0-8), affine
   index(9-13) or hflip(12)/vflip(13), size(14-15). attr2: tile(0-9),
   priority(10-11), palette bank(12-15). The 4th halfword of every 4th entry
   holds one term of an affine matrix. */
typedef struct { u16 attr0, attr1, attr2, fill; } OBJATTR;
#define OAM ((volatile OBJATTR*)0x07000000)
#define OBJ_HIDE     0x0200   /* attr0 mode bit: disable this object */
#define OBJ_AFFINE   0x0100   /* attr0: use an affine matrix (rotate / scale) */
#define OBJ_DBLSIZE  0x0200   /* attr0, with OBJ_AFFINE: 2x bounding box */
#define OBJ_SHAPE_SQ 0x0000
#define OBJ_SIZE(s)  ((u16)((s) << 14))
#define OBJ_TILE(n)  ((u16)((n) & 0x03FF))
#define OBJ_PALBANK(n) ((u16)((n) << 12))
#define OBJ_PRIO(n)  ((u16)(((n) & 3) << 10))
#define OBJ_HFLIP    0x1000
#define OBJ_VFLIP    0x2000
#define OBJ_AFFINE_IX(n) ((u16)(((n) & 31) << 9))

/* BGR555 colour from 0..31 components */
#define RGB15(r, g, b) ((u16)((r) | ((g) << 5) | ((b) << 10)))
/* the sprite transparent colour key (magenta) — pixels of this value are not
   blitted, so sprites can have see-through areas without an alpha channel */
#define TRANSPARENT 0x7C1F

/* ---- PSG sound registers ----
   1 = square with sweep, 2 = square, 3 = programmable wave, 4 = noise. */
#define REG_SOUND1CNT_L (*(volatile u16*)0x04000060)
#define REG_SOUND1CNT_H (*(volatile u16*)0x04000062)
#define REG_SOUND1CNT_X (*(volatile u16*)0x04000064)
#define REG_SOUND2CNT_L (*(volatile u16*)0x04000068)
#define REG_SOUND2CNT_H (*(volatile u16*)0x0400006C)
#define REG_SOUND3CNT_L (*(volatile u16*)0x04000070)
#define REG_SOUND3CNT_H (*(volatile u16*)0x04000072)
#define REG_SOUND3CNT_X (*(volatile u16*)0x04000074)
#define REG_SOUND4CNT_L (*(volatile u16*)0x04000078)
#define REG_SOUND4CNT_H (*(volatile u16*)0x0400007C)
#define REG_SOUNDCNT_L  (*(volatile u16*)0x04000080)
#define REG_SOUNDCNT_H  (*(volatile u16*)0x04000082)
#define REG_SOUNDCNT_X  (*(volatile u16*)0x04000084)
/* channel 3's waveform: 16 bytes = 32 four-bit samples, per bank */
#define WAVE_RAM        ((volatile u16*)0x04000090)

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
#define KEY_ANY    0x03FF

#endif
