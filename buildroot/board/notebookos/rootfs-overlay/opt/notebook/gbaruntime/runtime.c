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
static u16 g_step_frames = 1;    /* VBlanks between game steps (room speed) */
static const nb_Room* g_room = 0;
static Instance* g_view = 0;     /* the camera follows this instance (0 = fixed) */
static Instance* g_other = 0;    /* what the most recent collision test found */

/* global game state (Game Maker score/lives/health) + persistent globals */
s32 nb_score = 0, nb_lives = 3, nb_health = 100;
s32 nb_global[NB_MAX_GLOBALS];
/* the save-type marker vbam / flashcarts scan for to enable 32 KiB SRAM */
static const char nb_sram_sig[] __attribute__((used, aligned(4))) = "SRAM_V113";

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

/* ---- persistent save (SRAM: 32 KiB at 0x0E000000, 8-bit access only) ---- */
#define SRAM        ((volatile u8*)0x0E000000)
#define SAVE_MAGIC  0x42474D31   /* '1MGB' */
#define SAVE_HISCORE (16 + NB_MAX_GLOBALS * 4)   /* high score lives after the globals */

static void sram_w32(int off, s32 v) {
    SRAM[off] = (u8)v;
    SRAM[off + 1] = (u8)(v >> 8);
    SRAM[off + 2] = (u8)(v >> 16);
    SRAM[off + 3] = (u8)(v >> 24);
}

static s32 sram_r32(int off) {
    return (s32)((u32)SRAM[off] | ((u32)SRAM[off + 1] << 8)
                 | ((u32)SRAM[off + 2] << 16) | ((u32)SRAM[off + 3] << 24));
}

void rt_game_save(void) {
    sram_w32(0, (s32)SAVE_MAGIC);
    sram_w32(4, nb_score);
    sram_w32(8, nb_lives);
    sram_w32(12, nb_health);
    for (int i = 0; i < NB_MAX_GLOBALS; i++)
        sram_w32(16 + i * 4, nb_global[i]);
}

int rt_game_load(void) {
    if (sram_r32(0) != (s32)SAVE_MAGIC)
        return 0;
    nb_score = sram_r32(4);
    nb_lives = sram_r32(8);
    nb_health = sram_r32(12);
    for (int i = 0; i < NB_MAX_GLOBALS; i++)
        nb_global[i] = sram_r32(16 + i * 4);
    return 1;
}

/* The best score ever submitted. Kept separately from the save slot so a game
   with no save/load at all can still have a high score that survives being
   switched off — the thing that makes a home-made game worth replaying. */
s32 rt_highscore(void) {
    if (sram_r32(0) != (s32)SAVE_MAGIC)
        return 0;
    return sram_r32(SAVE_HISCORE);
}

