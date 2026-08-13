/* runtime.c — the Notebook OS GBA SDK game engine.

   A mode-0 hardware renderer (a streaming tile layer, a repeating parallax
   layer, 128 OBJ sprites and a text/HUD layer) plus a Game-Maker-style
   instance/event loop, tile collision, a four-channel PSG sound engine and
   battery-backed saving.

   Frame shape: everything that touches OAM or a scroll register happens in the
   few hundred microseconds right after VBlank starts, from a shadow copy in
   IWRAM flushed by DMA, so sprites can never tear or flicker no matter how long
   the game's own logic takes. */
#include "runtime.h"
#include "font.h"

static Instance g_inst[NB_MAX_INSTANCES];
static u16 g_keys, g_keys_prev;
static s16 g_cur_room = -1;
static s16 g_next_room = -1;
/* Where a warp puts the traveller in the room it opens into. -1 means "wherever
   the room places it", which is what a plain rt_room_goto does. */
static s32 g_arrive_x = -1, g_arrive_y = -1;
static u16 g_step_frames = 1;    /* VBlanks between game steps (room speed) */
static const nb_Room* g_room = 0;
static Instance* g_view = 0;     /* the camera follows this instance (0 = fixed) */
static Instance* g_other = 0;
static u8 g_death_fired = 0;   /* on_no_health latch: re-arms when health rises */    /* what the most recent collision test found */

/* global game state (Game Maker score/lives/health) + persistent globals */
s32 nb_score = 0, nb_lives = 3, nb_health = 100;
s32 nb_global[NB_MAX_GLOBALS];
extern const int nb_save_type;   /* 0 SRAM, 1 Flash 64K, 2 Flash 128K -- the generator decides */
/* The save-type signature lives in the GENERATED game now (nb_save_sig):
   emulators and flash carts size the battery by scanning the ROM for it,
   and exactly one may exist -- a runtime-baked SRAM string next to a
   generator-chosen FLASH one would leave detection to scan order. */

/* ---- fast memory copy (DMA channel 3) ---- */
static void dma_copy32(volatile void* dst, const void* src, int words) {
    if (words <= 0) return;
    REG_DMA3CNT = 0;
    REG_DMA3SAD = (u32)src;
    REG_DMA3DAD = (u32)dst;
    REG_DMA3CNT = DMA_ENABLE | DMA_32 | (u32)words;
}

static void dma_copy16(volatile void* dst, const void* src, int halfwords) {
    if (halfwords <= 0) return;
    REG_DMA3CNT = 0;
    REG_DMA3SAD = (u32)src;
    REG_DMA3DAD = (u32)dst;
    REG_DMA3CNT = DMA_ENABLE | (u32)halfwords;
}

/* ---- persistent save -----------------------------------------------------
 * One 8-bit port at 0x0E000000, three parts that can sit behind it. The
 * GENERATOR decides which (nb_save_type): 0 = SRAM 32K, 1 = Flash 64K,
 * 2 = Flash 128K -- and emits the matching signature string, so the emulator
 * or flash cart provisions the same part the code drives.
 *
 * SRAM is plain byte writes. Flash is a command protocol at two magic
 * addresses: bytes can only be PROGRAMMED 1->0, so rewriting anything means
 * erasing its 4 KiB sector back to 0xFF first, and every program/erase is
 * finished by polling until the data reads back (DQ7). The whole save block
 * lives in sector 0, which makes "rewrite" one erase -- but it also means the
 * high score shares the sector, so both writers preserve the other's value
 * across the erase. On the 128K part the port is banked; the save block stays
 * in bank 0 and save_init() pins it there at boot. */
#define SAVE_PORT   ((volatile u8*)0x0E000000)
#define FLASH_A     (*(volatile u8*)0x0E005555)
#define FLASH_B     (*(volatile u8*)0x0E002AAA)
#define SAVE_MAGIC  0x42474D31   /* '1MGB' */
#define SAVE_HISCORE (16 + NB_MAX_GLOBALS * 4)   /* high score lives after the globals */

/* ---- EEPROM ---------------------------------------------------------------
 * The third save part, and the one that shares nothing with the other two.
 * SRAM and flash are memory you address a byte at a time; EEPROM is a SERIAL
 * device behind a one-bit port at 0x0D000000, and the only legal way to talk
 * to it is DMA3 -- the CPU cannot clock it, because the transfer has to be
 * uninterrupted. Every halfword sent carries ONE bit in its low bit.
 *
 * It also has no byte writes at all: the unit is an 8-byte BLOCK. So this
 * keeps the whole save block in a RAM shadow, serves reads and writes from
 * there, and commits complete blocks -- which is why save_commit() exists and
 * does nothing for the other two parts.
 *
 * The address is 6 bits on the 512-byte part and 14 on the 8 KB one. Sending
 * the wrong width is not an error the device reports: it stores the data
 * somewhere else and reads back rubbish, so the width comes from the same
 * generator-chosen nb_save_type that picks the signature. */
#define EEPROM_PORT ((volatile u16*)0x0D000000)
#define SAVE_BYTES  (SAVE_HISCORE + 4)
#define EE_BLOCKS   ((SAVE_BYTES + 7) / 8)

static u8 g_ee_shadow[EE_BLOCKS * 8] __attribute__((aligned(4)));
static u8 g_ee_loaded;          /* the shadow holds what the device holds */
static u8 g_ee_dirty[EE_BLOCKS]; /* blocks whose bytes actually changed */
static u16 g_ee_bits[81];       /* the longest request: 2 + 14 + 64 + 1 */

static int ee_addr_bits(void) { return nb_save_type == 4 ? 14 : 6; }

static void ee_dma_send(const volatile void* src, int halfwords) {
    REG_DMA3CNT = 0;
    REG_DMA3SAD = (u32)src;
    REG_DMA3DAD = (u32)EEPROM_PORT;
    REG_DMA3CNT = DMA_ENABLE | DMA_16 | DMA_DST_FIX | (u32)halfwords;
    /* BOUNDED, like every other wait on a save part. DMA3 clears its own
       enable bit the moment the transfer ends, so this normally spins a few
       hundred cycles -- but on a cartridge with no EEPROM behind the port
       there is nothing to end it, and an unbounded wait here hangs the GAME
       rather than the save. The first version of this had no bound and the
       power-cycle gate caught it: a fresh EEPROM cartridge never reached its
       Create event. */
    for (u32 t = 0; t < 4096u; t++)
        if (!(REG_DMA3CNT & DMA_ENABLE)) return;
    REG_DMA3CNT = 0;                /* give up and leave the channel free */
}

static void ee_dma_receive(volatile void* dst, int halfwords) {
    REG_DMA3CNT = 0;
    REG_DMA3SAD = (u32)EEPROM_PORT;
    REG_DMA3DAD = (u32)dst;
    REG_DMA3CNT = DMA_ENABLE | DMA_16 | DMA_SRC_FIX | (u32)halfwords;
    for (int spin = 0; (REG_DMA3CNT & DMA_ENABLE) && spin < 100000; spin++) { }
    if (REG_DMA3CNT & DMA_ENABLE) REG_DMA3CNT = 0;
}

/* Read one 8-byte block into `out`. */
static void eeprom_block_read(int block, u8* out) {
    int ab = ee_addr_bits(), n = 0, i;
    u16 in[68];
    g_ee_bits[n++] = 1;
    g_ee_bits[n++] = 1;                     /* 11 = read */
    for (i = ab - 1; i >= 0; i--)
        g_ee_bits[n++] = (u16)((block >> i) & 1);
    g_ee_bits[n++] = 0;
    ee_dma_send(g_ee_bits, n);
    ee_dma_receive(in, 68);
    /* Four ignored bits, then 64 data bits, most significant first. */
    for (i = 0; i < 8; i++) {
        u8 b = 0;
        for (int k = 0; k < 8; k++)
            b = (u8)((b << 1) | (in[4 + i * 8 + k] & 1));
        out[i] = b;
    }
}

/* Write one 8-byte block, then wait for the device to finish burning it.
   The poll is bounded: a worn or absent part must hang the SAVE, never the
   GAME -- the same rule the flash path follows. */
/* Set once a block write finishes without the port ever reporting ready:
   one probe is enough to learn that this cartridge does not answer the
   status read, and continuing to poll every later block would spend
   seconds learning it again. The writes still go out. */
static u8 g_ee_no_status;

static void eeprom_block_write(int block, const u8* src) {
    int ab = ee_addr_bits(), n = 0, i;
    g_ee_bits[n++] = 1;
    g_ee_bits[n++] = 0;                     /* 10 = write */
    for (i = ab - 1; i >= 0; i--)
        g_ee_bits[n++] = (u16)((block >> i) & 1);
    for (i = 0; i < 8; i++)
        for (int k = 7; k >= 0; k--)
            g_ee_bits[n++] = (u16)((src[i] >> k) & 1);
    g_ee_bits[n++] = 0;
    ee_dma_send(g_ee_bits, n);
    /* A real part finishes a block in about 10 ms and reports it by
       reading back 1. Two things went wrong before this shape: an
       unbounded wait hung the GAME on a cartridge with no EEPROM, and a
       generous per-block bound across every block left the Create event
       still inside rt_game_save five seconds after boot -- which the
       power-cycle gate reported, correctly, as a cartridge that never
       started. So: bounded, and if the first block never reports ready
       this cartridge is taken not to answer status at all. */
    if (g_ee_no_status) return;
    for (u32 t = 0; t < 8192u; t++)
        if (*EEPROM_PORT & 1) return;       /* 1 = finished */
    g_ee_no_status = 1;
}

static void ee_load_shadow(void) {
    if (g_ee_loaded) return;
    for (int b = 0; b < EE_BLOCKS; b++)
        eeprom_block_read(b, &g_ee_shadow[b * 8]);
    g_ee_loaded = 1;
}

static int save_is_eeprom(void) {
    return nb_save_type == 3 || nb_save_type == 4;
}

static int save_is_flash(void) {
    return nb_save_type == 1 || nb_save_type == 2;
}

static void flash_cmd(u8 c) {
    FLASH_A = 0xAA;
    FLASH_B = 0x55;
    FLASH_A = c;
}

/* Poll until the byte reads back. Bounded: a worn or absent part must hang
   the SAVE, never the GAME. */
static int flash_wait(volatile u8* p, u8 want) {
    for (u32 n = 0; n < 2000000u; n++)
        if (*p == want) return 1;
    return 0;
}

static void save_init(void) {
    if (nb_save_type == 2) {        /* pin bank 0 on the banked 128K part */
        flash_cmd(0xB0);
        SAVE_PORT[0] = 0;
    }
}

static void save_erase_sector0(void) {
    flash_cmd(0x80);
    FLASH_A = 0xAA;
    FLASH_B = 0x55;
    SAVE_PORT[0] = 0x30;            /* erase the sector holding the block */
    flash_wait(&SAVE_PORT[0], 0xFF);
}

static void save_w8(int off, u8 v) {
    if (save_is_eeprom()) {
        ee_load_shadow();
        if (off >= 0 && off < (int)sizeof g_ee_shadow
            && g_ee_shadow[off] != v) {
            g_ee_shadow[off] = v;
            g_ee_dirty[off / 8] = 1;
        }
        return;
    }
    if (nb_save_type == 0) {
        SAVE_PORT[off] = v;
        return;
    }
    flash_cmd(0xA0);                /* program one byte, then wait it true */
    SAVE_PORT[off] = v;
    flash_wait(&SAVE_PORT[off], v);
}

static void save_w32(int off, s32 v) {
    save_w8(off, (u8)v);
    save_w8(off + 1, (u8)(v >> 8));
    save_w8(off + 2, (u8)(v >> 16));
    save_w8(off + 3, (u8)(v >> 24));
}

/* Reads are plain bytes on the memory-mapped parts; EEPROM answers from
   the shadow, which is filled from the device on first touch. */
static s32 save_r32(int off) {
    if (save_is_eeprom()) {
        ee_load_shadow();
        if (off < 0 || off + 3 >= (int)sizeof g_ee_shadow) return 0;
        return (s32)((u32)g_ee_shadow[off]
                     | ((u32)g_ee_shadow[off + 1] << 8)
                     | ((u32)g_ee_shadow[off + 2] << 16)
                     | ((u32)g_ee_shadow[off + 3] << 24));
    }
    return (s32)((u32)SAVE_PORT[off] | ((u32)SAVE_PORT[off + 1] << 8)
                 | ((u32)SAVE_PORT[off + 2] << 16)
                 | ((u32)SAVE_PORT[off + 3] << 24));
}

/* EEPROM writes reach the device here, a whole block at a time; the other
   parts have already written and this does nothing. */
static void save_commit(void) {
    if (!save_is_eeprom()) return;
    /* Only blocks whose bytes CHANGED. A save that moves a score and a
       couple of globals touches two or three of nineteen, and each
       untouched block skipped is a block-burn of device time not spent. */
    for (int b = 0; b < EE_BLOCKS; b++) {
        if (!g_ee_dirty[b]) continue;
        eeprom_block_write(b, &g_ee_shadow[b * 8]);
        g_ee_dirty[b] = 0;
    }
}

void rt_game_save(void) {
    /* The high score shares sector 0; carry it over the erase. */
    s32 hs = (save_r32(0) == (s32)SAVE_MAGIC) ? save_r32(SAVE_HISCORE) : 0;
    if (save_is_flash())
        save_erase_sector0();
    save_w32(0, (s32)SAVE_MAGIC);
    save_w32(4, nb_score);
    save_w32(8, nb_lives);
    save_w32(12, nb_health);
    for (int i = 0; i < NB_MAX_GLOBALS; i++)
        save_w32(16 + i * 4, nb_global[i]);
    if (save_is_flash())
        save_w32(SAVE_HISCORE, hs);
    save_commit();
}

int rt_game_load(void) {
    if (save_r32(0) != (s32)SAVE_MAGIC)
        return 0;
    nb_score = save_r32(4);
    nb_lives = save_r32(8);
    nb_health = save_r32(12);
    for (int i = 0; i < NB_MAX_GLOBALS; i++)
        nb_global[i] = save_r32(16 + i * 4);
    return 1;
}

/* The best score ever submitted. Kept separately from the save slot so a game
   with no save/load at all can still have a high score that survives being
   switched off — the thing that makes a home-made game worth replaying. */
s32 rt_highscore(void) {
    if (save_r32(0) != (s32)SAVE_MAGIC)
        return 0;
    return save_r32(SAVE_HISCORE);
}

int rt_highscore_submit(void) {
    s32 best = rt_highscore();
    if (nb_save_type == 0 || save_is_eeprom()) {
        /* Both address the block directly: SRAM because it is memory,
           EEPROM because the shadow is. No erase, so no carrying. */
        if (save_r32(0) != (s32)SAVE_MAGIC)
            save_w32(0, (s32)SAVE_MAGIC);
        if (nb_score > best) {
            save_w32(SAVE_HISCORE, nb_score);
            save_commit();
            return 1;
        }
        save_commit();
        return 0;
    }
    /* Flash: the sector is rewritten whole, preserving the save block. A
       submit with no save behaves as it always has -- the magic goes in and
       the slot fields read back zero. */
    if (nb_score <= best && save_r32(0) == (s32)SAVE_MAGIC)
        return 0;
    s32 sc = 0, lv = 0, hp = 0, g[NB_MAX_GLOBALS];
    int had = save_r32(0) == (s32)SAVE_MAGIC;
    for (int i = 0; i < NB_MAX_GLOBALS; i++)
        g[i] = had ? save_r32(16 + i * 4) : 0;
    if (had) {
        sc = save_r32(4);
        lv = save_r32(8);
        hp = save_r32(12);
    }
    save_erase_sector0();
    save_w32(0, (s32)SAVE_MAGIC);
    save_w32(4, sc);
    save_w32(8, lv);
    save_w32(12, hp);
    for (int i = 0; i < NB_MAX_GLOBALS; i++)
        save_w32(16 + i * 4, g[i]);
    save_w32(SAVE_HISCORE, nb_score > best ? nb_score : best);
    return nb_score > best;
}

/* ---------------- randomness ---------------- */
static u32 g_rng = 0x1234567u;

s32 rt_random(s32 n) {
    g_rng = g_rng * 1103515245u + 12345u;
    if (n <= 0) return 0;
    return (s32)((g_rng >> 16) % (u32)n);
}

void rt_random_seed(s32 seed) { g_rng = (u32)seed | 1u; }

/* ---------------- fixed-point trigonometry ----------------
   Directions are 0..255 for a full turn (0 = right, 64 = up, 128 = left), and
   sines are 8.8 fixed point. No FPU on ARM7, so everything is a table lookup
   plus one integer multiply. */
static const u16 nb_sin_q[65] = {
       0,    6,   13,   19,   25,   31,   38,   44,   50,   56,   62,   68,
      74,   80,   86,   92,   98,  104,  109,  115,  121,  126,  132,  137,
     142,  147,  152,  157,  162,  167,  172,  177,  181,  185,  190,  194,
     198,  202,  206,  209,  213,  216,  220,  223,  226,  229,  231,  234,
     237,  239,  241,  243,  245,  247,  248,  250,  251,  252,  253,  254,
     255,  255,  256,  256,  256,
};
/* atan(i/64) scaled so a full 45 degrees is 32 units of direction */
static const u8 nb_atan_q[65] = {
      0,  1,  1,  2,  3,  3,  4,  4,  5,  6,  6,  7,  8,  8,  9,  9,
     10, 11, 11, 12, 12, 13, 13, 14, 15, 15, 16, 16, 17, 17, 18, 18,
     19, 19, 20, 20, 21, 21, 22, 22, 23, 23, 24, 24, 25, 25, 25, 26,
     26, 27, 27, 27, 28, 28, 29, 29, 29, 30, 30, 30, 31, 31, 31, 32,
     32,
};

s32 rt_sin8(s32 dir) {
    dir &= 255;
    if (dir <= 64) return (s32)nb_sin_q[dir];
    if (dir <= 128) return (s32)nb_sin_q[128 - dir];
    if (dir <= 192) return -(s32)nb_sin_q[dir - 128];
    return -(s32)nb_sin_q[256 - dir];
}

s32 rt_cos8(s32 dir) { return rt_sin8(dir + 64); }

/* Integer square root (binary digit-by-digit; ARM7 has no divide either). */
static u32 isqrt32(u32 v) {
    u32 rem = 0, root = 0;
    for (int i = 0; i < 16; i++) {
        root <<= 1;
        rem = (rem << 2) | (v >> 30);
        v <<= 2;
        if (root < rem) { rem -= root | 1; root += 2; }
    }
    return root >> 1;
}

static s32 hypot_i(s32 dx, s32 dy) {
    if (dx < 0) dx = -dx;
    if (dy < 0) dy = -dy;
    if (dx > 0x7FFF) dx = 0x7FFF;
    if (dy > 0x7FFF) dy = 0x7FFF;
    return (s32)isqrt32((u32)(dx * dx + dy * dy));
}

/* Direction from (0,0) to (dx,dy) in screen coordinates (y grows downwards),
   as 0..255 anticlockwise from "right". */
static s32 dir_of(s32 dx, s32 dy) {
    s32 up = -dy;
    s32 ax = dx < 0 ? -dx : dx, ay = up < 0 ? -up : up;
    s32 a;
    if (!ax && !ay) return 0;
    if (ax >= ay) a = (s32)nb_atan_q[(ay * 64) / ax];
    else          a = 64 - (s32)nb_atan_q[(ax * 64) / ay];
    if (dx >= 0) return up >= 0 ? a : (256 - a) & 255;
    return up >= 0 ? 128 - a : 128 + a;
}

/* ---------------- video (mode 0) ----------------
   BG0  the room's tile layer: tileset in charblock 0, a wrapping 64x64 window
        (screenblocks 28-31) that streams in as the camera moves, so a room may
        be far larger than the 512x512 px that window covers.
   BG1  the text/HUD layer, fixed to the screen (screenblock 26, charblock 1).
   BG2  the panel layer behind the text: dialogue boxes and menu backgrounds.
   BG3  an optional repeating 32x32 parallax layer (screenblock 24). */
#define BG_MAP_SB    28
#define BG_TEXT_SB   26
#define BG_PANEL_SB  25
#define BG_FAR_SB    24
#define BG_AFF_SB    27   /* the affine map: 8-bit indices, 2 KB, free between
                            the parallax/panel/text blocks and the room map */
#define BG_AFF_CB    2    /* 8bpp affine tiles; charblock 2 is otherwise unused */
#define SOLID_TILE   NB_FONT_COUNT   /* the filled cell rt_draw_box paints with */

static s32 g_cam_x = 0, g_cam_y = 0;     /* camera top-left, in room pixels */
static s32 g_scroll_x = 0, g_scroll_y = 0;   /* camera + screen shake */
static s32 g_win_x = 0, g_win_y = 0;     /* tile window origin currently loaded */
static s32 g_room_cw = 0, g_room_ch = 0; /* room size in tiles */
static s32 g_room_w = 0, g_room_h = 0;   /* room size in pixels */
/* Collision reads the tile layer thousands of times a frame, and every read of
   a `const` in the cartridge costs several cycles and breaks the ROM prefetch.
   So the room's own fields are mirrored into IWRAM at load time, and the
   tileset's solid flags are expanded into a flat IWRAM table indexed straight
   by a tilemap entry: one multiply, one ROM read, one IWRAM read per cell. */
static const u16* g_tiles = 0;
static u8 g_edge_solid = 0;
static u8 g_has_solid = 0;
static u8 g_solid_of[512];
/* Creates the instance pool had no room for. rt_create returns 0 and every
   caller in the generated code discards it, so a game that spawns past 128
   simply stops spawning with nothing to say so. Counted here and shown on
   the profiler overlay, which is where an author is already looking when
   something is wrong with a busy scene. */
static u32 g_create_refused = 0;
static u16 g_dispcnt = MODE0 | BG0_ON | BG1_ON | BG2_ON | OBJ_ON | OBJ_1D_MAP;

/* screen effects */
static s8 g_fade_user = 0;
static u8 g_dark = 0, g_flash_n = 0, g_flash_max = 0;
static u8 g_shake_n = 0, g_shake_mag = 0;
static u8 g_trans = 0;                   /* 0 none, 1 fading out, 2 fading in */
static s16 g_trans_room = -1;

/* the OAM the next VBlank will install; built in IWRAM, flushed by DMA */
static OBJATTR g_oam[128] __attribute__((aligned(4)));

/* ---- BG1 text layer (fixed to the screen; over the map and the sprites) ---- */
/* One BG palette bank per ink colour, so a HUD can use more than white. */
static const u8 nb_text_bank[NB_COLOURS] = { 15, 8, 9, 10, 11, 12, 13, 14 };

static u16 text_cell(unsigned char ch, int colour) {
    if (colour < 0 || colour >= NB_COLOURS) colour = 0;
    return (u16)(ch | (u16)(nb_text_bank[colour] << 12));
}

void rt_clear_text(void) {
    volatile u16* tsb = SCREENBLOCK(BG_TEXT_SB);
    volatile u16* psb = SCREENBLOCK(BG_PANEL_SB);
    for (int i = 0; i < 32 * 32; i++) { tsb[i] = 0; psb[i] = 0; }
}

void rt_clear_box(int col, int row, int w, int h) {
    for (int r = row; r < row + h; r++) {
        if (r < 0 || r >= 20) continue;
        volatile u16* tsb = SCREENBLOCK(BG_TEXT_SB) + r * 32;
        volatile u16* psb = SCREENBLOCK(BG_PANEL_SB) + r * 32;
        for (int c = col; c < col + w; c++)
            if (c >= 0 && c < 30) { tsb[c] = 0; psb[c] = 0; }
    }
}

void rt_draw_text_c(int col, int row, const char* s, int colour) {
    if (row < 0 || row >= 20 || !s) return;
    volatile u16* sb = SCREENBLOCK(BG_TEXT_SB) + row * 32;
    for (int c = col; *s; s++, c++) {
        if (c < 0 || c >= 30)
            continue;
        unsigned char ch = (unsigned char)*s;
        if (ch < NB_FONT_FIRST || ch >= NB_FONT_FIRST + NB_FONT_COUNT)
            ch = ' ';
        sb[c] = text_cell((unsigned char)(ch - NB_FONT_FIRST), colour);
    }
}

void rt_draw_text(int col, int row, const char* s) {
    rt_draw_text_c(col, row, s, NB_WHITE);
}

/* How wide a string is in PIXELS, using each glyph's own width.
 *
 * Measurement is separate from drawing on purpose. Text is still drawn one
 * glyph per 8-pixel cell, so this is not yet what rt_draw_text advances by --
 * it is what centring, fitting and a proportional renderer all need, and
 * having it correct before the renderer exists is what makes the renderer a
 * small change rather than a large one.
 *
 * Control codes cost no width: measuring the raw characters would report a
 * line as too long and wrap one that fits. */
int rt_text_width(const char* s) {
    int w = 0;
    if (!s) return 0;
    while (*s) {
        unsigned char c = (unsigned char)*s;
        if (c == '{') {
            const char* j = s + 1;
            while (*j && *j != '}' && *j != ' ' && *j != '\n') j++;
            if (*j == '}') { s = j + 1; continue; }
        }
        if (c >= NB_FONT_FIRST && c < NB_FONT_FIRST + NB_FONT_COUNT)
            w += nb_font_w[c - NB_FONT_FIRST];
        else
            w += 8;
        s++;
    }
    return w;
}

/* The same string in whole cells, which is what the fixed-cell renderer uses. */
int rt_text_cells(const char* s) {
    int n = 0;
    if (!s) return 0;
    while (*s) {
        if (*s == '{') {
            const char* j = s + 1;
            while (*j && *j != '}' && *j != ' ' && *j != '\n') j++;
            if (*j == '}') { s = j + 1; continue; }
        }
        n++; s++;
    }
    return n;
}

void rt_draw_text_centre(int row, const char* s, int colour) {
    int n;
    if (!s) return;
    /* Counted WITHOUT control codes: a banner carrying a colour code used to
       be pushed left by the width of the code itself. */
    n = rt_text_cells(s);
    rt_draw_text_c((30 - n) / 2, row, s, colour);
}

/* A filled rectangle: the background of a dialogue box, a menu, a health bar or
   a game-over banner. Boxes live on their OWN layer behind the text, because a
   character cell is transparent around its ink — painting a box into the text
   layer and then writing on it would show the room through every letter. */
void rt_draw_box(int col, int row, int w, int h, int colour) {
    for (int r = row; r < row + h; r++) {
        if (r < 0 || r >= 20) continue;
        volatile u16* sb = SCREENBLOCK(BG_PANEL_SB) + r * 32;
        for (int c = col; c < col + w; c++)
            if (c >= 0 && c < 30) sb[c] = text_cell(SOLID_TILE, colour);
    }
}

/* A box with a one-cell border in a second colour — a window, not a smudge. */
void rt_draw_panel(int col, int row, int w, int h, int fill, int border) {
    rt_draw_box(col, row, w, h, border);
    if (w > 2 && h > 2)
        rt_draw_box(col + 1, row + 1, w - 2, h - 2, fill);
}

static int int_to_str(char* buf, s32 value, int digits) {
    char tmp[12];
    int i = 0, n = 0, neg = value < 0;
    u32 v = neg ? (u32)(-(s32)value) : (u32)value;
    if (v == 0) tmp[n++] = '0';
    while (v > 0) { tmp[n++] = (char)('0' + (v % 10)); v /= 10; }
    if (neg) buf[i++] = '-';
    for (int p = n; p < digits && i < 11; p++) buf[i++] = '0';
    while (n > 0 && i < 11) buf[i++] = tmp[--n];
    buf[i] = 0;
    return i;
}

void rt_draw_int_c(int col, int row, s32 value, int colour) {
    char buf[12];
    int_to_str(buf, value, 0);
    rt_draw_text_c(col, row, buf, colour);
}

void rt_draw_int(int col, int row, s32 value) {
    rt_draw_int_c(col, row, value, NB_WHITE);
}

/* Leading zeros, so a score read-out does not jitter as it grows. */
void rt_draw_int_pad(int col, int row, s32 value, int digits, int colour) {
    char buf[12];
    if (digits < 0) digits = 0;
    if (digits > 10) digits = 10;
    int_to_str(buf, value, digits);
    rt_draw_text_c(col, row, buf, colour);
}

/* ---- the room's tile layer, as a wrapping window over an unlimited room ----
   Map cell (tx & 63, ty & 63) always holds room tile (tx, ty) for the 64x64
   window currently loaded, and BG0's scroll register wraps every 512 px, so
   simply refilling the tile column or row that has just come into view is all
   that is needed to scroll a room of any size. */
static void map_put(s32 tx, s32 ty) {
    volatile u16* sb = SCREENBLOCK(BG_MAP_SB + (((ty >> 5) & 1) << 1)
                                   + ((tx >> 5) & 1));
    u16 v = 0;
    if (g_room && g_room->tiles && tx >= 0 && ty >= 0
        && tx < g_room_cw && ty < g_room_ch)
        v = g_room->tiles[ty * g_room_cw + tx];
    sb[(ty & 31) * 32 + (tx & 31)] = v;
}

static void map_col(s32 tx) {
    for (s32 ty = g_win_y; ty < g_win_y + 64; ty++) map_put(tx, ty);
}

static void map_row(s32 ty) {
    for (s32 tx = g_win_x; tx < g_win_x + 64; tx++) map_put(tx, ty);
}

static void map_full(void) {
    for (s32 ty = g_win_y; ty < g_win_y + 64; ty++)
        for (s32 tx = g_win_x; tx < g_win_x + 64; tx++) map_put(tx, ty);
}

static void map_update(void) {
    if (!g_room) return;
    s32 wx = (g_cam_x >> 3) - 16, wy = (g_cam_y >> 3) - 16;
    if (g_room_cw <= 64) wx = 0;
    else { if (wx < 0) wx = 0; else if (wx > g_room_cw - 64) wx = g_room_cw - 64; }
    if (g_room_ch <= 64) wy = 0;
    else { if (wy < 0) wy = 0; else if (wy > g_room_ch - 64) wy = g_room_ch - 64; }
    s32 dx = wx - g_win_x, dy = wy - g_win_y;
    if (!dx && !dy) return;
    if (dx <= -64 || dx >= 64 || dy <= -64 || dy >= 64) {
        g_win_x = wx; g_win_y = wy; map_full(); return;
    }
    while (g_win_y != wy) {
        if (g_win_y < wy) { g_win_y++; map_row(g_win_y + 63); }
        else              { g_win_y--; map_row(g_win_y); }
    }
    while (g_win_x != wx) {
        if (g_win_x < wx) { g_win_x++; map_col(g_win_x + 63); }
        else              { g_win_x--; map_col(g_win_x); }
    }
}

/* The parallax layer: a 32x32 tile pattern that wraps by itself, scrolled at a
   fraction of the camera's speed so it reads as distance. */
static void far_load(const nb_Room* r) {
    volatile u16* sb = SCREENBLOCK(BG_FAR_SB);
    if (!r->far_tiles) {
        g_dispcnt &= (u16)~BG3_ON;
        return;
    }
    dma_copy16(sb, r->far_tiles, 32 * 32);
    g_dispcnt |= BG3_ON;
}

static void rt_video_init(void) {
    /* OBJ palette (16 banks of 16) + every sprite's tiles, uploaded once */
    dma_copy16(OBJ_PALETTE, nb_obj_palette, 256);
    int n = nb_obj_tile_count * 16;    /* 16 u16 per 4bpp tile */
    if (n > 16384) n = 16384;          /* OBJ VRAM is 32 KiB */
    dma_copy16(OBJ_TILES, nb_obj_tiles, n);
    for (int i = 0; i < 128; i++) {
        g_oam[i].attr0 = OBJ_HIDE;
        g_oam[i].attr1 = 0; g_oam[i].attr2 = 0; g_oam[i].fill = 0;
        OAM[i].attr0 = OBJ_HIDE;
    }

    /* shared BG palette (entry 0 is the per-room backdrop) + tileset tiles */
    for (int i = 1; i < 16; i++) BG_PALETTE[i] = nb_bg_palette[i];
    int m = nb_bg_tile_count * 16;
    if (m > 8192) m = 8192;            /* charblock 0 holds 512 tiles */
    dma_copy16(CHARBLOCK(0), nb_bg_tiles, m);
    REG_BG0CNT = BGCNT_CB(0) | BGCNT_SB(BG_MAP_SB) | BGCNT_4BPP | BGCNT_SIZE(3) | 2;

    /* The affine tileset, if the project has one: 8bpp, 64 bytes a tile, into
       the charblock nothing else claims. Uploaded once like every other
       tileset -- a room that uses none simply never points a layer at it. */
    if (nb_aff_tile_count > 0) {
        int a = nb_aff_tile_count * 32;    /* 32 u16 per 8bpp tile */
        if (a > 8192) a = 8192;            /* 256 tiles fills the charblock */
        dma_copy16(CHARBLOCK(BG_AFF_CB), nb_aff_tiles, a);
        /* And its palette. An 8bpp layer indexes the WHOLE 256-entry BG
           palette, not the 16 the 4bpp tiles share -- forgetting this uploads
           perfect tiles that index black, which is a layer configured
           correctly in every register and invisible on screen. Entry 0 stays
           the room backdrop, as it is for every other layer. */
        for (int c = 1; c < 256; c++) BG_PALETTE[c] = nb_aff_palette[c];
    }

    /* BG3: the parallax layer shares the room's tileset, behind everything */
    REG_BG3CNT = BGCNT_CB(0) | BGCNT_SB(BG_FAR_SB) | BGCNT_4BPP | 3;

    /* BG1 text layer: the font goes to charblock 1, followed by one filled tile
       that rt_draw_box paints panels with. Priority 0 so a HUD or dialogue box
       sits above the map (priority 2) and the sprites (priority 1). */
    {
        int fn = (int)(sizeof(nb_font) / sizeof(nb_font[0]));
        volatile u16* fcb = CHARBLOCK(1);
        dma_copy16(fcb, nb_font, fn);
        for (int i = 0; i < 16; i++) fcb[SOLID_TILE * 16 + i] = 0x1111;
    }
    /* one palette bank per ink colour (bank 15 stays white: the default) */
    BG_PALETTE[15 * 16 + 1] = RGB15(31, 31, 31);   /* NB_WHITE  */
    BG_PALETTE[ 8 * 16 + 1] = RGB15( 1,  1,  3);   /* NB_BLACK  */
    BG_PALETTE[ 9 * 16 + 1] = RGB15(31, 30,  6);   /* NB_YELLOW */
    BG_PALETTE[10 * 16 + 1] = RGB15(31,  7,  6);   /* NB_RED    */
    BG_PALETTE[11 * 16 + 1] = RGB15( 8, 27,  9);   /* NB_GREEN  */
    BG_PALETTE[12 * 16 + 1] = RGB15( 8, 28, 31);   /* NB_CYAN   */
    BG_PALETTE[13 * 16 + 1] = RGB15( 9, 12, 31);   /* NB_BLUE   */
    BG_PALETTE[14 * 16 + 1] = RGB15(18, 18, 20);   /* NB_GREY   */
    REG_BG1CNT = BGCNT_CB(1) | BGCNT_SB(BG_TEXT_SB) | BGCNT_4BPP;   /* priority 0 */
    REG_BG1HOFS = 0; REG_BG1VOFS = 0;
    /* BG2 carries the panels. Same priority as the text, and a lower-numbered
       layer wins a tie, so text sits on its box and both sit over the sprites. */
    REG_BG2CNT = BGCNT_CB(1) | BGCNT_SB(BG_PANEL_SB) | BGCNT_4BPP;
    REG_BG2HOFS = 0; REG_BG2VOFS = 0;
    rt_clear_text();

    REG_BLDCNT = 0;
    REG_DISPCNT = g_dispcnt;
}

/* Centre the camera on the followed instance, clamped inside the room. */
static void rt_camera_update(void) {
    if (g_room && g_view && g_view->active) {
        s32 cx = g_view->x - SCREEN_W / 2;
        s32 cy = g_view->y - SCREEN_H / 2;
        s32 maxx = (s32)g_room->w - SCREEN_W; if (maxx < 0) maxx = 0;
        s32 maxy = (s32)g_room->h - SCREEN_H; if (maxy < 0) maxy = 0;
        if (cx < 0) cx = 0; else if (cx > maxx) cx = maxx;
        if (cy < 0) cy = 0; else if (cy > maxy) cy = maxy;
        g_cam_x = cx; g_cam_y = cy;
    }
    g_scroll_x = g_cam_x; g_scroll_y = g_cam_y;
    if (g_shake_n) {
        s32 m = g_shake_mag ? g_shake_mag : 2;
        g_scroll_x += rt_random(m * 2 + 1) - m;
        g_scroll_y += rt_random(m * 2 + 1) - m;
        g_shake_n--;
    }
}

void rt_view_follow(Instance* self) { g_view = self; }

void rt_view_fixed(s32 x, s32 y) { g_view = 0; g_cam_x = x; g_cam_y = y; }

s32 rt_view_x(void) { return g_cam_x; }
s32 rt_view_y(void) { return g_cam_y; }

void rt_fade(s32 amount) {
    if (amount < -16) amount = -16;
    if (amount > 16) amount = 16;
    g_fade_user = (s8)amount;
}

void rt_flash(s32 frames) {
    if (frames < 1) frames = 1;
    if (frames > 60) frames = 60;
    g_flash_n = g_flash_max = (u8)frames;
}

void rt_shake(s32 frames, s32 magnitude) {
    if (frames < 0) frames = 0;
    if (frames > 255) frames = 255;
    if (magnitude < 1) magnitude = 1;
    if (magnitude > 16) magnitude = 16;
    g_shake_n = (u8)frames; g_shake_mag = (u8)magnitude;
}

/* ---- power, and the cartridge's own hardware ------------------------------
 * SLEEP is BIOS Stop (SWI 3): the CPU, the display and the sound clock all
 * halt until an enabled interrupt arrives, and the console draws almost
 * nothing. The wake source must be enabled in REG_IE BEFORE stopping or the
 * machine never comes back -- a keypad interrupt is the only one a player can
 * reach, so rt_sleep arms it, stops, and restores the interrupt mask it found.
 *
 * rt_wait_vblank is BIOS Halt (SWI 2) for the frames a game spends waiting:
 * the CPU idles until any interrupt instead of spinning, which on a handheld
 * is battery.
 *
 * RUMBLE, SOLAR and GYRO are all CARTRIDGE hardware reached through the same
 * four GPIO pins the RTC uses -- no console pin does any of it. A cartridge
 * has one of them at most, so the three are separate calls over shared pins
 * and rt_gpio_release hands the pins back. */
static u16 g_gpio_saved_dir;

void rt_wait_vblank(void) {
    __asm__ volatile("swi 0x020000" ::: "r0", "r1", "r2", "r3", "memory");
}

void rt_sleep(void) {
    u16 ie = REG_IE, ime = REG_IME;
    REG_IE = IRQ_KEYPAD;              /* the only wake a player can produce */
    REG_KEYCNT = (u16)(KEYCNT_IRQ | KEY_A | KEY_B | KEY_START | KEY_SELECT);
    REG_IME = 1;
    __asm__ volatile("swi 0x030000" ::: "r0", "r1", "r2", "r3", "memory");
    REG_KEYCNT = 0;
    REG_IE = ie;
    REG_IME = ime;
}

/* Rumble: a GPIO pin held high shakes the motor. Cartridges that have one
   wire it to bit 3 -- the pin the RTC uses for chip select, which is why a
   cartridge never has both. */
void rt_rumble(int on) {
    g_gpio_saved_dir = REG_GPIO_DIR;
    REG_GPIO_CTRL = 1;                /* GPIO readable/writable */
    REG_GPIO_DIR = 0x0008;            /* bit 3 out */
    REG_GPIO_DATA = (u16)(on ? 0x0008 : 0x0000);
}

/* The solar sensor (Boktai): pulse the clock pin and count how long the
   ADC takes to flag. Bright light returns a small count. 0..255, and 0 on a
   cartridge with no sensor -- which is indistinguishable from darkness, so a
   game that needs the difference must ask the player. */
int rt_solar(void) {
    int n;
    REG_GPIO_CTRL = 1;
    REG_GPIO_DIR = 0x0007;            /* clk + reset out, flag in */
    REG_GPIO_DATA = 0x0002;           /* reset high */
    REG_GPIO_DATA = 0x0000;
    for (n = 0; n < 255; n++) {
        REG_GPIO_DATA = 0x0001;       /* clock high */
        REG_GPIO_DATA = 0x0000;
        if (REG_GPIO_DATA & 0x0008) break;
    }
    return n;
}

/* The gyro (WarioWare Twisted): the same ADC, read as 12 bits of angular
   rate. Centre is about 0x6C0; smaller is one way, larger the other. */
int rt_gyro(void) {
    int i, v = 0;
    REG_GPIO_CTRL = 1;
    REG_GPIO_DIR = 0x000B;            /* clk + start out, data in */
    REG_GPIO_DATA = 0x0002;           /* start conversion */
    REG_GPIO_DATA = 0x0000;
    for (i = 0; i < 12; i++) {
        REG_GPIO_DATA = 0x0001;
        REG_GPIO_DATA = 0x0000;
        v = (v << 1) | ((REG_GPIO_DATA & 0x0004) ? 1 : 0);
    }
    return v;
}

/* Hand the pins back to whatever else uses them -- the clock, most often. */
void rt_gpio_release(void) {
    REG_GPIO_DATA = 0;
    REG_GPIO_DIR = g_gpio_saved_dir;
    REG_GPIO_CTRL = 0;
}

/* ---- the affine ground layer ----------------------------------------------
 * A room may replace its flat tile layer with an AFFINE one that rotates and
 * scales -- Mode 7 ground, a spinning battle floor, a map that tilts.
 *
 * The cost is fixed by the hardware and worth stating plainly. Affine
 * backgrounds exist only in display modes 1 and 2, and mode 1 offers two text
 * layers where mode 0 offers four. This runtime needs three of them: the
 * room's tiles, the dialogue panel, and the text on top of it. An affine room
 * spends BG2 on the affine layer and therefore gives up the PARALLAX layer
 * entirely -- and its flat tile layer too, since the affine layer IS the
 * ground. What remains is exactly enough: BG0 carries the text and BG1 the
 * panel, which is the reverse of their mode-0 assignment because a lower
 * numbered layer wins a priority tie and the text has to sit on its box.
 *
 * Only the 16x16 and 32x32 map sizes are offered. 64x64 needs 4 KB of map and
 * the free space between the other screenblocks is 2 KB; taking more would
 * come out of the room's own tile map, which an affine room has already given
 * up but a NON-affine room has not, and a layout that changes shape per room
 * is a worse trade than two sizes. */