int rt_highscore_submit(void) {
    s32 best = rt_highscore();
    if (sram_r32(0) != (s32)SAVE_MAGIC)
        sram_w32(0, (s32)SAVE_MAGIC);
    if (nb_score > best) {
        sram_w32(SAVE_HISCORE, nb_score);
        return 1;
    }
    return 0;
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

void rt_draw_text_centre(int row, const char* s, int colour) {
    int n = 0;
    if (!s) return;
    while (s[n]) n++;
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

static void rt_vsync(void) {
    while (REG_VCOUNT >= 160) {}     /* finish the current visible frame */
    while (REG_VCOUNT < 160) {}      /* wait for VBlank */
}

/* ---------------- input ---------------- */
static void rt_input_update(void) {
    g_keys_prev = g_keys;
    g_keys = (u16)((~REG_KEYINPUT) & KEY_ANY);   /* active-high */
}

int rt_key_held(u16 key)     { return (g_keys & key) != 0; }
int rt_key_pressed(u16 key)  { return (g_keys & key) && !(g_keys_prev & key); }
int rt_key_released(u16 key) { return !(g_keys & key) && (g_keys_prev & key); }

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
    g_sfxt = -1; g_fx = -1;
    rt_wave(0, 0);
}

void rt_stop_music(void) {
    g_mus = -1;
    rt_square(0, 0, 0, 0, 0);
    rt_square(1, 0, 0, 0, 0);
    REG_SOUND4CNT_L = 0;
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

void rt_play_sfx(s16 sound) {
    if (sound < 0) { rt_stop_sfx(); return; }
    if (sound >= nb_sound_count) return;
    g_fx = -1;                       /* the wave channel does one thing at a time */
    g_sfxt = sound; g_sfxt_step = 0; g_sfxt_frame = 0;
}

void rt_sfx(int preset) {
    if (preset < 0 || preset >= NB_SFX_COUNT) return;
    const nb_Fx* f = &nb_fx[preset];
    g_fx = (s16)preset; g_fx_stage = 0; g_fx_frame = 0;
    g_fx_note4 = (s16)(f->n1 * 4);
    if (!f->ch) g_sfxt = -1;         /* the wave channel does one thing at a time */
}

/* The one call a game made before music and effects were separate channels:
   route it by the sound's own kind so old projects keep working and gain the
   layering for free. */
void rt_play_sound(s16 sound) {
    if (sound < 0) { rt_stop_music(); rt_stop_sfx(); return; }
    if (sound >= nb_sound_count) return;
    if (nb_sounds[sound].kind) rt_play_sfx(sound);
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
    *l = x - ox + bl;
    *t = y - oy + bt;
    *r = x - ox + w - 1 - br;
    *b = y - oy + h - 1 - bb;
    if (*r < *l) *r = *l;
    if (*b < *t) *b = *t;
}

static void rt_bbox(Instance* in, s32* l, s32* t, s32* r, s32* b) {
    rt_bbox_at(in, in->x, in->y, l, t, r, b);
}

Instance* rt_meeting(Instance* self, s16 object) {
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
static int cell_solid(s32 tx, s32 ty) {
    if ((u32)tx >= (u32)g_room_cw || (u32)ty >= (u32)g_room_ch)
        return g_edge_solid;
    if (!g_has_solid) return 0;
    return g_solid_of[g_tiles[ty * g_room_cw + tx] & 0x01FF];
}

int rt_tile_solid(s32 px, s32 py) { return cell_solid(px >> 3, py >> 3); }

/* Every cell a box covers. The cell range is clamped once, so the inner loop is
   a row pointer walk with no bounds test per cell. */
static int box_free(s32 l, s32 t, s32 r, s32 b) {
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
    rt_camera_update();
    map_update();
    REG_BG0HOFS = (u16)g_scroll_x; REG_BG0VOFS = (u16)g_scroll_y;
    REG_DISPCNT = g_dispcnt;
}

/* ---------------- the game step ---------------- */
static void rt_step_all(void) {
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
        nb_event_fn step = nb_objects[in->object].step;
        if (step) step(in);
    }
}

static void anim_step(Instance* in) {
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

static void rt_move_all(void) {
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

static void emit_sprite(Instance* in, int oi) {
    const nb_Sprite* s = &nb_sprites[in->sprite];
    int frame = in->image_index;
    if (frame < 0 || (u16)frame >= s->nframes) frame = 0;
    s32 sx = in->x - s->ox - g_scroll_x;
    s32 sy = in->y - s->oy - g_scroll_y;
    u16 tile = (u16)(s->tile + (u16)frame * s->tiles_per_frame);
    u16 a0 = (u16)(s->shape << 14);
    u16 a1 = OBJ_SIZE(s->size);
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
    g_oam[oi].attr2 = (u16)(OBJ_TILE(tile) | OBJ_PALBANK(s->palbank) | OBJ_PRIO(1));
}

static void rt_render(void) {
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
    REG_DISPCNT = g_dispcnt;
}

void rt_run(void) {
    REG_WAITCNT = WAITCNT_FAST;   /* ROM prefetch + the SRAM timing a save needs */
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
            rt_step_all();
            rt_move_all();
        }
        rt_camera_update();
        rt_render();
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