static void affine_layer_on(const nb_Room* r) {
    int size = r->aff_size > 1 ? 1 : r->aff_size;   /* 0 = 16x16, 1 = 32x32 */
    int cells = size ? 32 * 32 : 16 * 16;
    dma_copy16(SCREENBLOCK(BG_AFF_SB), (const u16*)r->aff_map, cells / 2);
    REG_BG2CNT = (u16)(BGCNT_CB(BG_AFF_CB) | BGCNT_SB(BG_AFF_SB)
                       | BGCNT_8BPP | BGCNT_WRAP | BGCNT_SIZE(size) | 2);
    /* Text above panel above the ground: BG0 and BG1 swap duties. */
    REG_BG0CNT = (u16)(BGCNT_CB(1) | BGCNT_SB(BG_TEXT_SB) | BGCNT_4BPP | 0);
    REG_BG1CNT = (u16)(BGCNT_CB(1) | BGCNT_SB(BG_PANEL_SB) | BGCNT_4BPP | 0);
    g_dispcnt = (u16)((g_dispcnt & (u16)~(7 | BG3_ON))
                      | MODE_1 | BG0_ON | BG1_ON | BG2_ON);
    REG_DISPCNT = g_dispcnt;
    /* Identity: the ground sits still until the game turns it. */
    rt_bg_affine(2, 0, 0, 0, 0, 0, 256);
}

/* Back to the flat arrangement. Called on every room load that has no affine
   layer, so a game can walk from an affine room into an ordinary one. */
static void affine_layer_off(void) {
    REG_BG0CNT = (u16)(BGCNT_CB(0) | BGCNT_SB(BG_MAP_SB) | BGCNT_4BPP
                       | BGCNT_SIZE(3) | 2);
    REG_BG1CNT = (u16)(BGCNT_CB(1) | BGCNT_SB(BG_TEXT_SB) | BGCNT_4BPP | 0);
    REG_BG2CNT = (u16)(BGCNT_CB(1) | BGCNT_SB(BG_PANEL_SB) | BGCNT_4BPP | 0);
    g_dispcnt = (u16)((g_dispcnt & (u16)~7) | BG0_ON | BG1_ON | BG2_ON);
    REG_DISPCNT = g_dispcnt;
}

/* ---- palettes at run time -------------------------------------------------
 * Two things a game needs that a static palette cannot give: a sprite that
 * changes its COLOURS without changing its picture -- the same enemy in four
 * team colours, a character who turns grey when poisoned -- and a colour that
 * a game computes rather than an artist picks.
 *
 * A sprite is drawn from one of 16 OBJ palette banks, chosen by the artist.
 * rt_set_palbank overrides that per INSTANCE, so two instances of one object
 * can wear different colours from the same tiles: a bank costs 16 colours,
 * where a recoloured copy of the sprite would cost its whole tile footprint.
 *
 * Writes land immediately. Palette RAM is not double-buffered and the frame
 * loop does not own it, so a mid-frame write tears -- call these from a Step
 * event, which runs inside the game step and well before the flush. */
void rt_set_palbank(Instance* self, int bank) {
    if (!self || bank < 0 || bank > 15) return;
    /* 0 is a real bank, so "use the sprite's own" needs a value outside the
       range: the field holds bank+1 and zero keeps the original behaviour,
       which is the same appended-field rule every struct here follows. */
    self->palbank = (u8)(bank + 1);
}

void rt_clear_palbank(Instance* self) {
    if (self) self->palbank = 0;
}

/* One colour, in the background palette (obj = 0) or the sprite one. */
void rt_pal_set(int obj, int index, u16 colour) {
    if (index < 0 || index > 255) return;
    (obj ? OBJ_PALETTE : BG_PALETTE)[index] = colour;
}

u16 rt_pal_get(int obj, int index) {
    if (index < 0 || index > 255) return 0;
    return (obj ? OBJ_PALETTE : BG_PALETTE)[index];
}

/* A run of colours at once -- a whole bank swapped in one call, which is what
   a palette-swapped enemy or a room's day-and-night tint actually needs. */
void rt_pal_load(int obj, int first, const u16* colours, int count) {
    if (!colours || first < 0 || count <= 0) return;
    if (first + count > 256) count = 256 - first;
    if (count <= 0) return;
    volatile u16* p = (obj ? OBJ_PALETTE : BG_PALETTE) + first;
    for (int i = 0; i < count; i++) p[i] = colours[i];
}

/* ---- palette cycling -----------------------------------------------------
 * The classic effect nothing else reproduces cheaply: waterfalls, lava,
 * torchlight, shimmer -- drawn once, animated by rotating a handful of
 * palette entries every few frames. Four independent cycles may run at once,
 * each over one contiguous range of BG or OBJ entries. Rotation is lossless,
 * so stopping a cycle leaves the colours wherever they stood; they are the
 * same colours, one slot along.
 *
 * The room's backdrop lives in BG entry 0 and room changes rewrite it, so a
 * cycle that includes entry 0 fights the room loader. Nothing forbids it --
 * cycling the backdrop is a legitimate sky effect -- but it is the author's
 * fight to pick. Steps happen in the VBlank flush, so a rotation is never
 * torn mid-frame. */
#define NB_PAL_CYCLES 4
typedef struct { u8 on, obj, first, count, frames, tick; } PalCycle;
static PalCycle g_pal_cyc[NB_PAL_CYCLES];

/* Begin a cycle; the slot number comes back (-1 = none free or bad range).
   obj: 0 = background palette, 1 = sprite palette. first/count: the entry
   range, within 0..255. frames: how many frames each step lasts. */
int rt_pal_cycle(int obj, int first, int count, int frames) {
    if (first < 0 || count < 2 || first + count > 256
        || frames < 1 || frames > 255)
        return -1;
    for (int i = 0; i < NB_PAL_CYCLES; i++) {
        PalCycle* c = &g_pal_cyc[i];
        if (c->on) continue;
        c->on = 1;
        c->obj = (u8)(obj != 0);
        c->first = (u8)first;
        c->count = (u8)count;
        c->frames = (u8)frames;
        c->tick = 0;
        return i;
    }
    return -1;
}

/* Stop one cycle by slot, or every cycle with -1. */
void rt_pal_cycle_stop(int slot) {
    if (slot < 0) {
        for (int i = 0; i < NB_PAL_CYCLES; i++) g_pal_cyc[i].on = 0;
    } else if (slot < NB_PAL_CYCLES) {
        g_pal_cyc[slot].on = 0;
    }
}

static void pal_cycle_step(void) {
    for (int i = 0; i < NB_PAL_CYCLES; i++) {
        PalCycle* c = &g_pal_cyc[i];
        if (!c->on || ++c->tick < c->frames) continue;
        c->tick = 0;
        volatile u16* p = (c->obj ? OBJ_PALETTE : BG_PALETTE) + c->first;
        u16 last = p[c->count - 1];
        for (int n = c->count - 1; n > 0; n--) p[n] = p[n - 1];
        p[0] = last;
    }
}

static void fx_apply(void) {
    s32 dark = g_dark, bright = 0;
    if (g_fade_user < 0) dark += -(s32)g_fade_user; else bright += g_fade_user;
    if (g_flash_n && g_flash_max) bright += (16 * g_flash_n) / g_flash_max;
    if (g_flash_n) g_flash_n--;
    if (dark > 16) dark = 16;
    if (bright > 16) bright = 16;
    if (dark > bright)  { REG_BLDCNT = BLD_ALL | BLD_BLACK; REG_BLDY = (u16)dark; }
    else if (bright)    { REG_BLDCNT = BLD_ALL | BLD_WHITE; REG_BLDY = (u16)bright; }
    else                  REG_BLDCNT = 0;
}

/* ---- interrupts ----------------------------------------------------------
 * Level 3 of the three-level model (docs/GBA-SDK-SPEC.md Part 0): a game can
 * take an interrupt directly. Levels 1 and 2 never see this — the action sheet
 * and the script language sit on top of a runtime that is now interrupt-driven
 * underneath them.
 *
 * The BIOS, not the CPU, dispatches to 0x03007FFC. The hardware exception at
 * 0x18 lands in the BIOS; the BIOS saves registers, then calls the address at
 * 0x03007FFC as a PLAIN ARM FUNCTION with lr pointing back into its own
 * epilogue, and the BIOS performs the exception return itself. So this must be
 * an ordinary function: an ordinary prologue, `bx lr` at the end, nothing else.
 *
 * It was `__attribute__((interrupt("IRQ")))` for a while, on the reasoning
 * that an IRQ handler is what this is -- and that attribute emits
 * `sub lr, lr, #4` on entry and an SPSR-restoring exception return, which is
 * correct at a raw vector and DOUBLE-APPLIES here, inside the BIOS's own
 * exception frame. The subtraction bent the return address into the middle of
 * the BIOS epilogue and the exception return swapped in a stale SPSR, so every
 * ROM hung at its first VBlankIntrWait while every host-side test stayed
 * green: the handler's C is correct, and no host test executes the BIOS
 * calling convention around it. The comment that used to be here asserted the
 * attribute was right, which is worth remembering when reading any comment
 * that explains why code is correct. */
static rt_irq_fn g_irq[RT_IRQ_SLOTS];
static volatile u32 g_frames;        /* VBlanks since boot */

static void rt_irq_entry(void) {
    u16 pending = (u16)(REG_IE & REG_IF);
    int i;
    for (i = 0; i < RT_IRQ_SLOTS; i++) {
        u16 bit = (u16)(1u << i);
        if (!(pending & bit)) continue;
        if (bit == IRQ_VBLANK) g_frames++;
        if (g_irq[i]) g_irq[i]();
    }
    /* Acknowledge BOTH. Writing REG_IF clears the hardware flag; the BIOS keeps
     * its own copy at 0x03007FF8 and SWI VBlankIntrWait spins for ever unless
     * that is set too. */
    REG_IF = pending;
    BIOS_IF = (u16)(BIOS_IF | pending);
}

void rt_irq_set(u16 mask, rt_irq_fn fn) {
    int i;
    u16 ime = REG_IME;
    REG_IME = 0;                     /* a half-installed handler must not fire */
    for (i = 0; i < RT_IRQ_SLOTS; i++) {
        if (mask & (1u << i)) g_irq[i] = fn;
    }
    if (fn) REG_IE = (u16)(REG_IE | mask);
    else    REG_IE = (u16)(REG_IE & ~mask);
    REG_IME = ime;
}

u32 rt_frame_count(void) { return g_frames; }

static void rt_irq_init(void) {
    int i;
    REG_IME = 0;
    for (i = 0; i < RT_IRQ_SLOTS; i++) g_irq[i] = 0;
    BIOS_IRQ_VEC = (u32)rt_irq_entry;
    REG_DISPSTAT = (u16)(REG_DISPSTAT | DSTAT_VBLANK_IRQ);
    REG_IF = 0xFFFF;                 /* discard anything already pending */
    REG_IE = IRQ_VBLANK;
    REG_IME = 1;
}

/* ---- proportional text ---------------------------------------------------
 * The dialogue panel drawn glyph-by-pixel instead of glyph-by-cell.
 *
 * WHY ONLY THE PANEL. Proportional text needs a RAM copy of the tiles it draws
 * into, because a glyph lands across a tile boundary and tiles are the only
 * thing VRAM takes. The panel is 26x4 = 104 tiles, which is 3.3 KB; the whole
 * 30x20 text layer would be 19 KB, most of IWRAM, to make a score read-out
 * slightly narrower. The HUD stays on cells.
 *
 * COLOUR IS PER TILE, not per pixel: 4bpp colour comes from the map entry's
 * palette bank, and a tile has one. A colour change therefore takes effect at
 * the next tile boundary. Said here because the alternative -- silently
 * recolouring the two or three pixels of the previous letter that share the
 * tile -- looks like a rendering fault.
 */
/* Where the panel sits, in cells. Shared with the dialogue engine below. */
#define SAY_COL_C  2
#define SAY_ROW_C  14
#define VWF_COLS   26
#define VWF_ROWS   4
#define VWF_TILES  (VWF_COLS * VWF_ROWS)
#define VWF_BASE   128            /* first tile of ours in the text charblock */

static u32 g_vwf[VWF_TILES * 8];  /* 8 rows of 8 4bpp pixels per tile */
static u8  g_vwf_bank[VWF_TILES];
static u8  g_vwf_on;

void rt_vwf(int on) { g_vwf_on = (u8)(on ? 1 : 0); }
int  rt_vwf_enabled(void) { return g_vwf_on; }

static void vwf_clear(void) {
    int i;
    for (i = 0; i < VWF_TILES * 8; i++) g_vwf[i] = 0;
    for (i = 0; i < VWF_TILES; i++) g_vwf_bank[i] = nb_text_bank[0];
}

/* One glyph at pixel x on text row `row`. Returns how far to advance. */
static int vwf_glyph(int x, int row, unsigned char ch, int colour) {
    int gi, y, gx, w;
    if (ch < NB_FONT_FIRST || ch >= NB_FONT_FIRST + NB_FONT_COUNT) ch = ' ';
    gi = ch - NB_FONT_FIRST;
    w = nb_font_w[gi];
    if (row < 0 || row >= VWF_ROWS) return w;
    if (colour < 0 || colour >= NB_COLOURS) colour = 0;
    for (y = 0; y < 8; y++) {
        u32 g = (u32)nb_font[gi * 16 + y * 2]
              | ((u32)nb_font[gi * 16 + y * 2 + 1] << 16);
        for (gx = 0; gx < w; gx++) {
            int X = x + gx;
            int tile, tx;
            if (!((g >> (gx * 4)) & 0xF)) continue;   /* not ink */
            if (X < 0 || X >= VWF_COLS * 8) continue;
            tile = row * VWF_COLS + (X >> 3);
            tx = X & 7;
            g_vwf[tile * 8 + y] |= (u32)1 << (tx * 4);
            g_vwf_bank[tile] = nb_text_bank[colour];
        }
    }
    return w;
}

/* Push the buffer to VRAM and point the panel's map entries at it. */
static void vwf_flush(void) {
    volatile u16 *cb = CHARBLOCK(1) + VWF_BASE * 16;
    volatile u16 *sb = SCREENBLOCK(BG_TEXT_SB);
    int t;
    dma_copy16(cb, (const u16 *)g_vwf, VWF_TILES * 16);
    for (t = 0; t < VWF_TILES; t++) {
        int row = t / VWF_COLS, col = t % VWF_COLS;
        sb[(SAY_ROW_C + row) * 32 + SAY_COL_C + col] =
            (u16)((VWF_BASE + t) | ((u16)g_vwf_bank[t] << 12));
    }
}

/* ---- dialogue ------------------------------------------------------------
 * A message revealed a character at a time, in a panel, advanced by A.
 *
 * WHY THE ENGINE OWNS THIS. Typewriter text written by hand in a Step event is
 * a state machine with a timer, a cursor, a page counter and a wait-for-button
 * -- five variables per speaking object, in a runtime that gives each instance
 * twelve. Written once here it costs nothing per object and behaves the same
 * everywhere, which is what makes a game's dialogue feel like one game.
 *
 * CONTROL CODES are written in the text itself, because dialogue is authored as
 * text and anything that has to be assembled from parts stops being editable by
 * whoever is writing the words:
 *
 *   \n      a new line
 *   {p}     hold here until A; then clear and carry on
 *   {s:N}   frames per character, 0 = the whole line at once
 *   {c:N}   colour, one of the TXT_ values
 *   {v:N}   the value of global N, in decimal
 *   {w:N}   pause N frames without waiting for a button
 *
 * An unknown code is PRINTED AS WRITTEN rather than swallowed. A typo that
 * silently erases the rest of a sentence is the worst thing a text engine can
 * do to somebody writing prose. */
#define SAY_COL   2
#define SAY_ROW   14
#define SAY_W     26
#define SAY_H     4

static const char *g_say;          /* the message, or 0 when nothing is said */
static u16 g_say_at;               /* how far the reveal has got */
static u16 g_say_wait;             /* frames left before the next character */
static u8  g_say_speed = 2;        /* frames per character */
static u8  g_say_hold;             /* waiting for A at a {p} or at the end */
static u8  g_say_col, g_say_row;   /* the cursor, in cells */
static u16 g_say_px;               /* ...or in pixels, when proportional */
static u8  g_say_ink;
static s16 g_say_voice = -1;       /* sound played per character, -1 for none */

/* How many cells the word starting at `i` will take.
 *
 * Control codes inside a word cost no width, so "{c:3}Bulbasaur" measures 10
 * and not 15 -- measuring the raw characters would wrap a line that fits and
 * leave a ragged right edge that looks like a bug in the text rather than in
 * the measurement. */
static u16 say_word_len(const char *s, u16 i) {
    u16 n = 0;
    while (s[i] && s[i] != ' ' && s[i] != '\n') {
        if (s[i] == '{') {
            u16 j = i + 1;
            while (s[j] && s[j] != '}' && s[j] != ' ' && s[j] != '\n') j++;
            if (s[j] == '}') { i = (u16)(j + 1); continue; }
        }
        i++; n++;
    }
    return n;
}

/* The same word, measured in PIXELS from each glyph's own width. */
static u16 say_word_px(const char *s, u16 i) {
    u16 n = 0;
    while (s[i] && s[i] != ' ' && s[i] != '\n') {
        if (s[i] == '{') {
            u16 j = i + 1;
            while (s[j] && s[j] != '}' && s[j] != ' ' && s[j] != '\n') j++;
            if (s[j] == '}') { i = (u16)(j + 1); continue; }
        }
        {
            unsigned char c = (unsigned char)s[i];
            n = (u16)(n + ((c >= NB_FONT_FIRST
                            && c < NB_FONT_FIRST + NB_FONT_COUNT)
                           ? nb_font_w[c - NB_FONT_FIRST] : 8));
        }
        i++;
    }
    return n;
}

static u16 say_num(const char *s, u16 *i, s32 *out) {
    s32 v = 0;
    u16 n = 0;
    while (s[*i] >= '0' && s[*i] <= '9') {
        v = v * 10 + (s[*i] - '0');
        (*i)++; n++;
    }
    *out = v;
    return n;
}

void rt_say_voice(s16 sound) { g_say_voice = sound; }

void rt_say(const char *text) {
    g_say = text;
    g_say_px = 0;
    if (g_vwf_on) { vwf_clear(); vwf_flush(); }
    g_say_at = 0;
    g_say_wait = 0;
    g_say_hold = 0;
    g_say_speed = 2;
    g_say_ink = NB_WHITE;
    g_say_col = 0;
    g_say_row = 0;
    if (!text) return;
    rt_draw_panel(SAY_COL - 1, SAY_ROW - 1, SAY_W + 2, SAY_H + 2,
                  NB_BLUE, NB_WHITE);
}

int rt_say_active(void) { return g_say != 0; }

void rt_say_end(void) {
    if (!g_say) return;
    g_say = 0;
    rt_clear_box(SAY_COL - 1, SAY_ROW - 1, SAY_W + 2, SAY_H + 2);
}

/* One frame of revealing. Returns 1 while a message is still on screen. */
int rt_say_step(void) {
    char one[2];
    if (!g_say) return 0;
    if (g_say_hold) {
        if (rt_key_pressed(KEY_A)) {
            g_say_hold = 0;
            if (!g_say[g_say_at]) { rt_say_end(); return 0; }
            /* a {p} page break: wipe the panel and carry on below it */
            if (g_vwf_on) { vwf_clear(); vwf_flush(); }
            else rt_clear_box(SAY_COL, SAY_ROW, SAY_W, SAY_H);
            g_say_col = 0; g_say_row = 0; g_say_px = 0;
        }
        return 1;
    }
    if (g_say_wait) { g_say_wait--; return 1; }

    for (;;) {
        char c = g_say[g_say_at];
        if (!c) { g_say_hold = 1; return 1; }     /* end: wait for A */
        g_say_at++;
        if (c == '\n') {
            g_say_col = 0;
            if (++g_say_row >= SAY_H) { g_say_row = SAY_H - 1; }
            continue;
        }
        /* Wrap at the START of a word that will not fit, not at the column
           it happens to reach. Breaking mid-word is the single most obvious
           thing a text box can get wrong. */
        if (c != ' ' && (g_vwf_on ? g_say_px : g_say_col) > 0) {
            /* Measured in whatever unit the cursor is in. Wrapping a
               proportional line by CELLS would break it early by however much
               narrower the words happened to be. */
            u16 wl = g_vwf_on
                   ? say_word_px(g_say, (u16)(g_say_at - 1))
                   : say_word_len(g_say, (u16)(g_say_at - 1));
            u16 lim = g_vwf_on ? (VWF_COLS * 8) : SAY_W;
            u16 at = g_vwf_on ? g_say_px : g_say_col;
            if (at + wl > lim && wl <= lim) {
                g_say_col = 0;
                g_say_px = 0;
                if (++g_say_row >= SAY_H) g_say_row = SAY_H - 1;
            }
        }
        if (c == ' ' && g_say_col == 0) continue;   /* no leading space */
        if (c == '{') {
            char k = g_say[g_say_at];
            u16 i = (u16)(g_say_at + 1);
            s32 v = 0;
            if (k == 'p' && g_say[i] == '}') {
                g_say_at = (u16)(i + 1);
                g_say_hold = 1;
                return 1;
            }
            if (g_say[i] == ':' ) {
                u16 save = i;
                i++;
                if (say_num(g_say, &i, &v) && g_say[i] == '}') {
                    g_say_at = (u16)(i + 1);
                    if (k == 's') { g_say_speed = (u8)v; continue; }
                    if (k == 'c') { g_say_ink = (u8)v; continue; }
                    if (k == 'w') { g_say_wait = (u16)v; return 1; }
                    if (k == 'v') {
                        /* the number, digit by digit, through the same cursor */
                        s32 n = (v >= 0 && v < NB_MAX_GLOBALS)
                                ? nb_global[v] : 0;
                        char buf[12];
                        int len = 0, j;
                        if (n < 0) { buf[len++] = '-'; n = -n; }
                        if (n == 0) buf[len++] = '0';
                        else {
                            char tmp[11]; int tn = 0;
                            while (n > 0 && tn < 10) { tmp[tn++] = (char)('0' + n % 10); n /= 10; }
                            for (j = tn - 1; j >= 0; j--) buf[len++] = tmp[j];
                        }
                        for (j = 0; j < len; j++) {
                            one[0] = buf[j]; one[1] = 0;
                            rt_draw_text_c(SAY_COL + g_say_col,
                                           SAY_ROW + g_say_row, one, g_say_ink);
                            if (++g_say_col >= SAY_W) { g_say_col = 0; g_say_row++; }
                        }
                        continue;
                    }
                    continue;
                }
                i = save;                        /* not a code after all */
            }
            /* An unknown code prints as written. Swallowing it would erase the
               rest of a sentence over a typo. */
        }
        one[0] = c; one[1] = 0;
        if (g_vwf_on) {
            g_say_px = (u16)(g_say_px
                             + vwf_glyph(g_say_px, g_say_row,
                                         (unsigned char)c, g_say_ink));
            vwf_flush();
        } else {
            rt_draw_text_c(SAY_COL + g_say_col, SAY_ROW + g_say_row, one,
                           g_say_ink);
        }
        if (g_say_voice >= 0 && c != ' ') rt_play_sound(g_say_voice);
        if (++g_say_col >= SAY_W) {
            g_say_col = 0;
            g_say_px = 0;
            if (++g_say_row >= SAY_H) g_say_row = SAY_H - 1;
        }
        if (g_say_speed) { g_say_wait = g_say_speed; return 1; }
        /* speed 0: keep going, the whole line lands in one frame */
    }
}

/* ---- the profiler --------------------------------------------------------
 * Where the frame went, in ticks of timer 2.
 *
 * TIMER 2 BECAUSE THE OTHERS ARE SPOKEN FOR: timer 0 is the project's (the
 * Help's interrupt example arms it), timer 1 clocks sampled audio. Taking
 * either would make profiling break the thing being profiled.
 *
 * TM_FREQ_64 because a frame is 280,896 cycles and a 16-bit counter is not:
 * at one tick per cycle the counter wraps six times a frame and every reading
 * is nonsense. At 64 cycles a tick a frame is 4,389 ticks, which fits with
 * room to spare.
 *
 * The counter is reset once a frame rather than read as a free-running value,
 * so a section that straddles the reset cannot report a negative cost.
 */
#define PROF_SLOTS   8
#define PROF_FRAME   4389           /* ticks in one 60 Hz frame at TM_FREQ_64 */

enum { PROF_STEP = 0, PROF_MOVE, PROF_DRAW, PROF_USER };

static u16 g_prof_open[PROF_SLOTS];
static u16 g_prof_acc[PROF_SLOTS];
static u16 g_prof_last[PROF_SLOTS];
static u8  g_prof_on;

void rt_prof(int on) {
    int i;
    g_prof_on = (u8)(on ? 1 : 0);
    for (i = 0; i < PROF_SLOTS; i++) {
        g_prof_open[i] = g_prof_acc[i] = g_prof_last[i] = 0;
    }
    if (g_prof_on) rt_timer_start(2, 65536u, TM_FREQ_64);
    else rt_timer_stop(2);
}

void rt_prof_begin(int slot) {
    if (!g_prof_on || (unsigned)slot >= PROF_SLOTS) return;
    g_prof_open[slot] = rt_timer_read(2);
}

void rt_prof_end(int slot) {
    u16 now;
    if (!g_prof_on || (unsigned)slot >= PROF_SLOTS) return;
    now = rt_timer_read(2);
    /* Unsigned subtraction is correct across a wrap, which is why these are
       u16 and not int: a section measured across the counter's roll-over must
       report its length, not a huge negative. */
    g_prof_acc[slot] = (u16)(g_prof_acc[slot] + (u16)(now - g_prof_open[slot]));
}

/* Ticks the slot cost during the LAST whole frame. Reading the accumulator
   mid-frame would give a figure that changes depending on when it was asked. */
int rt_prof_ticks(int slot) {
    if ((unsigned)slot >= PROF_SLOTS) return 0;
    return g_prof_last[slot];
}

int rt_prof_percent(int slot) {
    return rt_prof_ticks(slot) * 100 / PROF_FRAME;
}

static void prof_frame(void) {
    int i;
    if (!g_prof_on) return;
    for (i = 0; i < PROF_SLOTS; i++) {
        g_prof_last[i] = g_prof_acc[i];
        g_prof_acc[i] = 0;
    }
    rt_timer_start(2, 65536u, TM_FREQ_64);      /* restart the count */
}

/* A corner read-out: the three engine phases and the total, as percentages.
   Drawn only when asked for -- a profiler that is always on is a profiler
   measuring itself. */
void rt_prof_overlay(void) {
    int total;
    if (!g_prof_on) return;
    total = rt_prof_percent(PROF_STEP) + rt_prof_percent(PROF_MOVE)
          + rt_prof_percent(PROF_DRAW);
    rt_draw_text_c(0, 0, "STP", NB_WHITE);
    rt_draw_int_pad(4, 0, rt_prof_percent(PROF_STEP), 2, 0);
    rt_draw_text_c(7, 0, "MOV", NB_WHITE);
    rt_draw_int_pad(11, 0, rt_prof_percent(PROF_MOVE), 2, 0);
    rt_draw_text_c(14, 0, "DRW", NB_WHITE);
    rt_draw_int_pad(18, 0, rt_prof_percent(PROF_DRAW), 2, 0);
    rt_draw_text_c(21, 0, "ALL", NB_WHITE);
    rt_draw_int_pad(25, 0, total, 3, 0);
    /* Second row: how full the instance pool is, and how many creates it has
       already refused. A bullet pattern that quietly stops firing looks like a
       logic bug until this number is seen climbing. */
    rt_draw_text_c(0, 1, "OBJ", NB_WHITE);
    rt_draw_int_pad(4, 1, rt_instance_count(-1), 3, 0);
    rt_draw_text_c(8, 1, "/", NB_WHITE);
    rt_draw_int_pad(10, 1, NB_MAX_INSTANCES, 3, 0);
    rt_draw_text_c(14, 1, "LOST", g_create_refused ? NB_RED : NB_WHITE);
    rt_draw_int_pad(19, 1, (s32)g_create_refused, 4, 0);
}

/* ---- cutscenes -----------------------------------------------------------
 * Two things a scripted scene needs that a Step event cannot express without
 * a counter and a pile of branches.
 *
 * GLIDE: move an instance to a point over N frames. Written by hand this is a
 * start position, a target, a frame count and a division per axis per frame --
 * four of the twelve variables an instance has, spent on arithmetic. Held in
 * the engine it costs nothing per object.
 *
 * The remaining distance is divided by the remaining FRAMES rather than
 * stepping by a precomputed amount, so rounding cannot leave the instance a
 * pixel short of where the scene said it would be: the last frame always lands
 * exactly on the target. */
void rt_glide(Instance *in, s32 x, s32 y, s32 frames) {
    if (!in) return;
    if (frames <= 0) {
        in->x = (s16)x; in->y = (s16)y;
        in->glide = 0;
        return;
    }
    in->gx = (s16)x;
    in->gy = (s16)y;
    in->glide = (u16)(frames > 65535 ? 65535 : frames);
    /* A glide overrides speed: two things moving one instance is a fight
       nobody can see the cause of. */
    in->hspeed = 0; in->vspeed = 0;
    in->hspd8 = 0; in->vspd8 = 0;
}

int rt_gliding(Instance *in) { return in && in->glide ? 1 : 0; }

void rt_glide_stop(Instance *in) { if (in) in->glide = 0; }

static void glide_step(Instance *in) {
    s32 dx, dy;
    if (!in->glide) return;
    dx = (s32)in->gx - in->x;
    dy = (s32)in->gy - in->y;
    if (in->glide == 1) {
        in->x = in->gx;          /* the last frame lands exactly */
        in->y = in->gy;
    } else {
        in->x = (s16)(in->x + dx / (s32)in->glide);
        in->y = (s16)(in->y + dy / (s32)in->glide);
    }
    in->glide--;
}

/* ---- input lock ---
 * A cutscene that leaves the player able to walk out of it is a cutscene about
 * an empty room. The lock is read by rt_key_held and rt_key_pressed rather
 * than by clearing the key state, so a game can still ask what is held -- the
 * pause menu needs that while everything else is frozen. */
static u8 g_input_lock;
void rt_input_lock(int on) { g_input_lock = (u8)(on ? 1 : 0); }
int  rt_input_locked(void) { return g_input_lock; }

/* ---- menus ---------------------------------------------------------------
 * A list with a cursor: the interface a game of any size is mostly made of.
 *
 * NON-BLOCKING, like the dialogue engine and for the same reason: a menu that
 * spins its own loop stops the music, the animation and the link cable while
 * it is open. rt_menu_step is called once a frame and reports what happened.
 *
 * IT DRAWS ONLY WHEN SOMETHING CHANGED. Rewriting the panel every frame is
 * about 200 tile writes a frame for a picture that is identical to the last
 * one, and on this CPU that is a real fraction of the budget spent on nothing.
 */
#define MENU_MAX_ROWS 8

static const char *const *g_menu_items;
static int  g_menu_n, g_menu_at, g_menu_top, g_menu_rows;
static u8   g_menu_col, g_menu_row, g_menu_w, g_menu_open, g_menu_dirty;
static u8   g_menu_wrap = 1;

void rt_menu_open(const char *const *items, int n, int col, int row, int w) {
    if (!items || n <= 0) return;
    g_menu_items = items;
    g_menu_n = n;
    g_menu_at = 0;
    g_menu_top = 0;
    g_menu_col = (u8)col;
    g_menu_row = (u8)row;
    g_menu_w = (u8)(w < 4 ? 4 : w);
    g_menu_rows = n < MENU_MAX_ROWS ? n : MENU_MAX_ROWS;
    g_menu_open = 1;
    g_menu_dirty = 1;
}

/* Where to put the answer when the menu closes.
 *
 * A menu spans frames and an ACTION does not: a row of the sheet cannot wait
 * for a choice. So the action opens the menu and names a variable, the engine
 * writes the answer there when it closes, and the next Step event branches on
 * it with an ordinary If Variable. That keeps the whole interaction inside the
 * vocabulary somebody using the sheet already has. */
static Instance *g_menu_who;
static int g_menu_slot = -1;

void rt_menu_open_var(const char *const *items, int n, int col, int row, int w,
                      Instance *who, int slot) {
    rt_menu_open(items, n, col, row, w);
    if (!g_menu_open) return;
    g_menu_who = who;
    g_menu_slot = (slot >= 0 && slot < NB_MAX_VARS) ? slot : -1;
    /* -1 while the menu is up, so a Step event can tell "still choosing" from
       "chose the first item". Without this the sheet reads a stale 0 and acts
       on a choice nobody made. */
    if (g_menu_who && g_menu_slot >= 0)
        g_menu_who->var[g_menu_slot] = -1;
}

static void menu_answer(int v) {
    if (g_menu_who && g_menu_slot >= 0 && g_menu_who->active)
        g_menu_who->var[g_menu_slot] = v;
    g_menu_who = 0;
    g_menu_slot = -1;
}

void rt_menu_close(void) {
    if (!g_menu_open) return;
    g_menu_open = 0;
    rt_clear_box(g_menu_col - 1, g_menu_row - 1,
                 g_menu_w + 2, (u8)(g_menu_rows + 2));
}

int  rt_menu_active(void) { return g_menu_open; }
int  rt_menu_index(void)  { return g_menu_at; }
void rt_menu_wrap(int on)  { g_menu_wrap = (u8)(on ? 1 : 0); }

static void menu_draw(void) {
    int i;
    rt_draw_panel(g_menu_col - 1, g_menu_row - 1, g_menu_w + 2,
                  g_menu_rows + 2, NB_BLUE, NB_WHITE);
    for (i = 0; i < g_menu_rows; i++) {
        int item = g_menu_top + i;
        rt_clear_box(g_menu_col, g_menu_row + i, g_menu_w, 1);
        if (item >= g_menu_n) continue;
        /* The cursor is a character in the same cell grid as the text, so it
           cannot drift out of line with the row it points at. */
        rt_draw_text_c(g_menu_col, g_menu_row + i,
                       item == g_menu_at ? ">" : " ", NB_WHITE);
        rt_draw_text_c(g_menu_col + 1, g_menu_row + i,
                       g_menu_items[item], NB_WHITE);
    }
    /* More above or below than fits: say so, or a long list looks like a
       short one and the rest of it is never found. */
    if (g_menu_top > 0)
        rt_draw_text_c(g_menu_col + g_menu_w - 1, g_menu_row, "^", NB_WHITE);
    if (g_menu_top + g_menu_rows < g_menu_n)
        rt_draw_text_c(g_menu_col + g_menu_w - 1,
                       g_menu_row + g_menu_rows - 1, "v", NB_WHITE);
}

/* -1 while open, the chosen index on A, -2 on B. */
int rt_menu_step(void) {
    int moved = 0;
    if (!g_menu_open) return -2;
    if (rt_key_pressed(KEY_UP)) {
        if (g_menu_at > 0) { g_menu_at--; moved = 1; }
        else if (g_menu_wrap) { g_menu_at = g_menu_n - 1; moved = 1; }
    }
    if (rt_key_pressed(KEY_DOWN)) {
        if (g_menu_at < g_menu_n - 1) { g_menu_at++; moved = 1; }
        else if (g_menu_wrap) { g_menu_at = 0; moved = 1; }
    }
    if (moved) {
        /* Keep the cursor in view. Scrolling by a whole page instead would
           lose the item the player was looking at. */
        if (g_menu_at < g_menu_top) g_menu_top = g_menu_at;
        if (g_menu_at >= g_menu_top + g_menu_rows)
            g_menu_top = g_menu_at - g_menu_rows + 1;
        g_menu_dirty = 1;
    }
    if (g_menu_dirty) { menu_draw(); g_menu_dirty = 0; }
    if (rt_key_pressed(KEY_A)) {
        int at = g_menu_at;
        rt_menu_close();
        menu_answer(at);
        return at;
    }
    if (rt_key_pressed(KEY_B)) {
        rt_menu_close();
        menu_answer(-2);
        return -2;
    }
    return -1;
}

/* ---- the cartridge clock -------------------------------------------------
 * A Seiko S-3511A on the CARTRIDGE. The console has no clock of its own, so a
 * game reading the date is asking the cartridge it happens to be in -- which
 * is why every call here reports failure rather than returning a plausible
 * date. A day-night cycle that silently believes it is midnight on the 1st of
 * January is worse than one that knows it cannot tell the time.
 *
 * VERIFIED HERE: the command encoding, the BCD conversion, the field ranges,
 * and that a cartridge with no clock is rejected rather than believed.
 * NOT VERIFIED: the bit-banged transfer itself, which needs the chip. The
 * protocol below follows the S-3511A's published sequence; it has not been run
 * against hardware, and this comment is here so nobody assumes otherwise. */
#define RTC_CMD_RESET   0
#define RTC_CMD_STATUS  1
#define RTC_CMD_DATE    2       /* 7 bytes: yy mm dd wd hh mm ss, all BCD */
#define RTC_CMD_TIME    3       /* 3 bytes: hh mm ss */

/* The command byte: a fixed 0110 prefix, the command, then read or write.
   Getting the prefix wrong makes the chip ignore everything and the game reads
   a clock that never advances. */
static u8 rtc_cmd(u8 index, int read) {
    return (u8)(0x60 | ((index & 7) << 1) | (read ? 1 : 0));
}

static u8 bcd_to_bin(u8 v) { return (u8)((v >> 4) * 10 + (v & 0x0F)); }

static void rtc_pins(u16 dir) {
    REG_GPIO_CTRL = GPIO_READABLE;
    REG_GPIO_DIR = dir;
}

static void rtc_write_byte(u8 v) {
    for (int i = 0; i < 8; i++) {
        u16 bit = (u16)((v >> (7 - i)) & 1) << 1;   /* MSB first on SIO */
        REG_GPIO_DATA = (u16)(GPIO_CS | bit);        /* clock low */
        REG_GPIO_DATA = (u16)(GPIO_CS | bit | GPIO_SCK);
    }
}

static u8 rtc_read_byte(void) {
    u8 v = 0;
    for (int i = 0; i < 8; i++) {
        REG_GPIO_DATA = GPIO_CS;                     /* clock low */
        REG_GPIO_DATA = (u16)(GPIO_CS | GPIO_SCK);
        v = (u8)((v << 1) | ((REG_GPIO_DATA >> 1) & 1));
    }
    return v;
}

/* A date the chip cannot have produced. Checked because an absent clock does
   not answer with an error -- it answers with whatever the bus floats to, and
   0xFF everywhere is a perfectly readable "255th of the 255th". */
static int rtc_sane(const u8 *d) {
    if (d[1] < 1 || d[1] > 12) return 0;        /* month */
    if (d[2] < 1 || d[2] > 31) return 0;        /* day */
    if (d[3] > 6) return 0;                      /* weekday */
    if (d[4] > 23 || d[5] > 59 || d[6] > 59) return 0;
    return 1;
}

int rt_rtc_read(nb_DateTime *out) {
    u8 raw[7];
    if (!out) return 0;
    rtc_pins(GPIO_SCK | GPIO_SIO | GPIO_CS);     /* all three driven by us */
    REG_GPIO_DATA = 0;
    REG_GPIO_DATA = GPIO_SCK;
    REG_GPIO_DATA = (u16)(GPIO_SCK | GPIO_CS);   /* select */
    rtc_write_byte(rtc_cmd(RTC_CMD_DATE, 1));
    rtc_pins(GPIO_SCK | GPIO_CS);                /* SIO becomes an input */
    for (int i = 0; i < 7; i++) raw[i] = rtc_read_byte();
    REG_GPIO_DATA = GPIO_SCK;                    /* deselect */
    rtc_pins(0);

    for (int i = 0; i < 7; i++) raw[i] = bcd_to_bin(raw[i] & 0x7F);
    if (!rtc_sane(raw)) return 0;
    out->year = (u16)(2000 + raw[0]);
    out->month = raw[1];
    out->day = raw[2];
    out->weekday = raw[3];
    out->hour = raw[4];
    out->minute = raw[5];
    out->second = raw[6];
    return 1;
}

int rt_rtc_present(void) {
    nb_DateTime t;
    return rt_rtc_read(&t);
}

/* ---- the link cable ------------------------------------------------------
 * Multiplayer mode: two to four units, one halfword from each per transfer.
 *
 * THE SHAPE THAT WORKS. A frame of full game state is not transmissible -- at
 * 9600 baud the whole session shares about 16 bytes per frame. What fits is
 * INPUT: every unit sends its own buttons, every unit receives all four, and
 * every unit runs the same simulation on the same inputs. That is why this is
 * one halfword and not an API for sending objects.
 *
 * Nothing here blocks. A transfer takes real time and a game that waits for it
 * drops frames on a cable that is merely slow, so rt_link_poll reports what
 * arrived and the caller carries on. */
static u16 g_link_in[4];
static u8  g_link_ok;           /* a transfer has completed at least once */

int rt_link_open(u16 baud) {
    /* Both registers, in this order. RCNT first, or the port stays in whatever
       mode it was in -- on an RTC cartridge that is GPIO, and the link then
       does nothing at all with no error anywhere. */
    REG_RCNT = RCNT_SIO;
    REG_SIOCNT = (u16)(SIO_MULTI | (baud & 3));
    g_link_ok = 0;
    /* 0xFFFF from the very start, because that is what the header
       promises for a unit that has not answered -- and it is what the
       hardware itself reports in SIOMULTIn for absent units after a
       transfer. Initialised to 0 this read as "unit present, sent 0"
       until the first transfer completed, so the documented absence
       check recv(n) == 0xFFFF saw a phantom player. */
    for (int i = 0; i < 4; i++) g_link_in[i] = 0xFFFF;
    /* SD reads 1 only when every unit is connected and in multiplayer mode. */
    return (REG_SIOCNT & SIO_SD_READY) ? 1 : 0;
}

void rt_link_close(void) {
    REG_SIOCNT = 0;
    REG_RCNT = RCNT_SIO;
    g_link_ok = 0;
    /* The port is closed: nobody is answering, and the buffer says so. */
    for (int i = 0; i < 4; i++) g_link_in[i] = 0xFFFF;
}

int rt_link_ready(void) { return (REG_SIOCNT & SIO_SD_READY) ? 1 : 0; }
int rt_link_parent(void) { return (REG_SIOCNT & SIO_SI_CHILD) ? 0 : 1; }
int rt_link_id(void) { return (REG_SIOCNT & SIO_ID_MASK) >> SIO_ID_SHIFT; }
int rt_link_busy(void) { return (REG_SIOCNT & SIO_START) ? 1 : 0; }

/* What this unit will send on the next transfer. Latched, not sent: only the
   parent starts a transfer, and a child that tried would be talking over it. */
void rt_link_send(u16 word) { REG_SIOMLT_SEND = word; }

int rt_link_start(void) {
    if (!rt_link_parent()) return 0;       /* a child may not start one */
    if (rt_link_busy()) return 0;          /* one is already running */
    REG_SIOCNT |= SIO_START;
    return 1;
}

/* Collect a finished transfer. Returns 1 when new words arrived.

   The error flag is checked BEFORE the data: a failed transfer leaves the
   previous contents in the registers, so reading without checking hands the
   game last frame's input as though it were this frame's, and the two units
   drift apart with nothing to show for it. */
int rt_link_poll(void) {
    if (rt_link_busy()) return 0;
    if (REG_SIOCNT & SIO_ERROR) {
        /* Left for the hardware to clear when the next transfer starts. An
           earlier version wrote the bit back on the belief that it was
           write-1-to-clear; that is not something this code can rely on, and a
           wrong claim about a register is worse than no claim. What matters
           here is only that the data is NOT read. */
        return 0;
    }
    g_link_in[0] = REG_SIOMULTI0;
    g_link_in[1] = REG_SIOMULTI1;
    g_link_in[2] = REG_SIOMULTI2;
    g_link_in[3] = REG_SIOMULTI3;
    g_link_ok = 1;
    return 1;
}

/* 0xFFFF is what an absent unit reads as, and it is also a legal word. Callers
   that need to tell them apart use rt_link_players(). */
u16 rt_link_recv(int unit) {
    if (unit < 0 || unit > 3) return 0xFFFF;
    return g_link_in[unit];
}

int rt_link_players(void) {
    int n = 0;
    if (!g_link_ok) return 0;
    for (int i = 0; i < 4; i++)
        if (g_link_in[i] != 0xFFFF) n++;
    return n;
}

/* ---- sampled audio (Direct Sound) ---------------------------------------
 * TWO PCM voices. Direct Sound A carries one-shot samples -- voices, impacts,
 * anything an author drops on a play_sound action. Direct Sound B carries a
 * LOOPING sample: a recorded soundtrack that keeps playing underneath while A
 * fires effects over it, which is the arrangement a sampled-music game needs
 * and one FIFO cannot provide.
 *
 * ONE TIMER FOR BOTH, timer 1, on purpose twice over. Each FIFO may be clocked
 * by timer 0 or 1; timer 0 is the one the Help's interrupt example arms, and
 * taking it would break that example the moment a project played a sample.
 * And both voices sharing a clock means starting the second voice must NOT
 * re-arm the timer -- that would hiccup the first voice's playback -- so the
 * timer starts only when no voice was active, and stops only when the last
 * voice goes quiet.
 *
 * RATE IS FIXED at 16384 Hz. The GBA has no resampler: the timer period IS
 * the sample rate. One rate, converted on import, removes a whole class of
 * "it plays too fast".
 *
 * The DMAs repeat forever; A is stopped by counting frames (an unstopped
 * sample loops its buffer, which sounds like a stuck note), and B loops by
 * design until told otherwise. */
#define PCM_RATE    16384u
#define PCM_PERIOD  (16777216u / PCM_RATE)      /* 1024 cycles per sample */

/* ---- the one-shot mixer (voice A) ----------------------------------------
 * Four one-shot samples audible AT ONCE, summed in software into a double
 * buffer that Direct Sound A streams. Before this, a second rt_pcm_play cut
 * the first mid-note: a footstep silenced a sword, a sword silenced a voice.
 *
 * The cadence is the part with a trap in it. 16384 Hz against 60 frames is
 * 273.07 samples a frame: mixing a fixed 273 starves the FIFO by four
 * samples a second, a fixed 274 overruns it. The mixer owes four EXTRA
 * samples a second, paid one at a time: every 15th frame mixes 274. The
 * DMA is re-pointed at the fresh half each VBlank -- inside the blank, so
 * the swap is never audible as a click.
 *
 * Summing saturates. Two loud samples clamp at full scale; wrapping would
 * turn their loudest instant into a spike of the opposite sign, which is a
 * CRACK where the design says LOUD. */
#define MIX_VOICES  4
#define MIX_MAX     304                 /* room for 274 with margin */
static s8  g_mixbuf[2][MIX_MAX] __attribute__((aligned(4)));
static u8  g_mixcur;                    /* which half the DMA is playing */
static u8  g_mixphase;                  /* 0..14; phase 0 mixes the extra sample */
typedef struct { const signed char* data; u32 pos, len; u8 on; } MixVoice;
static MixVoice g_mixv[MIX_VOICES];

static u32 g_pcm_b_frames;      /* voice B frames left (ignored when looping) */
static u8  g_pcm_b_loop;        /* voice B plays until rt_pcm_stop_b */
static u8  g_mix_running;       /* the A-side stream is armed */

static void pcm_timer_sync(void) {
    if (g_mix_running || g_pcm_b_frames || g_pcm_b_loop) {
        if (!(REG_TM1CNT_H & TM_ENABLE))
            rt_timer_start(1, PCM_PERIOD, 0);
    } else {
        rt_timer_stop(1);
    }
}

/* Sum every live voice into dst. IWRAM: this is 274 samples x 4 voices at
   60 fps, the hottest loop the audio owns. */
IWRAM_CODE static void mix_block(s8* dst, int n) {
    for (int i = 0; i < n; i++) {
        s32 acc = 0;
        for (int v = 0; v < MIX_VOICES; v++) {
            MixVoice* mv = &g_mixv[v];
            if (!mv->on) continue;
            acc += mv->data[mv->pos];
            if (++mv->pos >= mv->len) mv->on = 0;
        }
        if (acc > 127) acc = 127;
        if (acc < -128) acc = -128;
        dst[i] = (s8)acc;
    }
}

static void mix_arm_dma(const s8* buf) {
    REG_DMA1CNT = 0;
    REG_DMA1SAD = (u32)buf;
    REG_DMA1DAD = (u32)&REG_FIFO_A;
    REG_DMA1CNT = DMA_DST_FIX | DMA_SRC_INC | DMA_REPEAT | DMA_32
                  | DMA_SPECIAL | DMA_ENABLE;
}

static void mix_start(void) {
    if (g_mix_running) return;
    REG_SOUNDCNT_H |= (u16)(PSG_VOL_FULL | DSA_VOL_FULL | DSA_RIGHT
                            | DSA_LEFT | DSA_TIMER1 | DSA_RESET);
    for (int i = 0; i < MIX_MAX; i++) { g_mixbuf[0][i] = 0; g_mixbuf[1][i] = 0; }
    g_mixcur = 0;
    g_mixphase = 0;
    mix_arm_dma(g_mixbuf[0]);
    g_mix_running = 1;
    pcm_timer_sync();
}

void rt_pcm_stop(void) {
    for (int v = 0; v < MIX_VOICES; v++) g_mixv[v].on = 0;
    if (g_mix_running) {
        REG_DMA1CNT = 0;
        REG_SOUNDCNT_H &= (u16)~(DSA_VOL_FULL | DSA_RIGHT | DSA_LEFT
                                 | DSA_TIMER1 | DSA_RESET);
        REG_SOUNDCNT_H |= PSG_VOL_FULL;
        g_mix_running = 0;
    }
    pcm_timer_sync();
}

void rt_pcm_stop_b(void) {
    REG_DMA2CNT = 0;
    REG_SOUNDCNT_H &= (u16)~(DSB_VOL_FULL | DSB_RIGHT | DSB_LEFT
                             | DSB_TIMER1 | DSB_RESET);
    g_pcm_b_frames = 0;
    g_pcm_b_loop = 0;
    pcm_timer_sync();
}

void rt_pcm_play(const void *data, u32 nsamples) {
    if (!data || nsamples < 16) return;
    mix_start();
    /* A free voice, or the one closest to finishing: stealing the nearly
       done voice loses the least audible material. */
    int pick = -1;
    u32 least = 0xFFFFFFFFu;
    for (int v = 0; v < MIX_VOICES; v++) {
        if (!g_mixv[v].on) { pick = v; break; }
        u32 left = g_mixv[v].len - g_mixv[v].pos;
        if (left < least) { least = left; pick = v; }
    }
    g_mixv[pick].data = (const signed char*)data;
    g_mixv[pick].len = nsamples;
    g_mixv[pick].pos = 0;
    g_mixv[pick].on = 1;
}

/* The soundtrack voice. loop=0 plays the buffer once; loop=1 plays it until
   rt_pcm_stop_b() or rt_stop_music() -- a looping sample IS the music, so the
   call that silences music silences it. */
void rt_pcm_play_b(const void *data, u32 nsamples, int loop) {
    if (!data || nsamples < 16) return;
    rt_pcm_stop_b();
    REG_SOUNDCNT_H |= (u16)(PSG_VOL_FULL | DSB_VOL_FULL | DSB_RIGHT
                            | DSB_LEFT | DSB_TIMER1 | DSB_RESET);
    REG_DMA2CNT = 0;
    REG_DMA2SAD = (u32)data;
    REG_DMA2DAD = (u32)&REG_FIFO_B;
    REG_DMA2CNT = DMA_DST_FIX | DMA_SRC_INC | DMA_REPEAT | DMA_32
                  | DMA_SPECIAL | DMA_ENABLE;
    g_pcm_b_loop = (u8)(loop != 0);
    g_pcm_b_frames = loop ? 0 : (nsamples * 60u) / PCM_RATE + 1u;
    pcm_timer_sync();
}

int rt_pcm_playing(void) {
    for (int v = 0; v < MIX_VOICES; v++)
        if (g_mixv[v].on) return 1;
    return 0;
}
int rt_pcm_playing_b(void) { return g_pcm_b_loop || g_pcm_b_frames != 0; }

/* Called once per frame from the main loop, inside the VBlank: mix the
   half the DMA is NOT playing, then hand the DMA the freshly mixed one.
   Every 15th frame carries the extra sample that keeps 273.07 honest. */
static void rt_pcm_tick(void) {
    if (g_mix_running) {
        int n = 273 + (g_mixphase == 0);
        if (++g_mixphase >= 15) g_mixphase = 0;
        u8 next = (u8)(g_mixcur ^ 1);
        mix_block(g_mixbuf[next], n);
        mix_arm_dma(g_mixbuf[next]);
        g_mixcur = next;
    }
    if (!g_pcm_b_loop && g_pcm_b_frames && --g_pcm_b_frames == 0)
        rt_pcm_stop_b();
}

/* ---- colour blending -----------------------------------------------------
 * Alpha needs BOTH sets of layers named: what is blended, and what it is
 * blended with. Naming only the first produces no visible change and no error,
 * which reads as "blending does not work". */
void rt_blend_alpha(u16 top, u16 bottom, int eva, int evb) {
    if (eva < 0) eva = 0;
    if (eva > 16) eva = 16;
    if (evb < 0) evb = 0;
    if (evb > 16) evb = 16;
    REG_BLDCNT = (u16)((top & 0x003F) | (bottom & 0x3F00) | BLD_ALPHA);
    REG_BLDALPHA = (u16)((evb << 8) | eva);
}

void rt_blend_brightness(u16 layers, int amount) {
    /* amount: -16 fully black .. 0 none .. +16 fully white */
    int mag = amount < 0 ? -amount : amount;
    if (mag > 16) mag = 16;
    if (mag == 0) { REG_BLDCNT = BLD_OFF; REG_BLDY = 0; return; }
    REG_BLDCNT = (u16)((layers & 0x003F) | (amount < 0 ? BLD_BLACK : BLD_WHITE));
    REG_BLDY = (u16)mag;
}

void rt_blend_off(void) {
    REG_BLDCNT = BLD_OFF;
    REG_BLDY = 0;
}

/* ---- windows -------------------------------------------------------------
 * A window is a rectangle plus two layer masks: what is drawn inside it and
 * what is drawn everywhere else.
 *
 * The clamping is not tidiness. The hardware treats a right edge of 0, or one
 * that has wrapped past the left, as the full 240 -- so a window given a width
 * of zero covers the WHOLE SCREEN, which is the opposite of what was asked
 * for and looks like the window feature being broken. */
void rt_window(int n, int x, int y, int w, int h, u16 inside, u16 outside) {
    int x2, y2;
    if (n != 0 && n != 1) return;
    if (w < 0) w = 0;
    if (h < 0) h = 0;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x > 240) x = 240;
    if (y > 160) y = 160;
    x2 = x + w;
    if (x2 > 240) x2 = 240;
    if (x2 < x) x2 = x;
    y2 = y + h;
    if (y2 > 160) y2 = 160;
    if (y2 < y) y2 = y;
    if (n == 0) {
        REG_WIN0H = (u16)((x << 8) | x2);
        REG_WIN0V = (u16)((y << 8) | y2);
    } else {
        REG_WIN1H = (u16)((x << 8) | x2);
        REG_WIN1V = (u16)((y << 8) | y2);
    }
    if (n == 0)
        REG_WININ = (u16)((REG_WININ & 0x3F00) | (inside & 0x003F));
    else
        REG_WININ = (u16)((REG_WININ & 0x003F) | ((inside & 0x003F) << 8));
    REG_WINOUT = (u16)((REG_WINOUT & 0x3F00) | (outside & 0x003F));
    /* Into g_dispcnt, not the register: the frame loop ends every VBlank
       with REG_DISPCNT = g_dispcnt, so a bit set only in the register
       lasts one frame -- the same silent revert the display mode had. */
    g_dispcnt |= (u16)(n == 0 ? WIN0_ON : WIN1_ON);
    REG_DISPCNT = g_dispcnt;
}

void rt_window_off(int n) {
    if (n == 0) g_dispcnt &= (u16)~WIN0_ON;
    else if (n == 1) g_dispcnt &= (u16)~WIN1_ON;
    else g_dispcnt &= (u16)~(WIN0_ON | WIN1_ON | WINOBJ_ON);
    REG_DISPCNT = g_dispcnt;
}

/* The sprite-shaped window. Any instance marked with rt_set_objwin stops
 * being DRAWN and becomes a stencil: `inside` names the layers visible where
 * its opaque pixels fall. A spotlight that is actually torch-shaped, water
 * that ripples the layers behind a sprite-shaped mask -- the effect the two
 * rectangle windows cannot make. The outside-of-all-windows content is set
 * by rt_window(); this touches only the OBJ window's own bits. */
void rt_window_obj(int inside) {
    REG_WINOUT = (u16)((REG_WINOUT & 0x003F) | ((inside & 0x003F) << 8));
    g_dispcnt |= WINOBJ_ON;
    REG_DISPCNT = g_dispcnt;
}

void rt_window_obj_off(void) {
    g_dispcnt &= (u16)~WINOBJ_ON;
    REG_DISPCNT = g_dispcnt;
}

void rt_set_objwin(Instance* self, int on) {
    if (self) self->objwin = (u8)(on != 0);
}

/* ---- mosaic --------------------------------------------------------------
 * Sizes are 1..16 in the API and 0..15 in the register, because 0 there means
 * "blocks of one", i.e. off. Passing 0 through unchanged would make the two
 * ends of the range mean the same thing. */
void rt_mosaic(int bg_w, int bg_h, int obj_w, int obj_h) {
    int bw = bg_w - 1, bh = bg_h - 1, ow = obj_w - 1, oh = obj_h - 1;
    if (bw < 0) bw = 0;
    if (bw > 15) bw = 15;
    if (bh < 0) bh = 0;
    if (bh > 15) bh = 15;
    if (ow < 0) ow = 0;
    if (ow > 15) ow = 15;
    if (oh < 0) oh = 0;
    if (oh > 15) oh = 15;
    REG_MOSAIC = (u16)(bw | (bh << 4) | (ow << 8) | (oh << 12));
}

/* ---- affine --------------------------------------------------------------
 * The one piece of GBA arithmetic most worth having written once.
 *
 * The P matrix maps SCREEN space to TEXTURE space -- the INVERSE of the
 * transform being pictured. Two consequences catch everyone: scaling up
 * divides, and the X/Y registers hold where texture (0,0) lands rather than
 * the centre of anything. Setting X/Y to the rotation centre makes the picture
 * swing around the screen instead of turning on the spot.
 *
 * rt_bg_affine states the intent instead: put texture pixel (tx,ty) at screen
 * pixel (sx,sy), turned by `angle` (0..255 for a full turn) and scaled by
 * `scale` in 8.8 where 256 is life size. */
static void affine_matrix(s32 angle, s32 scale, s32 *pa, s32 *pb,
                          s32 *pc, s32 *pd) {
    s32 inv, co, si;
    if (scale <= 0) scale = 1;          /* a zero scale divides by zero */
    inv = (256 * 256) / scale;          /* 8.8 of 1/scale */
    co = rt_cos8(angle);
    si = rt_sin8(angle);
    *pa = (co * inv) >> 8;
    *pb = (-si * inv) >> 8;
    *pc = (si * inv) >> 8;
    *pd = (co * inv) >> 8;
}

/* The display mode, kept where the frame loop can see it.
 *
 * Writing REG_DISPCNT directly does not survive: rt_flush() ends every frame
 * with `REG_DISPCNT = g_dispcnt`, so a mode set from game code was reverted on
 * the next VBlank -- the picture changed for one frame and then silently went
 * back. Anything that means to change mode has to change g_dispcnt.
 *
 * The BG enable and mapping bits stay the runtime's; only the mode is the
 * game's to choose. Note that BG2/BG3 being AFFINE in modes 1 and 2 is a
 * property of the mode, but their map data is not: an affine background reads
 * 8-bit tile indices in a layout the generator does not yet emit, so mode 1
 * gives you an affine BG2 over whatever is in its screenblock. Authoring
 * affine maps is Phase 7. */
void rt_video_mode(int mode) {
    if (mode < 0 || mode > 5) return;
    g_dispcnt = (u16)((g_dispcnt & (u16)~7) | (u16)mode);
    REG_DISPCNT = g_dispcnt;
}

int rt_video_mode_get(void) { return g_dispcnt & 7; }

/* ---- bitmap modes 3, 4 and 5 ---------------------------------------------
 * A framebuffer on BG2: the one way to draw a shape the tileset and the sprite
 * sheet have no entry for. The SPEC wants it for particle fields, where the
 * count is past what 128 OAM entries can carry (Part IV.1, technique 3).
 *
 * Everything here reads the mode and the page out of g_dispcnt rather than
 * taking them as arguments, for the same reason rt_video_mode exists: the
 * frame loop ends every VBlank with `REG_DISPCNT = g_dispcnt`, so state kept
 * anywhere else is reverted a frame later with nothing to say so. One place,
 * and the loop already knows about it.
 *
 * The page arithmetic is the part worth stating plainly. Page 0 is at
 * 0x06000000 and page 1 at 0x0600A000, and DISPCNT bit 4 chooses which one the
 * hardware DISPLAYS. Drawing therefore goes to the OTHER one -- the hidden
 * page -- and rt_bitmap_flip presents it. A renderer that draws into the page
 * being scanned out shows every half-finished shape it makes. */
static int bitmap_w(void) { return (g_dispcnt & 7) == 5 ? M5_W : M3_W; }
static int bitmap_h(void) { return (g_dispcnt & 7) == 5 ? M5_H : M3_H; }

/* Where drawing lands: 0 or 1. Mode 3's buffer is 75 KiB and runs straight
   through 0x0600A000, so it has one page and that page is the visible one --
   in mode 3 there is nothing to hide behind. */
int rt_bitmap_page(void) {
    int mode = g_dispcnt & 7;
    if (mode != 4 && mode != 5) return 0;
    return (g_dispcnt & DCNT_PAGE) ? 0 : 1;
}

static volatile u16* bitmap_base(void) {
    return rt_bitmap_page() ? VRAM_PAGE1 : VRAM_PAGE0;
}

/* Enter a bitmap mode: 3, 4 or 5. BG2 carries the framebuffer in all three and
   is switched on here, because a mode change alone leaves the screen blank and
   looks like the bitmap call having done nothing. The displayed page is reset
   to 0 so a game entering mode 4 or 5 starts by drawing into page 1, hidden,
   which is the order double buffering has to be used in. */
void rt_bitmap_mode(int mode) {
    if (mode < 3 || mode > 5) return;
    rt_video_mode(mode);                  /* the mode bits, where the loop reads them */
    g_dispcnt = (u16)((g_dispcnt | BG2_ON) & (u16)~DCNT_PAGE);
    REG_DISPCNT = g_dispcnt;
}

/* Show the page just drawn and start drawing into the other one. Modes 4 and 5
   only; mode 3 has a single page and this is deliberately a no-op there rather
   than a flip that swaps the framebuffer for tile memory.
   The swap takes effect on the next scanline, so a tear-free present is
   rt_vsync() and then this. */
void rt_bitmap_flip(void) {
    int mode = g_dispcnt & 7;
    if (mode != 4 && mode != 5) return;
    g_dispcnt ^= DCNT_PAGE;
    REG_DISPCNT = g_dispcnt;
}

/* Mode 4's one hardware trap, and it is a silent one.
 *
 * VRAM HAS NO 8-BIT WRITE PORT. A byte store to VRAM is silently dropped, so
 * a tempting `pixels[y * 240 + x] = index` draws nothing at all. The only
 * correct 8bpp plot is a read-modify-write of the halfword the pixel shares:
 * keep the neighbour's byte, replace this pixel's, then issue one 16-bit store.
 *
 * These two span helpers are where that lives for runs of pixels. The middle
 * of a span is written a pair at a time, which needs no read at all; only the
 * odd first and last pixels share a halfword with something that must survive.
 * x is even/odd in SOURCE pixels, and the halfword index is x >> 1. */
static void bitmap_span4(int x, int y, int w, u8 idx) {
    volatile u16 *row = bitmap_base() + y * (M4_W / 2);
    u16 pair = (u16)(idx | (idx << 8));
    if (w <= 0) return;
    if (x & 1) {                          /* shares its halfword with x-1 */
        volatile u16 *p = &row[x >> 1];
        *p = (u16)((*p & 0x00FF) | ((u16)idx << 8));
        x++; w--;
    }
    while (w >= 2) { row[x >> 1] = pair; x += 2; w -= 2; }
    if (w > 0) {                          /* shares its halfword with x+1 */
        volatile u16 *p = &row[x >> 1];
        *p = (u16)((*p & 0xFF00) | idx);
    }
}

static void bitmap_row4(int x, int y, int w, const u8 *s) {
    volatile u16 *row = bitmap_base() + y * (M4_W / 2);
    if (w <= 0) return;
    if (x & 1) {
        volatile u16 *p = &row[x >> 1];
        *p = (u16)((*p & 0x00FF) | ((u16)*s++ << 8));
        x++; w--;
    }
    while (w >= 2) {
        row[x >> 1] = (u16)(s[0] | ((u16)s[1] << 8));
        s += 2; x += 2; w -= 2;
    }
    if (w > 0) {
        volatile u16 *p = &row[x >> 1];
        *p = (u16)((*p & 0xFF00) | *s);
    }
}

/* One pixel. `colour` is BGR555 in modes 3 and 5 and a palette index 0..255 in
   mode 4. Out of bounds is dropped rather than wrapped: a wrapped pixel comes
   out on the far side of the row above, which reads as a rendering bug rather
   than as coordinates that left the screen. */
void rt_bitmap_pixel(int x, int y, u16 colour) {
    int mode = g_dispcnt & 7;
    if (mode < 3 || mode > 5) return;
    if (x < 0 || y < 0 || x >= bitmap_w() || y >= bitmap_h()) return;
    if (mode == 4) { bitmap_span4(x, y, 1, (u8)colour); return; }
    bitmap_base()[y * bitmap_w() + x] = colour;
}

/* A filled rectangle, clipped to the screen. A rectangle that starts off the
   left edge keeps the part that is on screen, so a particle drifting out of
   view thins rather than jumping. */
void rt_bitmap_rect(int x, int y, int w, int h, u16 colour) {
    int mode = g_dispcnt & 7, bw = bitmap_w(), bh = bitmap_h(), r;
    if (mode < 3 || mode > 5) return;
    if (w <= 0 || h <= 0) return;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x >= bw || y >= bh) return;
    if (x + w > bw) w = bw - x;
    if (y + h > bh) h = bh - y;
    if (w <= 0 || h <= 0) return;
    if (mode == 4) {
        for (r = 0; r < h; r++) bitmap_span4(x, y + r, w, (u8)colour);
        return;
    }
    {
        volatile u16 *base = bitmap_base();
        for (r = 0; r < h; r++) {
            volatile u16 *p = base + (y + r) * bw + x;
            for (int c = 0; c < w; c++) p[c] = colour;
        }
    }
}

/* The whole drawing page. In mode 4 the index is doubled into both bytes of
   each halfword, so the fill needs no read at all. */
void rt_bitmap_clear(u16 colour) {
    int mode = g_dispcnt & 7, i, n;
    volatile u16 *base;
    if (mode < 3 || mode > 5) return;
    base = bitmap_base();
    if (mode == 4) {
        colour = (u16)((colour & 0xFF) | ((colour & 0xFF) << 8));
        n = (M4_W * M4_H) / 2;
    } else {
        n = bitmap_w() * bitmap_h();
    }
    for (i = 0; i < n; i++) base[i] = colour;
}

/* A w-by-h image at (x,y), clipped. `src` is read in the mode's own pixel
   size -- u16 of colour in modes 3 and 5, one byte of palette index in mode 4
   -- with a stride of w, and clipping walks INTO the source rather than
   dropping rows, so a sprite half off the left edge shows its right half
   rather than its left half moved. */
void rt_bitmap_blit(int x, int y, int w, int h, const void *src) {
    int mode = g_dispcnt & 7, bw = bitmap_w(), bh = bitmap_h();
    int sx = 0, sy = 0, stride = w, r;
    if (mode < 3 || mode > 5 || !src) return;
    if (w <= 0 || h <= 0) return;
    if (x < 0) { sx = -x; w += x; x = 0; }
    if (y < 0) { sy = -y; h += y; y = 0; }
    if (x >= bw || y >= bh) return;
    if (x + w > bw) w = bw - x;
    if (y + h > bh) h = bh - y;
    if (w <= 0 || h <= 0) return;
    if (mode == 4) {
        const u8 *s = (const u8*)src;
        for (r = 0; r < h; r++)
            bitmap_row4(x, y + r, w, s + (sy + r) * stride + sx);
        return;
    }
    {
        const u16 *s = (const u16*)src;
        volatile u16 *base = bitmap_base();
        for (r = 0; r < h; r++) {
            volatile u16 *d = base + (y + r) * bw + x;
            const u16 *sr = s + (sy + r) * stride + sx;
            for (int c = 0; c < w; c++) d[c] = sr[c];
        }
    }
}

void rt_bg_affine(int bg, s32 tx, s32 ty, s32 sx, s32 sy,
                  s32 angle, s32 scale) {
    s32 pa, pb, pc, pd, x, y;
    if (bg != 2 && bg != 3) return;
    affine_matrix(angle, scale, &pa, &pb, &pc, &pd);
    /* Texture (tx,ty) must land on screen (sx,sy):
       X = (tx << 8) - (PA*sx + PB*sy), same for Y with PC/PD. */
    x = (tx << 8) - (pa * sx + pb * sy);
    y = (ty << 8) - (pc * sx + pd * sy);
    if (bg == 2) {
        REG_BG2PA = (s16)pa; REG_BG2PB = (s16)pb;
        REG_BG2PC = (s16)pc; REG_BG2PD = (s16)pd;
        REG_BG2X = x; REG_BG2Y = y;
    } else {
        REG_BG3PA = (s16)pa; REG_BG3PB = (s16)pb;
        REG_BG3PC = (s16)pc; REG_BG3PD = (s16)pd;
        REG_BG3X = x; REG_BG3Y = y;
    }
}

void rt_obj_affine(int group, s32 angle, s32 scale) {
    s32 pa, pb, pc, pd;
    if (group < 0 || group > 31) return;
    affine_matrix(angle, scale, &pa, &pb, &pc, &pd);
    OAM_AFF_PA(group) = (s16)pa;
    OAM_AFF_PB(group) = (s16)pb;
    OAM_AFF_PC(group) = (s16)pc;
    OAM_AFF_PD(group) = (s16)pd;
}

/* ---- timers --------------------------------------------------------------
 * Four 16-bit up-counters. A timer counts UP to overflow, so the reload value
 * is 65536 minus the ticks wanted — rt_timer_start takes the period and does
 * that arithmetic, because getting it backwards is the classic way to get a
 * timer that fires at the wrong rate and looks like a tuning problem.
 *
 * This is what DMA audio is clocked by: a timer set to the sample period
 * requests a FIFO refill from DMA1/2 (see rt_dma_sound). Nobody writing a song
 * ever sees this — the generator configures it. */
static volatile u16 *const g_tm_l[4] = {
    &REG_TM0CNT_L, &REG_TM1CNT_L, &REG_TM2CNT_L, &REG_TM3CNT_L };
static volatile u16 *const g_tm_h[4] = {
    &REG_TM0CNT_H, &REG_TM1CNT_H, &REG_TM2CNT_H, &REG_TM3CNT_H };

void rt_timer_start(int ch, u32 ticks, u16 flags) {
    if (ch < 0 || ch > 3) return;
    *g_tm_h[ch] = 0;                        /* stop before re-arming */
    if (ticks == 0 || ticks > 65536u) ticks = 65536u;
    *g_tm_l[ch] = (u16)(65536u - ticks);    /* counts UP to overflow */
    *g_tm_h[ch] = (u16)(flags | TM_ENABLE);
}

void rt_timer_stop(int ch) {
    if (ch < 0 || ch > 3) return;
    *g_tm_h[ch] = 0;
}

u16 rt_timer_read(int ch) {
    if (ch < 0 || ch > 3) return 0;
    return *g_tm_l[ch];
}

/* ---- DMA ------------------------------------------------------------------
 * rt_dma is the general form; rt_hdma_start is the one that matters for
 * effects. An HBlank DMA repeats every scanline with no CPU involvement, which
 * is how a per-line scroll table (water, heat haze, Mode-7-ish floors) or a
 * per-line palette is done. It must be armed during VBlank and the source
 * table must hold one entry per visible line. */
static volatile u32 *const g_dma_s[4] = {
    &REG_DMA0SAD, &REG_DMA1SAD, &REG_DMA2SAD, &REG_DMA3SAD };
static volatile u32 *const g_dma_d[4] = {
    &REG_DMA0DAD, &REG_DMA1DAD, &REG_DMA2DAD, &REG_DMA3DAD };
static volatile u32 *const g_dma_c[4] = {
    &REG_DMA0CNT, &REG_DMA1CNT, &REG_DMA2CNT, &REG_DMA3CNT };

void rt_dma(int ch, void *dst, const void *src, u32 count, u32 flags) {
    if (ch < 0 || ch > 3) return;
    *g_dma_c[ch] = 0;                       /* disable before re-pointing */
    *g_dma_s[ch] = (u32)src;
    *g_dma_d[ch] = (u32)dst;
    *g_dma_c[ch] = (count & 0xFFFF) | flags | DMA_ENABLE;
}

void rt_dma_stop(int ch) {
    if (ch < 0 || ch > 3) return;
    *g_dma_c[ch] = 0;
}

void rt_hdma_start(int ch, void *reg, const void *table, u32 units_per_line) {
    /* DST_RELOAD + REPEAT + HBLANK: the destination resets each frame and one
     * unit is written per scanline. */
    rt_dma(ch, reg, table, units_per_line,
           DMA_16 | DMA_REPEAT | DMA_HBLANK | DMA_DST_RELOAD | DMA_SRC_INC);
}

/* ---- BIOS -----------------------------------------------------------------
 * THE TRAP, and it is silent: in ARM state the BIOS call number sits in bits
 * 23-16, so LZ77UnCompVram is `swi 0x120000`. Writing `swi 0x12` assembles to
 * call 0, SoftReset, and the cartridge simply reboots — which reads as a crash
 * with no error anywhere. Every call below is shifted.
 *
 * The compressed formats are what make 16 MB of graphics fit: the BIOS does
 * LZ77, Huffman and run-length in hardware-speed code that costs no ROM. */
void rt_lz77_vram(const void *src, void *dst) {
    register const void *r0 __asm__("r0") = src;
    register void *r1 __asm__("r1") = dst;
    __asm__ volatile("swi 0x120000" :: "r"(r0), "r"(r1)
                     : "r2", "r3", "memory");
}

void rt_lz77_wram(const void *src, void *dst) {
    register const void *r0 __asm__("r0") = src;
    register void *r1 __asm__("r1") = dst;
    __asm__ volatile("swi 0x110000" :: "r"(r0), "r"(r1)
                     : "r2", "r3", "memory");
}

void rt_huff(const void *src, void *dst) {
    register const void *r0 __asm__("r0") = src;
    register void *r1 __asm__("r1") = dst;
    __asm__ volatile("swi 0x130000" :: "r"(r0), "r"(r1)
                     : "r2", "r3", "memory");
}

void rt_rl_vram(const void *src, void *dst) {
    register const void *r0 __asm__("r0") = src;
    register void *r1 __asm__("r1") = dst;
    __asm__ volatile("swi 0x150000" :: "r"(r0), "r"(r1)
                     : "r2", "r3", "memory");
}

s32 rt_div(s32 num, s32 den) {
    register s32 r0 __asm__("r0") = num;
    register s32 r1 __asm__("r1") = den;
    if (den == 0) return 0;             /* the BIOS hangs on a zero divisor */
    __asm__ volatile("swi 0x060000" : "+r"(r0), "+r"(r1) :: "r3");
    return r0;
}

u16 rt_sqrt(u32 v) {
    register u32 r0 __asm__("r0") = v;
    __asm__ volatile("swi 0x080000" : "+r"(r0) :: "r1", "r2", "r3");
    return (u16)r0;
}

static void rt_vsync(void) {
    /* SWI 5, VBlankIntrWait: halts the CPU until the next VBlank instead of
     * spinning on VCOUNT through the whole frame. In ARM state the BIOS call
     * number lives in bits 23-16, so it is 0x050000 — `swi 0x05` here would
     * silently call SWI 0 (SoftReset) and reboot the cartridge. */
    __asm__ volatile("swi 0x050000" ::: "r0", "r1", "r2", "r3", "memory");
}

/* ---------------- input ---------------- */
static void rt_input_update(void) {
    g_keys_prev = g_keys;
    g_keys = (u16)((~REG_KEYINPUT) & KEY_ANY);   /* active-high */
}

/* The lock is applied HERE rather than by clearing the key state, so
   rt_key_raw still answers -- a pause menu has to work while everything else
   is frozen, and a menu that reads its own keys through a lock cannot. */
int rt_key_held(u16 key)     { return !g_input_lock && (g_keys & key) != 0; }
int rt_key_pressed(u16 key)  { return !g_input_lock && (g_keys & key)
                                      && !(g_keys_prev & key); }
int rt_key_released(u16 key) { return !g_input_lock && !(g_keys & key)
                                      && (g_keys_prev & key); }
int rt_key_raw(u16 key)      { return (g_keys & key) != 0; }
int rt_key_raw_pressed(u16 key) { return (g_keys & key)
                                         && !(g_keys_prev & key); }

/* ================================ sound ================================
   Square 1 and square 2 carry the music's lead and bass, the noise channel its
   drums, and the programmable wave channel plays sound effects. Because effects
   live on their own channel, a coin or a jump never silences the music — the
   single most noticeable thing missing from a first home-made game. */

/* GBA square-channel frequency register per MIDI note (0 => too low to play).
   The wave channel runs an octave lower for the same register value, so note N
   on the wave channel uses the entry for N+12. */
static const u16 nb_note_freq[128] = {
       0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,
       0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,
       0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,    0,
      44,  157,  263,  363,  457,  547,  631,  711,  786,  856,  923,  986,
    1046, 1102, 1155, 1205, 1253, 1297, 1339, 1379, 1417, 1452, 1486, 1517,
    1547, 1575, 1602, 1627, 1650, 1673, 1694, 1714, 1732, 1750, 1767, 1783,
    1798, 1812, 1825, 1837, 1849, 1860, 1871, 1881, 1890, 1899, 1907, 1915,
    1923, 1930, 1936, 1943, 1949, 1954, 1959, 1964, 1969, 1974, 1978, 1982,
    1985, 1989, 1992, 1995, 1998, 2001, 2004, 2006, 2009, 2011, 2013, 2015,
    2017, 2018, 2020, 2022, 2023, 2025, 2026, 2027, 2028, 2029, 2030, 2031,
    2032, 2033, 2034, 2035, 2036, 2036, 2037, 2038,
};

/* music slot */
static s16 g_mus = -1;
static u16 g_mus_step, g_mus_frame;
static u8  g_mus_vol = 7;
/* authored-sound-effect slot (the wave channel) */
static s16 g_sfxt = -1;
static u16 g_sfxt_step, g_sfxt_frame;
/* The priority of whatever is sounding on the wave channel. The channel does
   one thing at a time, and "last one wins" means a footstep cuts off a death
   the frame after it starts -- the reason priority exists at all. */
static u8 g_sfx_prio;

/* Built-in effects carry their own priority: a death or an explosion should
   not be silenced by the footstep that follows it. Indexed by NB_SFX_*. */
static const u8 nb_fx_prio[NB_SFX_COUNT] = {
    1,  /* BLIP    */  2,  /* JUMP    */  3,  /* COIN    */
    2,  /* SHOOT   */  5,  /* HURT    */  6,  /* EXPLODE */
    5,  /* POWERUP */  1,  /* LAND    */  3,  /* SELECT  */
    4,  /* ERROR   */  4,  /* WARP    */  0,  /* STEP    */
};
/* built-in sound-effect slot (the little synth below) */
static s16 g_fx = -1;
static u8  g_fx_stage, g_fx_frame;
static s16 g_fx_note4;      /* current pitch, in quarter-semitones */

static void rt_sound_init(void) {
    REG_SOUNDCNT_X = 0x0080;      /* master sound enable */
    REG_SOUNDCNT_L = 0xFF77;      /* all four channels to both speakers */
    REG_SOUNDCNT_H = 0x0002;      /* PSG at 100% output */
    /* the wave channel's waveform: a 32-sample sawtooth, bright enough to cut
       through the two squares. Both banks get it, so whichever is selected
       plays the same shape. */
    for (int bank = 0; bank < 2; bank++) {
        REG_SOUND3CNT_L = (u16)(bank ? 0x0040 : 0x0000);
        for (int i = 0; i < 8; i++) {
            /* four samples per halfword: high nibble of the low byte first */
            int s0 = 15 - (i * 4 + 0) / 2, s1 = 15 - (i * 4 + 1) / 2;
            int s2 = 15 - (i * 4 + 2) / 2, s3 = 15 - (i * 4 + 3) / 2;
            WAVE_RAM[i] = (u16)((s0 << 4) | s1 | (s2 << 12) | (s3 << 8));
        }
    }
    REG_SOUND3CNT_L = 0x0000;
    REG_SOUND3CNT_H = 0x0000;
}

/* Retrigger a square channel: ch 0 = square 1 (lead), 1 = square 2 (bass).
   midi <= 0 silences it. */
static void rt_square(int ch, int midi, u16 duty, u16 vol, u16 decay) {
    u16 env, f;
    if (midi > 0 && midi < 128 && nb_note_freq[midi]) {
        u16 v = vol ? vol : 15;
        v = (u16)((v * g_mus_vol) / 7);
        if (!v) v = 1;
        u16 d = duty ? (u16)(duty - 1) : 2;      /* 0 => 50%, the old default */
        env = (u16)((v << 12) | (d << 6) | ((decay & 7) << 8));
        f = nb_note_freq[midi];
    } else {
        env = 0x0000; f = 0;      /* volume 0 = silent */
    }
    if (ch == 0) {
        REG_SOUND1CNT_L = 0;      /* no sweep */
        REG_SOUND1CNT_H = env;
        REG_SOUND1CNT_X = (u16)(0x8000 | f);
    } else {
        REG_SOUND2CNT_L = env;
        REG_SOUND2CNT_H = (u16)(0x8000 | f);
    }
}

/* The noise channel: percussion, and the percussive built-in effects. `shift`
   sets the pitch (higher = deeper), `decay` the hardware envelope fall-off. */
static void rt_noise(int shift, int width, int div, int vol, int decay) {
    if (shift < 0) shift = 0;
    if (shift > 13) shift = 13;
    if (vol <= 0) { REG_SOUND4CNT_L = 0; return; }
    if (vol > 15) vol = 15;
    REG_SOUND4CNT_L = (u16)((vol << 12) | ((decay & 7) << 8));
    REG_SOUND4CNT_H = (u16)(0x8000 | (shift << 4) | ((width & 1) << 3)
                            | (div & 7));
}

static void rt_drum(int code) {
    switch (code) {
    case 1: rt_noise(6, 0, 0, 13, 1); break;   /* kick  */
    case 2: rt_noise(3, 0, 0, 11, 2); break;   /* snare */
    case 3: rt_noise(1, 1, 0,  7, 1); break;   /* hat   */
    case 4: rt_noise(4, 0, 1, 12, 4); break;   /* crash */
    default: break;
    }
}

/* The wave channel, used for every sound effect. `vol_code` is 1 = full,
   2 = half, 3 = quarter, 0 = off, which is all the hardware offers — a decay
   is stepped in software by the effect players below. */
static void rt_wave(int midi, int vol_code) {
    if (midi <= 0 || vol_code <= 0) {
        REG_SOUND3CNT_L = 0x0000;
        REG_SOUND3CNT_H = 0x0000;
        return;
    }
    int reg_note = midi + 12;
    if (reg_note > 127) reg_note = 127;
    u16 f = nb_note_freq[reg_note];
    if (!f) return;
    REG_SOUND3CNT_L = 0x0080;                       /* one bank, playback on */
    REG_SOUND3CNT_H = (u16)((vol_code & 3) << 13);
    REG_SOUND3CNT_X = (u16)(0x8000 | f);
}

/* ---- the built-in effects: a two-stage pitch envelope, no data required ---- */
typedef struct {
    u8 ch;        /* 0 = wave channel (tonal), 1 = noise channel (percussive) */
    u8 n1, f1;    /* stage 1: MIDI note (or noise shift), length in frames */
    s8 s1;        /* stage 1 slide, quarter-semitones (or quarter-shifts)/frame */
    u8 n2, f2;
    s8 s2;
    u8 vol;       /* noise channel only: 1..15 */
    u8 nparam;    /* noise channel only: (7-bit width << 3) | divider ratio */
} nb_Fx;

static const nb_Fx nb_fx[NB_SFX_COUNT] = {
    /*  ch  n1  f1   s1   n2  f2   s2  vol nparam */
    {   0,  84,  4,   0,   0,  0,   0,   0, 0 },   /* BLIP    */
    {   0,  55, 12,  10,   0,  0,   0,   0, 0 },   /* JUMP    */
    {   0,  84,  4,   0,  91, 10,   0,   0, 0 },   /* COIN    */
    {   0,  96, 10, -14,   0,  0,   0,   0, 0 },   /* SHOOT   */
    {   0,  72, 14,  -7,   0,  0,   0,   0, 0 },   /* HURT    */
    {   1,   2, 30,   1,   0,  0,   0,  13, 0 },   /* EXPLODE */
    {   0,  60,  8,   6,  79, 10,   6,   0, 0 },   /* POWERUP */
    {   1,   6,  8,   3,   0,  0,   0,  10, 0 },   /* LAND    */
    {   0,  79,  3,   0,  86,  5,   0,   0, 0 },   /* SELECT  */
    {   0,  55,  6,   0,  46, 12,   0,   0, 0 },   /* ERROR   */
    {   0,  45, 26,   5,   0,  0,   0,   0, 0 },   /* WARP    */
    {   1,   8,  4,   0,   0,  0,   0,   6, 8 },   /* STEP    */
};

void rt_stop_sfx(void) {
    g_sfxt = -1; g_fx = -1; g_sfx_prio = 0;
    rt_wave(0, 0);
}

void rt_stop_music(void) {
    g_mus = -1;
    rt_square(0, 0, 0, 0, 0);
    rt_square(1, 0, 0, 0, 0);
    REG_SOUND4CNT_L = 0;
    rt_pcm_stop_b();               /* a looping sample is the music too */
}

void rt_music_volume(int vol) {
    if (vol < 0) vol = 0;
    if (vol > 7) vol = 7;
    g_mus_vol = (u8)vol;
}

void rt_play_music(s16 sound) {
    if (sound < 0) { rt_stop_music(); return; }
    if (sound >= nb_sound_count) return;
    g_mus = sound; g_mus_step = 0; g_mus_frame = 0;
}

/* Is the wave channel free for a sound of this priority?

   Equal priority still wins, so repeating the same effect restarts it rather
   than being swallowed -- a gun that fires twice must be heard twice. */
static int sfx_may_start(u8 prio) {
    if (g_sfxt < 0 && g_fx < 0) return 1;
    return prio >= g_sfx_prio;
}

void rt_play_sfx(s16 sound) {
    if (sound < 0) { rt_stop_sfx(); return; }
    if (sound >= nb_sound_count) return;
    u8 prio = nb_sounds[sound].prio;
    if (!sfx_may_start(prio)) return;
    g_fx = -1;                       /* the wave channel does one thing at a time */
    g_sfxt = sound; g_sfxt_step = 0; g_sfxt_frame = 0;
    g_sfx_prio = prio;
}

void rt_sfx(int preset) {
    if (preset < 0 || preset >= NB_SFX_COUNT) return;
    const nb_Fx* f = &nb_fx[preset];
    u8 prio = nb_fx_prio[preset];
    /* Only the wave-channel presets contend for it; the ones on their own
       channel never needed to wait and must not start doing so. */
    if (!f->ch && !sfx_may_start(prio)) return;
    g_fx = (s16)preset; g_fx_stage = 0; g_fx_frame = 0;
    g_fx_note4 = (s16)(f->n1 * 4);
    if (!f->ch) { g_sfxt = -1; g_sfx_prio = prio; }
}

/* The one call a game made before music and effects were separate channels:
   route it by the sound's own kind so old projects keep working and gain the
   layering for free. */
void rt_play_sound(s16 sound) {
    if (sound < 0) { rt_stop_music(); rt_stop_sfx(); rt_pcm_stop(); return; }
    if (sound >= nb_sound_count) return;
    /* Routing lives HERE, not in the generator, so that code calling
       rt_play_sound gets the same answer as the drag-drop action. Deciding it
       in the generator meant a sampled sound played its (empty) pattern
       whenever it was reached from C -- one rule, two behaviours. */
    const nb_Sound* s = &nb_sounds[sound];
    if (s->pcm && s->pcm_len >= 16) {
        /* A looping sample is a soundtrack: it belongs on the B voice, under
           whatever one-shot effects A fires over it. */
        if (s->pcm_loop) rt_pcm_play_b(s->pcm, s->pcm_len, 1);
        else rt_pcm_play(s->pcm, s->pcm_len);
        return;
    }
    if (s->kind) rt_play_sfx(sound);
    else rt_play_music(sound);
}

static void music_update(void) {
    if (g_mus < 0 || g_mus >= nb_sound_count) return;
    const nb_Sound* s = &nb_sounds[g_mus];
    if (g_mus_frame == 0) {
        if (g_mus_step >= s->nsteps) {
            if (s->loop) g_mus_step = 0;
            else { rt_stop_music(); return; }
        }
        u8 lead = s->lead ? s->lead[g_mus_step] : 0;
        u8 bass = s->bass ? s->bass[g_mus_step] : 0;
        u8 drum = s->drum ? s->drum[g_mus_step] : 0;
        if (lead != 255) rt_square(0, lead, s->duty, s->vol, s->decay);
        if (bass != 255) rt_square(1, bass, s->duty, s->vol, s->decay);
        if (drum && g_fx < 0) rt_drum(drum);   /* an effect owns noise while it runs */
        g_mus_step++;
    }
    if (++g_mus_frame >= (s->tempo ? s->tempo : 1)) g_mus_frame = 0;
}

static void sfxtrack_update(void) {
    if (g_sfxt < 0 || g_sfxt >= nb_sound_count) return;
    const nb_Sound* s = &nb_sounds[g_sfxt];
    if (g_sfxt_frame == 0) {
        if (g_sfxt_step >= s->nsteps) {
            if (!s->loop) g_sfx_prio = 0;   /* the channel is free again */
            if (s->loop) g_sfxt_step = 0;
            else { rt_stop_sfx(); return; }
        }
        u8 note = s->lead ? s->lead[g_sfxt_step] : 0;
        if (note != 255) rt_wave(note, 1);
        g_sfxt_step++;
    }
    if (++g_sfxt_frame >= (s->tempo ? s->tempo : 1)) g_sfxt_frame = 0;
}

static void fx_update(void) {
    if (g_fx < 0) return;
    const nb_Fx* f = &nb_fx[g_fx];
    u8 len = g_fx_stage ? f->f2 : f->f1;
    s8 slide = g_fx_stage ? f->s2 : f->s1;
    if (g_fx_frame >= len) {
        if (g_fx_stage == 0 && f->f2) {
            g_fx_stage = 1; g_fx_frame = 0; g_fx_note4 = (s16)(f->n2 * 4);
            slide = f->s2;
        } else {
            g_fx = -1;
            if (f->ch) REG_SOUND4CNT_L = 0; else rt_wave(0, 0);
            return;
        }
    }
    int note = g_fx_note4 / 4;
    if (f->ch) {
        /* percussive: retrigger the noise channel only at the start of a stage,
           and let its hardware envelope do the decay */
        if (g_fx_frame == 0) rt_noise(note, (f->nparam >> 3) & 1, f->nparam & 7,
                                     f->vol, 3);
        else if (slide && (g_fx_frame & 3) == 0)
            rt_noise(note, (f->nparam >> 3) & 1, f->nparam & 7,
                     f->vol > 4 ? f->vol - 4 : 1, 3);
    } else {
        /* tonal: step the pitch every frame and fall away in three volume
           steps at the end, so an effect stops without a click */
        u8 total = (u8)(f->f1 + f->f2);
        u8 done = (u8)((g_fx_stage ? f->f1 : 0) + g_fx_frame);
        u8 left = (u8)(total > done ? total - done : 0);
        int vc = 1;
        if (left * 4 <= total) vc = 3;
        else if (left * 2 <= total) vc = 2;
        rt_wave(note, vc);
    }
    g_fx_note4 = (s16)(g_fx_note4 + slide);
    if (g_fx_note4 < 4) g_fx_note4 = 4;
    if (g_fx_note4 > 127 * 4) g_fx_note4 = 127 * 4;
    g_fx_frame++;
}

static void rt_sound_update(void) {
    music_update();
    sfxtrack_update();
    fx_update();
}

/* ---------------- instances ---------------- */
/* The instances nothing may pass through. Their boxes are cached once a step:
   recomputing them inside the per-pixel collision walk was, by a wide margin,
   the most expensive thing the engine did. */
static Instance* g_solid_list[NB_MAX_INSTANCES];
static s16 g_solid_box[NB_MAX_INSTANCES][4];
static int g_solid_n;

static void rt_clear_instances(void) {
    for (int i = 0; i < NB_MAX_INSTANCES; i++) g_inst[i].active = 0;
    g_solid_n = 0;
    g_other = 0;
}

Instance* rt_create(s16 object, s32 x, s32 y) {
    if (object < 0 || object >= nb_object_count) return 0;
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* in = &g_inst[i];
        if (in->active) continue;
        const nb_Object* ob = &nb_objects[object];
        in->active = 1;
        in->object = object;
        in->sprite = ob->sprite;
        in->image_index = 0;
        in->x = x; in->y = y;
        in->hspeed = 0; in->vspeed = 0; in->grav = 0;
        in->hspd8 = 0; in->vspd8 = 0; in->grav8 = 0;
        in->xsub = 0; in->ysub = 0;
        in->hidden = 0; in->flip = 0; in->flags = 0;
        in->depth = ob->depth & (NB_MAX_DEPTH - 1);
        in->angle = 0; in->scale = 0;
        in->anim_lo = 0; in->anim_hi = 0;
        in->image_speed = (in->sprite >= 0 && in->sprite < nb_sprite_count)
                          ? (s16)nb_sprites[in->sprite].anim_speed : 0;
        in->image_accum = 0;
        for (int a = 0; a < NB_MAX_ALARMS; a++) in->alarm[a] = -1;
        for (int v = 0; v < NB_MAX_VARS; v++) in->var[v] = 0;
        if (ob->create) ob->create(in);
        return in;
    }
    g_create_refused++;
    return 0;   /* instance pool full */
}

void rt_destroy(Instance* self) {
    if (!self || !self->active) return;
    if (self->object >= 0 && self->object < nb_object_count
        && nb_objects[self->object].destroy)
        nb_objects[self->object].destroy(self);
    self->active = 0;
    if (g_other == self) g_other = 0;
    if (g_view == self) g_view = 0;
}

void rt_destroy_object(s16 object) {
    for (int i = 0; i < NB_MAX_INSTANCES; i++)
        if (g_inst[i].active && (object < 0 || g_inst[i].object == object))
            rt_destroy(&g_inst[i]);
}

int rt_instance_count(s16 object) {
    int n = 0;
    for (int i = 0; i < NB_MAX_INSTANCES; i++)
        if (g_inst[i].active && (object < 0 || g_inst[i].object == object)) n++;
    return n;
}

Instance* rt_find(s16 object) {
    for (int i = 0; i < NB_MAX_INSTANCES; i++)
        if (g_inst[i].active && (object < 0 || g_inst[i].object == object))
            return &g_inst[i];
    return 0;
}

Instance* rt_nearest(Instance* self, s16 object) {
    Instance* best = 0;
    s32 bd = 0x7FFFFFFF;
    if (!self) return rt_find(object);
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* o = &g_inst[i];
        if (!o->active || o == self) continue;
        if (object >= 0 && o->object != object) continue;
        s32 dx = o->x - self->x, dy = o->y - self->y;
        s32 d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = o; }
    }
    return best;
}

Instance* rt_other(void) { return g_other; }

s32 rt_x_of(Instance* in) { return in ? in->x : 0; }
s32 rt_y_of(Instance* in) { return in ? in->y : 0; }

s32 rt_var_of(Instance* in, int slot) {
    if (!in || slot < 0 || slot >= NB_MAX_VARS) return 0;
    return in->var[slot];
}

void rt_set_var_of(Instance* in, int slot, s32 v) {
    if (!in || slot < 0 || slot >= NB_MAX_VARS) return;
    in->var[slot] = v;
}

/* ---------------- collision ---------------- */
/* An instance's collision box: the sprite's box, shrunk by the object's insets
   so a character with padding in its artwork does not collide with thin air. */
static void rt_bbox_at(Instance* in, s32 x, s32 y,
                       s32* l, s32* t, s32* r, s32* b) {
    s32 w = 16, h = 16, ox = 8, oy = 8;
    if (in->sprite >= 0 && in->sprite < nb_sprite_count) {
        const nb_Sprite* s = &nb_sprites[in->sprite];
        w = s->w; h = s->h; ox = s->ox; oy = s->oy;
    }
    s32 bl = 0, bt = 0, br = 0, bb = 0;
    if (in->object >= 0 && in->object < nb_object_count) {
        const nb_Object* ob = &nb_objects[in->object];
        bl = ob->bb_l; bt = ob->bb_t; br = ob->bb_r; bb = ob->bb_b;
    }
    s32 x0 = x - ox, y0 = y - oy;
    *l = x0 + bl;
    *t = y0 + bt;
    *r = x0 + w - 1 - br;
    *b = y0 + h - 1 - bb;
    /* The insets are per-edge and nothing checks them against the sprite's own
       size, so a facing pair can cross. Collapse the box INSIDE the sprite:
       clamping only `r` up to `l` leaves it sitting at the left inset, which
       for an inset wider than the sprite is a hit box floating in space clear
       of the thing it belongs to -- the instance would then collide with what
       it is nowhere near and pass through what it is touching. */
    if (*l > x0 + w - 1) *l = x0 + w - 1;
    if (*t > y0 + h - 1) *t = y0 + h - 1;
    if (*r < *l) *r = *l;
    if (*b < *t) *b = *t;
}

static void rt_bbox(Instance* in, s32* l, s32* t, s32* r, s32* b) {
    rt_bbox_at(in, in->x, in->y, l, t, r, b);
}

Instance* rt_meeting(Instance* self, s16 object) {
    /* Mercy frames: an object that declares hurt_frames feels no contact
       while its invincibility counts down. The skip lives HERE because
       collision events are folded into generated step code the engine cannot
       wrap -- but every one of them asks this function first. */
    if (self && self->inv && self->object >= 0
        && self->object < nb_object_count
        && nb_objects[self->object].hurt_frames)
        return 0;
    if (!self) return 0;
    s32 al, at, ar, ab; rt_bbox(self, &al, &at, &ar, &ab);
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* o = &g_inst[i];
        if (!o->active || o == self) continue;
        if (object >= 0 && o->object != object) continue;
        s32 bl, bt, br, bb; rt_bbox(o, &bl, &bt, &br, &bb);
        if (al <= br && bl <= ar && at <= bb && bt <= ab) { g_other = o; return o; }
    }
    return 0;
}

Instance* rt_place_meeting(Instance* self, s32 x, s32 y, s16 object) {
    /* Mercy frames: an object that declares hurt_frames feels no contact
       while its invincibility counts down. The skip lives HERE because
       collision events are folded into generated step code the engine cannot
       wrap -- but every one of them asks this function first. */
    if (self && self->inv && self->object >= 0
        && self->object < nb_object_count
        && nb_objects[self->object].hurt_frames)
        return 0;
    if (!self) return 0;
    s32 al, at, ar, ab; rt_bbox_at(self, x, y, &al, &at, &ar, &ab);
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* o = &g_inst[i];
        if (!o->active || o == self) continue;
        if (object >= 0 && o->object != object) continue;
        s32 bl, bt, br, bb; rt_bbox(o, &bl, &bt, &br, &bb);
        if (al <= br && bl <= ar && at <= bb && bt <= ab) { g_other = o; return o; }
    }
    return 0;
}

/* Is the room's tile layer solid at this tile cell? Outside the room counts as
   solid unless the room says its edge is open, so a game with tile collision
   cannot walk out of its own level by accident. */
IWRAM_CODE static int cell_solid(s32 tx, s32 ty) {
    if ((u32)tx >= (u32)g_room_cw || (u32)ty >= (u32)g_room_ch)
        return g_edge_solid;
    if (!g_has_solid) return 0;
    return g_solid_of[g_tiles[ty * g_room_cw + tx] & 0x01FF];
}

int rt_tile_solid(s32 px, s32 py) { return cell_solid(px >> 3, py >> 3); }

/* Every cell a box covers. The cell range is clamped once, so the inner loop is
   a row pointer walk with no bounds test per cell. */
IWRAM_CODE static int box_free(s32 l, s32 t, s32 r, s32 b) {
    if (g_edge_solid && (l < 0 || t < 0 || r >= g_room_w || b >= g_room_h))
        return 0;
    if (!g_has_solid) return 1;
    s32 tl = l >> 3, tt = t >> 3, tr = r >> 3, tb = b >> 3;
    if (tl < 0) tl = 0;
    if (tt < 0) tt = 0;
    if (tr >= g_room_cw) tr = g_room_cw - 1;
    if (tb >= g_room_ch) tb = g_room_ch - 1;
    for (s32 ty = tt; ty <= tb; ty++) {
        const u16* row = g_tiles + ty * g_room_cw;
        for (s32 tx = tl; tx <= tr; tx++)
            if (g_solid_of[row[tx] & 0x01FF]) return 0;
    }
    return 1;
}

int rt_place_free(Instance* self, s32 x, s32 y) {
    s32 l, t, r, b;
    if (!self) return 1;
    rt_bbox_at(self, x, y, &l, &t, &r, &b);
    return box_free(l, t, r, b);
}

/* Does this box overlap a solid instance? Uses the cached boxes. */
static int box_free_inst(Instance* self, s32 l, s32 t, s32 r, s32 b) {
    for (int i = 0; i < g_solid_n; i++) {
        if (g_solid_list[i] == self) continue;
        const s16* q = g_solid_box[i];
        if (l <= q[2] && q[0] <= r && t <= q[3] && q[1] <= b) return 0;
    }
    return 1;
}

int rt_place_free_all(Instance* self, s32 x, s32 y) {
    s32 l, t, r, b;
    if (!self) return 1;
    rt_bbox_at(self, x, y, &l, &t, &r, &b);
    if (!box_free(l, t, r, b)) return 0;
    return box_free_inst(self, l, t, r, b);
}

/* Only the strip of pixels a one-pixel move newly covers has to be tested, so a
   collision step near a wall costs two tile lookups instead of a box scan. */
static int col_free(s32 px, s32 t, s32 b) {
    if (g_edge_solid && (px < 0 || px >= g_room_w)) return 0;
    return box_free(px, t, px, b);
}

static int row_free(s32 py, s32 l, s32 r) {
    if (g_edge_solid && (py < 0 || py >= g_room_h)) return 0;
    return box_free(l, py, r, py);
}

int rt_blocked_h(Instance* self) { return self && (self->flags & NB_F_BLOCK_H); }
int rt_blocked_v(Instance* self) { return self && (self->flags & NB_F_BLOCK_V); }
int rt_on_ground(Instance* self) { return self && (self->flags & NB_F_GROUND); }

void rt_jump(Instance* self, s32 power) {
    if (!self || !(self->flags & NB_F_GROUND)) return;
    self->vspeed = -power;
    self->vspd8 = 0; self->ysub = 0;
    self->flags &= (u8)~NB_F_GROUND;
}

void rt_bounce(Instance* self) {
    if (!self) return;
    if (self->flags & NB_F_BLOCK_H) {
        self->hspeed = -self->hspeed;
        self->hspd8 = (s16)-self->hspd8;
    }
    if (self->flags & NB_F_BLOCK_V) {
        self->vspeed = -self->vspeed;
        self->vspd8 = (s16)-self->vspd8;
    }
}

/* ---------------- movement ---------------- */
static void set_vel8(Instance* self, s32 vx8, s32 vy8) {
    self->hspeed = vx8 / 256;
    self->vspeed = vy8 / 256;
    self->hspd8 = (s16)(vx8 - self->hspeed * 256);
    self->vspd8 = (s16)(vy8 - self->vspeed * 256);
}

/* Speed in 1/256 px per step, so an instance can crawl or travel at any angle. */
void rt_move_toward8(Instance* self, s32 tx, s32 ty, s32 speed8) {
    if (!self) return;
    s32 dx = tx - self->x, dy = ty - self->y;
    s32 d = hypot_i(dx, dy);
    if (d == 0) { set_vel8(self, 0, 0); return; }
    if (speed8 > 0x3FFF) speed8 = 0x3FFF;
    set_vel8(self, dx * speed8 / d, dy * speed8 / d);
}

/* The whole-pixel form kept for compatibility, but now exact: it moves at the
   requested speed in a true straight line instead of the old Manhattan
   approximation, which crept diagonally and stalled below 2 px a step. */
void rt_move_toward(Instance* self, s32 tx, s32 ty, s32 speed) {
    rt_move_toward8(self, tx, ty, speed * 256);
}

void rt_set_speed_dir(Instance* self, s32 dir, s32 speed8) {
    if (!self) return;
    if (speed8 > 0x3FFF) speed8 = 0x3FFF;
    set_vel8(self, rt_cos8(dir) * speed8 / 256, -rt_sin8(dir) * speed8 / 256);
}

void rt_chase(Instance* self, s16 object, s32 speed8) {
    Instance* t = rt_nearest(self, object);
    if (!t) { if (self) set_vel8(self, 0, 0); return; }
    rt_move_toward8(self, t->x, t->y, speed8);
}

s32 rt_dir_to(Instance* self, s32 tx, s32 ty) {
    if (!self) return 0;
    return dir_of(tx - self->x, ty - self->y);
}

s32 rt_dist_to(Instance* self, s32 tx, s32 ty) {
    if (!self) return 0;
    return hypot_i(tx - self->x, ty - self->y);
}

s32 rt_dist_to_object(Instance* self, s16 object) {
    Instance* t = rt_nearest(self, object);
    if (!t || !self) return 0x7FFF;
    return hypot_i(t->x - self->x, t->y - self->y);
}

/* ---------------- presentation ---------------- */
void rt_set_flip(Instance* self, int h, int v) {
    if (!self) return;
    self->flip = (u8)((h ? 1 : 0) | (v ? 2 : 0));
}

/* Mirror the sprite to match the way it is travelling, which is what makes a
   character look like it is facing where it walks with one drawn frame. */
void rt_face_motion(Instance* self) {
    if (!self) return;
    s32 vx = self->hspeed * 256 + self->hspd8;
    if (vx > 0) self->flip &= (u8)~1;
    else if (vx < 0) self->flip |= 1;
}

void rt_set_visible(Instance* self, int on) {
    if (self) self->hidden = on ? 0 : 1;
}

void rt_set_depth(Instance* self, int depth) {
    if (!self) return;
    if (depth < 0) depth = 0;
    if (depth >= NB_MAX_DEPTH) depth = NB_MAX_DEPTH - 1;
    self->depth = (u8)depth;
}

void rt_set_angle(Instance* self, s32 dir) {
    if (self) self->angle = (u8)(dir & 255);
}

void rt_set_scale(Instance* self, s32 scale8) {
    if (!self) return;
    if (scale8 < 16) scale8 = 16;          /* 1/16 size floor: below this the
                                              affine matrix overflows */
    if (scale8 > 4096) scale8 = 4096;
    self->scale = (s16)scale8;
}

void rt_anim_range(Instance* self, int lo, int hi) {
    if (!self) return;
    if (lo < 0) lo = 0;
    if (hi < lo) hi = lo;
    self->anim_lo = (u8)lo;
    self->anim_hi = (u8)(hi + 1);          /* 0 means "the whole sprite" */
    self->flags &= (u8)~(NB_F_ANIM_ONCE | NB_F_ANIM_DONE);
    if (self->image_index < lo || self->image_index > hi)
        self->image_index = (s16)lo;
}

void rt_anim_once(Instance* self, int lo, int hi) {
    rt_anim_range(self, lo, hi);
    if (self) self->flags |= NB_F_ANIM_ONCE;
}

int rt_anim_done(Instance* self) { return self && (self->flags & NB_F_ANIM_DONE); }

/* ---------------- rooms ---------------- */
void rt_room_goto(s16 room) { g_next_room = room; }

void rt_room_goto_at(s16 room, s32 x, s32 y) {
    g_next_room = room;
    g_arrive_x = x; g_arrive_y = y;
}

/* Has the followed instance stepped into a warp?

   Overlap, not containment: a warp one tile wide would otherwise be missed
   entirely by anything moving faster than its width, which reads as a door
   that works only sometimes. */
static void rt_warp_check(void) {
    const nb_Room* r = g_room;
    s32 l, t, rr, b;
    if (!r || !r->warps || !g_view || !g_view->active) return;
    if (g_next_room >= 0) return;          /* a change is already pending */
    rt_bbox_at(g_view, g_view->x, g_view->y, &l, &t, &rr, &b);
    for (u16 i = 0; i < r->nwarps; i++) {
        const nb_Warp* w = &r->warps[i];
        if (rr < (s32)w->x || l >= (s32)(w->x + w->w)) continue;
        if (b < (s32)w->y || t >= (s32)(w->y + w->h)) continue;
        if (w->room < 0 || w->room >= nb_room_count) continue;
        rt_room_goto_at(w->room, w->tx, w->ty);
        return;
    }
}

void rt_room_goto_fade(s16 room) {
    if (g_trans) return;
    g_trans = 1; g_trans_room = room;
}

void rt_room_restart(void) { g_next_room = g_cur_room; }

s16 rt_room(void) { return g_cur_room; }

static void rt_room_load(s16 room) {
    if (room < 0 || room >= nb_room_count) return;
    rt_clear_instances();
    const nb_Room* r = &nb_rooms[room];
    /* the tile layer is thousands of VRAM writes: blank the display while it is
       rebuilt so the player never sees the previous room's map shear */
    REG_DISPCNT = (u16)(g_dispcnt | FORCE_BLANK);
    g_room = r;
    g_room_cw = r->w / 8; g_room_ch = r->h / 8;
    g_room_w = r->w; g_room_h = r->h;
    g_tiles = r->tiles;
    g_edge_solid = (u8)(r->edge_open ? 0 : 1);
    g_has_solid = 0;
    for (int i = 0; i < 512; i++) g_solid_of[i] = 0;
    if (r->tile_solid && r->tiles) {
        int n = nb_bg_tile_count > 512 ? 512 : nb_bg_tile_count;
        for (int i = 0; i < n; i++)
            if (r->tile_solid[i]) { g_solid_of[i] = 1; g_has_solid = 1; }
    }
    BG_PALETTE[0] = r->bg;        /* mode-0 backdrop shows through behind tiles */
    rt_clear_text();
    g_cam_x = 0; g_cam_y = 0; g_scroll_x = 0; g_scroll_y = 0;
    g_win_x = 0; g_win_y = 0;
    map_full();
    far_load(r);
    g_cur_room = room;
    /* room speed (steps/sec) -> VBlanks per step; 0 or >60 means one step/frame */
    {
        u16 sp = r->speed ? r->speed : 60;
        g_step_frames = (u16)(60 / sp);
        if (g_step_frames < 1) g_step_frames = 1;
    }
    for (int i = 0; i < r->ninst; i++)
        rt_create(r->insts[i].object, r->insts[i].x, r->insts[i].y);
    /* the camera follows the first placed instance (the player, by convention);
       rt_view_follow can retarget it later */
    g_view = 0;
    for (int i = 0; i < NB_MAX_INSTANCES; i++)
        if (g_inst[i].active) { g_view = &g_inst[i]; break; }
    /* A warp names where its traveller lands. Applied after the room has made
       its instances, so the arrival overrides the placement the room would
       otherwise have used -- and cleared immediately, or every later entry to
       the room would inherit one warp's destination. */
    if (g_view && g_arrive_x >= 0) {
        g_view->x = (s16)g_arrive_x;
        g_view->y = (s16)g_arrive_y;
        g_view->xsub = 0; g_view->ysub = 0;
    }
    /* The ground: affine if the room brought one, flat otherwise. Done
       on every load so walking out of an affine room restores the flat
       arrangement without the author asking. */
    if (r->aff_map) affine_layer_on(r);
    else affine_layer_off();
    g_arrive_x = -1; g_arrive_y = -1;
    rt_camera_update();
    map_update();
    REG_BG0HOFS = (u16)g_scroll_x; REG_BG0VOFS = (u16)g_scroll_y;
    REG_DISPCNT = g_dispcnt;
}

/* ---------------- the game step ---------------- */
/* IWRAM: 128 instances a frame, and ROM is a 16-bit bus with waitstates
   where IWRAM is 32-bit with none. Measured at full sprite saturation the
   step/move/draw trio cost 104%% of a frame -- the game ran at 0.68 steps
   per VBlank, which is 41 fps, not 60. */
IWRAM_CODE static void rt_step_all(void) {
    /* count alarms down first, so a step fn's alarm==0 check fires this step */
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* in = &g_inst[i];
        if (!in->active) continue;
        for (int a = 0; a < NB_MAX_ALARMS; a++)
            if (in->alarm[a] > 0) in->alarm[a]--;
    }
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* in = &g_inst[i];
        if (!in->active) continue;
        if (in->inv) in->inv--;
        const nb_Object* ob = &nb_objects[in->object];
        /* Mercy frames arm themselves: whatever inside this step costs
           health -- a collision event, a script, raw C -- is what grants the
           invincibility, because the engine watches the ledger rather than
           the weapon. */
        s32 h0 = nb_health;
        if (ob->step) ob->step(in);
        if (nb_health < h0 && ob->hurt_frames && !in->inv)
            in->inv = ob->hurt_frames;
    }
    /* The floor, and the one death. Health never leaves a step negative, and
       reaching zero fires each opted-in object's event exactly once -- it
       cannot refire until health has risen above zero, so death logic is
       authored without a latch variable in every project. */
    if (nb_health < 0) nb_health = 0;
    if (nb_health == 0 && !g_death_fired) {
        g_death_fired = 1;
        for (int o = 0; o < nb_object_count; o++) {
            if (!nb_objects[o].on_no_health) continue;
            for (int i = 0; i < NB_MAX_INSTANCES; i++) {
                Instance* in = &g_inst[i];
                if (in->active && in->object == o) {
                    nb_objects[o].on_no_health(in);
                    break;
                }
            }
        }
    } else if (nb_health > 0) {
        g_death_fired = 0;
    }
}

IWRAM_CODE static void anim_step(Instance* in) {
    if (!in->image_speed || in->sprite < 0 || in->sprite >= nb_sprite_count)
        return;
    s16 nf = (s16)nb_sprites[in->sprite].nframes;
    if (nf <= 0) return;
    s16 lo = in->anim_lo, hi = in->anim_hi ? (s16)(in->anim_hi - 1) : (s16)(nf - 1);
    if (hi >= nf) hi = (s16)(nf - 1);
    if (lo > hi) lo = hi;
    if (in->flags & NB_F_ANIM_DONE) return;
    in->image_accum = (s16)(in->image_accum + in->image_speed);
    while (in->image_accum >= 16) {
        in->image_accum -= 16;
        in->image_index++;
        if (in->image_index > hi) {
            if (in->flags & NB_F_ANIM_ONCE) {
                in->image_index = hi;
                in->flags |= NB_F_ANIM_DONE;
                return;
            }
            in->image_index = lo;
        }
    }
}

/* Move one instance. Objects with tilecol set advance a pixel at a time per
   axis so they end up flush against a wall and land exactly on a floor — the
   thing that turns a decorative tile layer into a platform game. */
static void inst_move(Instance* in) {
    const nb_Object* ob = &nb_objects[in->object];
    if (in->glide) { glide_step(in); return; }
    in->vspeed += in->grav;
    if (in->grav8) {
        s32 t = in->vspd8 + in->grav8;
        if (t > 32767) t = 32767; else if (t < -32768) t = -32768;
        in->vspd8 = (s16)t;
    }
    s32 ax = (s32)in->xsub + in->hspd8;
    s32 dx = in->hspeed + (ax >> 8);
    in->xsub = (s16)(ax & 255);
    s32 ay = (s32)in->ysub + in->vspd8;
    s32 dy = in->vspeed + (ay >> 8);
    in->ysub = (s16)(ay & 255);
    in->flags &= (u8)~(NB_F_BLOCK_H | NB_F_BLOCK_V | NB_F_GROUND);
    if (!ob->tilecol) { in->x += dx; in->y += dy; return; }
    int all = ob->tilecol >= 2 && g_solid_n > 0;
    s32 l, t, r, b;
    rt_bbox_at(in, in->x, in->y, &l, &t, &r, &b);
    /* The usual case is that nothing is in the way, so test the whole strip the
       move sweeps through in one go and only walk pixel by pixel when that
       strip is not clear — which is exactly when the instance is at a wall. */
    if (dx) {
        if (dx < -64) dx = -64; else if (dx > 64) dx = 64;
        s32 ul = dx > 0 ? l : l + dx, ur = dx > 0 ? r + dx : r;
        if (box_free(ul, t, ur, b)
            && (!all || box_free_inst(in, ul, t, ur, b))) {
            in->x += dx; l += dx; r += dx;
        } else {
            s32 s = dx > 0 ? 1 : -1, n = dx > 0 ? dx : -dx;
            while (n--) {
                s32 edge = (s > 0 ? r : l) + s;
                if (col_free(edge, t, b)
                    && (!all || box_free_inst(in, l + s, t, r + s, b))) {
                    in->x += s; l += s; r += s;
                } else {
                    in->flags |= NB_F_BLOCK_H;
                    in->hspeed = 0; in->hspd8 = 0; in->xsub = 0;
                    break;
                }
            }
        }
    }
    if (dy) {
        if (dy < -64) dy = -64; else if (dy > 64) dy = 64;
        s32 ut = dy > 0 ? t : t + dy, ub = dy > 0 ? b + dy : b;
        if (box_free(l, ut, r, ub)
            && (!all || box_free_inst(in, l, ut, r, ub))) {
            in->y += dy; t += dy; b += dy;
        } else {
            s32 s = dy > 0 ? 1 : -1, n = dy > 0 ? dy : -dy;
            while (n--) {
                s32 edge = (s > 0 ? b : t) + s;
                if (row_free(edge, l, r)
                    && (!all || box_free_inst(in, l, t + s, r, b + s))) {
                    in->y += s; t += s; b += s;
                } else {
                    in->flags |= NB_F_BLOCK_V;
                    in->vspeed = 0; in->vspd8 = 0; in->ysub = 0;
                    break;
                }
            }
        }
    }
    if (!row_free(b + 1, l, r)
        || (all && !box_free_inst(in, l, t + 1, r, b + 1)))
        in->flags |= NB_F_GROUND;
}

IWRAM_CODE static void rt_move_all(void) {
    /* the instances other things cannot pass through, gathered once so the
       per-pixel collision walk above stays cheap */
    g_solid_n = 0;
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* in = &g_inst[i];
        if (in->active && in->object >= 0 && in->object < nb_object_count
            && nb_objects[in->object].solid) {
            s32 l, t, r, b;
            rt_bbox(in, &l, &t, &r, &b);
            g_solid_box[g_solid_n][0] = (s16)l;
            g_solid_box[g_solid_n][1] = (s16)t;
            g_solid_box[g_solid_n][2] = (s16)r;
            g_solid_box[g_solid_n][3] = (s16)b;
            g_solid_list[g_solid_n++] = in;
        }
    }
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* in = &g_inst[i];
        if (!in->active) continue;
        inst_move(in);
        anim_step(in);
    }
}

/* ---------------- rendering ----------------
   One hardware object per visible instance, built into a shadow copy of OAM in
   IWRAM. Instances are emitted front layer first, because a lower OAM slot wins
   when two sprites overlap, which is how `depth` orders them. */
static int aff_slot;

static void aff_write(int slot, s32 pa, s32 pb, s32 pc, s32 pd) {
    g_oam[slot * 4 + 0].fill = (u16)(s16)pa;
    g_oam[slot * 4 + 1].fill = (u16)(s16)pb;
    g_oam[slot * 4 + 2].fill = (u16)(s16)pc;
    g_oam[slot * 4 + 3].fill = (u16)(s16)pd;
}

IWRAM_CODE static void emit_sprite(Instance* in, int oi) {
    const nb_Sprite* s = &nb_sprites[in->sprite];
    int frame = in->image_index;
    if (frame < 0 || (u16)frame >= s->nframes) frame = 0;
    s32 sx = in->x - s->ox - g_scroll_x;
    s32 sy = in->y - s->oy - g_scroll_y;
    u16 tile = (u16)(s->tile + (u16)frame * s->tiles_per_frame);
    u16 a0 = (u16)(s->shape << 14);
    u16 a1 = OBJ_SIZE(s->size);
    /* The instance's bank wins over the sprite's when it has one. */
    u16 palbank = in->palbank ? (u16)(in->palbank - 1) : s->palbank;
    /* OBJ mode 2: this sprite's opaque pixels define the OBJ window
       instead of being drawn. The blink and every other attribute still
       apply -- a blinking stencil flickers the window, which is exactly
       what a mercy flash should look like through one. */
    if (in->objwin) a0 |= 0x0800;
    /* The mercy blink: skipping the draw every other pair of frames is the
       classic signal, costs nothing, and corrupts no author state -- hidden
       stays the author's. */
    if (in->inv & 2) return;
    int transformed = (in->angle || (in->scale && in->scale != 256));
    if (transformed && aff_slot < 32) {
        s32 sc = in->scale ? in->scale : 256;
        s32 co = rt_cos8(in->angle), si = rt_sin8(in->angle);
        s32 pa = co * 256 / sc, pb = si * 256 / sc;
        s32 pc = -si * 256 / sc, pd = co * 256 / sc;
        if (in->flip & 1) { pa = -pa; pc = -pc; }
        if (in->flip & 2) { pb = -pb; pd = -pd; }
        aff_write(aff_slot, pa, pb, pc, pd);
        /* double-size keeps a rotated or enlarged sprite from being clipped;
           the object's box grows to 2x, so its top-left moves out by half */
        a0 |= OBJ_AFFINE | OBJ_DBLSIZE;
        a1 |= OBJ_AFFINE_IX(aff_slot);
        aff_slot++;
        sx -= s->w / 2; sy -= s->h / 2;
        if (sx <= -(s32)s->w * 2 || sx >= SCREEN_W
            || sy <= -(s32)s->h * 2 || sy >= SCREEN_H) {
            g_oam[oi].attr0 = OBJ_HIDE;
            return;
        }
    } else {
        if (in->flip & 1) a1 |= OBJ_HFLIP;
        if (in->flip & 2) a1 |= OBJ_VFLIP;
    }
    g_oam[oi].attr0 = (u16)((sy & 0x00FF) | a0);
    g_oam[oi].attr1 = (u16)((sx & 0x01FF) | a1);
    g_oam[oi].attr2 = (u16)(OBJ_TILE(tile) | OBJ_PALBANK(palbank) | OBJ_PRIO(1));
}

IWRAM_CODE static void rt_render(void) {
    int oi = 0;
    u32 layers = 0;
    aff_slot = 0;
    for (int i = 0; i < NB_MAX_INSTANCES; i++)
        if (g_inst[i].active) layers |= 1u << (g_inst[i].depth & (NB_MAX_DEPTH - 1));
    for (int d = 0; d < NB_MAX_DEPTH && oi < 128; d++) {
        if (!(layers & (1u << d))) continue;
        for (int i = 0; i < NB_MAX_INSTANCES && oi < 128; i++) {
            Instance* in = &g_inst[i];
            if (!in->active || (in->depth & (NB_MAX_DEPTH - 1)) != d) continue;
            if (in->hidden) continue;
            if (in->object < 0 || in->object >= nb_object_count) continue;
            if (!nb_objects[in->object].visible) continue;
            if (in->sprite < 0 || in->sprite >= nb_sprite_count) continue;
            const nb_Sprite* s = &nb_sprites[in->sprite];
            s32 sx = in->x - s->ox - g_scroll_x;
            s32 sy = in->y - s->oy - g_scroll_y;
            if (!in->angle && (!in->scale || in->scale == 256)
                && (sx <= -(s32)s->w || sx >= SCREEN_W
                    || sy <= -(s32)s->h || sy >= SCREEN_H))
                continue;                            /* fully off-screen */
            emit_sprite(in, oi);
            oi++;
        }
    }
    for (; oi < 128; oi++) g_oam[oi].attr0 = OBJ_HIDE;
}

/* Everything that touches OAM or a scroll register, done in the first moments
   of VBlank from the shadow copy: 256 words by DMA, no tearing. */
static void rt_flush(void) {
    dma_copy32(OAM, g_oam, 128 * 8 / 4);
    REG_BG0HOFS = (u16)g_scroll_x;
    REG_BG0VOFS = (u16)g_scroll_y;
    if (g_room && g_room->far_tiles) {
        u16 div = g_room->far_div ? g_room->far_div : 2;
        REG_BG3HOFS = (u16)(g_scroll_x / div);
        REG_BG3VOFS = (u16)(g_scroll_y / div);
    }
    fx_apply();
    pal_cycle_step();             /* rotations land inside the VBlank */
    REG_DISPCNT = g_dispcnt;
}

void rt_run(void) {
    REG_WAITCNT = WAITCNT_FAST;   /* ROM prefetch + the SRAM timing a save needs */
    save_init();                  /* pin the 128K flash part to bank 0 */
    rt_irq_init();                /* before anything waits on a VBlank */
    rt_video_init();
    rt_sound_init();
    rt_clear_instances();
    rt_room_load(nb_start_room);
    rt_render();
    u16 stepc = 0;
    for (;;) {
        rt_vsync();
        rt_flush();               /* install last frame's sprites and scroll */
        map_update();             /* stream in any tile column just exposed */
        rt_sound_update();
        /* a room transition freezes the game while the screen fades out and in */
        if (g_trans == 1) {
            g_dark = (u8)(g_dark + 2);
            if (g_dark >= 16) {
                g_dark = 16;
                rt_room_load(g_trans_room);
                g_trans = 2;
                stepc = 0;
            }
        } else if (g_trans == 2) {
            if (g_dark >= 2) g_dark = (u8)(g_dark - 2);
            else { g_dark = 0; g_trans = 0; }
        } else if (++stepc >= g_step_frames) {
            /* Input is sampled on step frames so rt_key_pressed() edges line up
               with the steps that read them. */
            stepc = 0;
            rt_input_update();
            rt_prof_begin(PROF_STEP);
            rt_step_all();
            rt_prof_end(PROF_STEP);
            rt_prof_begin(PROF_MOVE);
            rt_move_all();
            rt_prof_end(PROF_MOVE);
            /* After movement: a warp is entered by arriving on it, and testing
               before the move would fire a frame early, from outside it. */
            rt_warp_check();
        }
        rt_camera_update();
        rt_prof_begin(PROF_DRAW);
        rt_say_step();
        rt_pcm_tick();
        rt_render();
        rt_prof_end(PROF_DRAW);
        prof_frame();
        if (g_next_room >= 0 && g_next_room != g_cur_room) {
            s16 nr = g_next_room; g_next_room = -1;
            rt_room_load(nr);
            rt_render();
            stepc = 0;
        } else if (g_next_room >= 0) {
            /* the same room again: a restart */
            g_next_room = -1;
            rt_room_load(g_cur_room);
            rt_render();
            stepc = 0;
        }
    }
}

/* Entry: crt0 branches here after RAM setup. */
int main(void) {
    rt_run();
    return 0;
}
