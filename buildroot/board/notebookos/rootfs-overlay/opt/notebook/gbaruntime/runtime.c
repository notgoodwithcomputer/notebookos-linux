/* runtime.c — the Notebook OS GBA IDE game engine.
   Mode-0 hardware renderer (tiled BG + OBJ sprites + a BG1 text layer) and a
   Game-Maker-style instance/event loop. */
#include "runtime.h"
#include "font.h"

static Instance g_inst[NB_MAX_INSTANCES];
static u16 g_keys, g_keys_prev;
static u16 g_room_bg;
static s16 g_cur_room = -1;
static s16 g_next_room = -1;
static u16 g_step_frames = 1;    /* VBlanks between game steps (room speed) */
static const nb_Room* g_room = 0;
static Instance* g_view = 0;     /* the camera follows this instance (0 = fixed) */

/* global game state (Game Maker score/lives/health) + persistent globals */
s32 nb_score = 0, nb_lives = 3, nb_health = 100;
s32 nb_global[NB_MAX_GLOBALS];
/* the save-type marker vbam / flashcarts scan for to enable 32 KiB SRAM */
static const char nb_sram_sig[] __attribute__((used, aligned(4))) = "SRAM_V113";

/* ---- persistent save (SRAM: 32 KiB at 0x0E000000, 8-bit access only) ---- */
#define SRAM        ((volatile u8*)0x0E000000)
#define SAVE_MAGIC  0x42474D31   /* '1MGB' */

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
static u32 g_rng = 0x1234567u;

/* ---------------- video (mode 0: tiled BG + hardware OBJ sprites) ---------- */
/* BG0 is the tile layer: tileset in charblock 0, a 64x64 (512x512 px) map in
   screenblocks 28-31, hardware-scrolled by the camera. OBJ tiles at charblock 4. */
#define BG_MAP_SB    28    /* BG0 tilemap: 64x64 across screenblocks 28-31 */
#define BG_TEXT_SB   26    /* BG1 fixed text layer: one 32x32 screenblock */
#define TEXT_PALBANK 15    /* dedicated BG palette bank for text ink */
static s32 g_cam_x = 0, g_cam_y = 0;   /* camera top-left, in room pixels */

/* ---- BG1 text layer (fixed to the screen; over the map and sprites) ---- */
void rt_clear_text(void) {
    volatile u16* sb = SCREENBLOCK(BG_TEXT_SB);
    for (int i = 0; i < 32 * 32; i++) sb[i] = 0;
}

void rt_draw_text(int col, int row, const char* s) {
    if (row < 0 || row >= 20 || !s) return;
    volatile u16* sb = SCREENBLOCK(BG_TEXT_SB) + row * 32;
    for (int c = col; *s; s++, c++) {
        if (c < 0 || c >= 30)
            continue;
        unsigned char ch = (unsigned char)*s;
        if (ch < NB_FONT_FIRST || ch >= NB_FONT_FIRST + NB_FONT_COUNT)
            ch = ' ';
        sb[c] = (u16)((ch - NB_FONT_FIRST) | (TEXT_PALBANK << 12));
    }
}

void rt_draw_int(int col, int row, s32 value) {
    char buf[12];
    int i = 0, neg = value < 0;
    u32 v = neg ? (u32)(-value) : (u32)value;
    char tmp[12];
    int n = 0;
    if (v == 0) tmp[n++] = '0';
    while (v > 0) { tmp[n++] = (char)('0' + (v % 10)); v /= 10; }
    if (neg) buf[i++] = '-';
    while (n > 0) buf[i++] = tmp[--n];
    buf[i] = 0;
    rt_draw_text(col, row, buf);
}

static void rt_video_init(void) {
    /* OBJ palette (16 banks of 16) + every sprite's tiles, uploaded once */
    for (int i = 0; i < 256; i++) OBJ_PALETTE[i] = nb_obj_palette[i];
    int n = nb_obj_tile_count * 16;    /* 16 u16 per 4bpp tile */
    if (n > 16384) n = 16384;          /* OBJ VRAM is 32 KiB */
    for (int i = 0; i < n; i++) OBJ_TILES[i] = nb_obj_tiles[i];
    for (int i = 0; i < 128; i++) OAM[i].attr0 = OBJ_HIDE;

    /* shared BG palette (entry 0 is the per-room backdrop) + tileset tiles */
    for (int i = 1; i < 16; i++) BG_PALETTE[i] = nb_bg_palette[i];
    int m = nb_bg_tile_count * 16;
    if (m > 8192) m = 8192;            /* charblock 0 holds 512 tiles */
    volatile u16* cb = CHARBLOCK(0);
    for (int i = 0; i < m; i++) cb[i] = nb_bg_tiles[i];
    REG_BG0CNT = BGCNT_CB(0) | BGCNT_SB(BG_MAP_SB) | BGCNT_4BPP | BGCNT_SIZE(3) | 2;

    /* BG1 fixed text layer: font -> charblock 1; white ink in palette bank 15;
       priority 0 so dialogue/HUD sit above the map (prio 2) and sprites (prio 1) */
    {
        int fn = (int)(sizeof(nb_font) / sizeof(nb_font[0]));
        volatile u16* fcb = CHARBLOCK(1);
        for (int i = 0; i < fn; i++) fcb[i] = nb_font[i];
    }
    BG_PALETTE[TEXT_PALBANK * 16 + 1] = RGB15(31, 31, 31);
    REG_BG1CNT = BGCNT_CB(1) | BGCNT_SB(BG_TEXT_SB) | BGCNT_4BPP;   /* priority 0 */
    REG_BG1HOFS = 0; REG_BG1VOFS = 0;
    rt_clear_text();

    REG_DISPCNT = MODE0 | BG0_ON | BG1_ON | OBJ_ON | OBJ_1D_MAP;
}

/* Fill the 64x64 BG map (4 screenblocks) from the room's tile layer; cells
   outside the room, or when the room has no layer, are the blank tile 0. */
static void rt_load_tilemap(const nb_Room* r) {
    int cw = r->w / 8, ch = r->h / 8;
    const u16* tm = r->tiles;
    for (int ty = 0; ty < 64; ty++) {
        for (int tx = 0; tx < 64; tx++) {
            volatile u16* sb = SCREENBLOCK(BG_MAP_SB + ((ty >> 5) << 1) + (tx >> 5));
            u16 v = (tm && tx < cw && ty < ch) ? tm[ty * cw + tx] : 0;
            sb[(ty & 31) * 32 + (tx & 31)] = v;
        }
    }
}

/* Centre the camera on the followed instance, clamped inside the room. */
static void rt_camera_update(void) {
    if (!g_room || !g_view || !g_view->active) { g_cam_x = 0; g_cam_y = 0; return; }
    s32 cx = g_view->x - SCREEN_W / 2;
    s32 cy = g_view->y - SCREEN_H / 2;
    s32 maxx = (s32)g_room->w - SCREEN_W; if (maxx < 0) maxx = 0;
    s32 maxy = (s32)g_room->h - SCREEN_H; if (maxy < 0) maxy = 0;
    if (cx < 0) cx = 0; else if (cx > maxx) cx = maxx;
    if (cy < 0) cy = 0; else if (cy > maxy) cy = maxy;
    g_cam_x = cx; g_cam_y = cy;
}

static void rt_vsync(void) {
    while (REG_VCOUNT >= 160) {}     /* finish current visible frame */
    while (REG_VCOUNT < 160) {}      /* wait for VBlank */
}

/* ---------------- input ---------------- */
static void rt_input_update(void) {
    g_keys_prev = g_keys;
    g_keys = (~REG_KEYINPUT) & 0x03FF;   /* active-high */
}

int rt_key_held(u16 key)    { return (g_keys & key) != 0; }
int rt_key_pressed(u16 key) { return (g_keys & key) && !(g_keys_prev & key); }
int rt_key_released(u16 key) { return !(g_keys & key) && (g_keys_prev & key); }

/* ---------------- rng ---------------- */
s32 rt_random(s32 n) {
    g_rng = g_rng * 1103515245u + 12345u;
    if (n <= 0) return 0;
    return (s32)((g_rng >> 16) % (u32)n);
}

/* ---------------- sound (PSG chiptune) ---------------- */
/* GBA square-channel frequency register per MIDI note (0 => too low to play). */
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
static s16 g_snd = -1;
static u16 g_snd_step, g_snd_frame;

static void rt_sound_init(void) {
    REG_SOUNDCNT_X = 0x0080;      /* master sound enable */
    REG_SOUNDCNT_L = 0xFF77;      /* square 1+2 to both speakers, full volume */
    REG_SOUNDCNT_H = 0x0002;      /* PSG at 100% output */
}

/* Retrigger a square channel: ch 0 = sound1, ch 1 = sound2. midi<=0 => silence. */
static void rt_square(int ch, int midi) {
    u16 env, f;
    if (midi > 0 && midi < 128 && nb_note_freq[midi]) {
        env = 0xF080;             /* volume 15, 50% duty, no length limit */
        f = nb_note_freq[midi];
    } else {
        env = 0x0080; f = 0;      /* volume 0 = silent */
    }
    if (ch == 0) {
        REG_SOUND1CNT_L = 0;      /* no sweep */
        REG_SOUND1CNT_H = env;
        REG_SOUND1CNT_X = 0x8000 | f;
    } else {
        REG_SOUND2CNT_L = env;
        REG_SOUND2CNT_H = 0x8000 | f;
    }
}

void rt_play_sound(s16 sound) {
    if (sound < 0) {
        g_snd = -1;
        rt_square(0, 0); rt_square(1, 0);
        return;
    }
    if (sound >= nb_sound_count) return;
    g_snd = sound; g_snd_step = 0; g_snd_frame = 0;
}

static void rt_sound_update(void) {
    if (g_snd < 0 || g_snd >= nb_sound_count) return;
    const nb_Sound* s = &nb_sounds[g_snd];
    if (g_snd_frame == 0) {
        if (g_snd_step >= s->nsteps) {
            if (s->loop) g_snd_step = 0;
            else { rt_play_sound(-1); return; }
        }
        u8 lead = s->lead ? s->lead[g_snd_step] : 0;
        u8 bass = s->bass ? s->bass[g_snd_step] : 0;
        if (lead != 255) rt_square(0, lead);   /* 255 = hold previous note */
        if (bass != 255) rt_square(1, bass);
        g_snd_step++;
    }
    if (++g_snd_frame >= (s->tempo ? s->tempo : 1)) g_snd_frame = 0;
}

/* ---------------- instances ---------------- */
static void rt_clear_instances(void) {
    for (int i = 0; i < NB_MAX_INSTANCES; i++) g_inst[i].active = 0;
}

Instance* rt_create(s16 object, s32 x, s32 y) {
    if (object < 0 || object >= nb_object_count) return 0;
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* in = &g_inst[i];
        if (in->active) continue;
        in->active = 1;
        in->object = object;
        in->sprite = nb_objects[object].sprite;
        in->image_index = 0;
        in->x = x; in->y = y;
        in->hspeed = 0; in->vspeed = 0; in->grav = 0;
        in->image_speed = (in->sprite >= 0 && in->sprite < nb_sprite_count)
                          ? (s16)nb_sprites[in->sprite].anim_speed : 0;
        in->image_accum = 0;
        for (int a = 0; a < NB_MAX_ALARMS; a++) in->alarm[a] = -1;
        for (int v = 0; v < NB_MAX_VARS; v++) in->var[v] = 0;
        if (nb_objects[object].create) nb_objects[object].create(in);
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
}

void rt_destroy_object(s16 object) {
    for (int i = 0; i < NB_MAX_INSTANCES; i++)
        if (g_inst[i].active && (object < 0 || g_inst[i].object == object))
            rt_destroy(&g_inst[i]);
}

/* Point the instance's speed toward (tx,ty). Manhattan-normalised (no FPU on
   ARM7): good enough for chase/seek without a square root. */
void rt_move_toward(Instance* self, s32 tx, s32 ty, s32 speed) {
    if (!self) return;
    s32 dx = tx - self->x, dy = ty - self->y;
    s32 len = (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
    if (len == 0) { self->hspeed = 0; self->vspeed = 0; return; }
    self->hspeed = dx * speed / len;
    self->vspeed = dy * speed / len;
}

int rt_instance_count(s16 object) {
    int n = 0;
    for (int i = 0; i < NB_MAX_INSTANCES; i++)
        if (g_inst[i].active && (object < 0 || g_inst[i].object == object)) n++;
    return n;
}

/* bounding box from an instance's current sprite (fallback 16x16) */
static void rt_bbox(Instance* in, int* l, int* t, int* r, int* b) {
    int w = 16, h = 16, ox = 8, oy = 8;
    if (in->sprite >= 0 && in->sprite < nb_sprite_count) {
        const nb_Sprite* s = &nb_sprites[in->sprite];
        w = s->w; h = s->h; ox = s->ox; oy = s->oy;
    }
    *l = in->x - ox; *t = in->y - oy;
    *r = *l + w - 1; *b = *t + h - 1;
}

Instance* rt_meeting(Instance* self, s16 object) {
    if (!self) return 0;
    int al, at, ar, ab; rt_bbox(self, &al, &at, &ar, &ab);
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* o = &g_inst[i];
        if (!o->active || o == self) continue;
        if (object >= 0 && o->object != object) continue;
        int bl, bt, br, bb; rt_bbox(o, &bl, &bt, &br, &bb);
        if (al <= br && bl <= ar && at <= bb && bt <= ab) return o;
    }
    return 0;
}

/* ---------------- rooms ---------------- */
void rt_room_goto(s16 room) { g_next_room = room; }

static void rt_room_load(s16 room) {
    if (room < 0 || room >= nb_room_count) return;
    rt_clear_instances();
    const nb_Room* r = &nb_rooms[room];
    g_room = r;
    g_room_bg = r->bg;
    BG_PALETTE[0] = r->bg;        /* mode-0 backdrop shows through behind tiles */
    rt_clear_text();
    rt_load_tilemap(r);
    g_cam_x = 0; g_cam_y = 0;
    g_cur_room = room;
    /* room speed (steps/sec) -> VBlanks per step; 0 or >60 means one step/frame */
    {
        u16 sp = r->speed ? r->speed : 60;
        g_step_frames = 60 / sp;
        if (g_step_frames < 1) g_step_frames = 1;
    }
    for (int i = 0; i < r->ninst; i++)
        rt_create(r->insts[i].object, r->insts[i].x, r->insts[i].y);
    /* the camera follows the first placed instance (the player, by convention);
       a "Follow View" action can retarget it later */
    g_view = 0;
    for (int i = 0; i < NB_MAX_INSTANCES; i++)
        if (g_inst[i].active) { g_view = &g_inst[i]; break; }
}

/* ---------------- main loop ---------------- */
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
    /* movement + gravity + animation after all step logic (GM order) */
    for (int i = 0; i < NB_MAX_INSTANCES; i++) {
        Instance* in = &g_inst[i];
        if (!in->active) continue;
        in->vspeed += in->grav;
        in->x += in->hspeed;
        in->y += in->vspeed;
        if (in->image_speed && in->sprite >= 0 && in->sprite < nb_sprite_count) {
            in->image_accum += in->image_speed;
            while (in->image_accum >= 16) {
                in->image_accum -= 16;
                in->image_index++;
                s16 nf = (s16)nb_sprites[in->sprite].nframes;
                if (nf > 0 && in->image_index >= nf) in->image_index = 0;
            }
        }
    }
}

/* Rebuild OAM from the active instances each VBlank: one hardware object per
   visible instance, positioned relative to the camera, unused slots hidden. */
static void rt_render(void) {
    int oi = 0;
    for (int i = 0; i < NB_MAX_INSTANCES && oi < 128; i++) {
        Instance* in = &g_inst[i];
        if (!in->active) continue;
        const nb_Object* ob = &nb_objects[in->object];
        if (!ob->visible) continue;
        if (in->sprite < 0 || in->sprite >= nb_sprite_count) continue;
        const nb_Sprite* s = &nb_sprites[in->sprite];
        int frame = in->image_index;
        if (frame < 0 || (u16)frame >= s->nframes) frame = 0;
        int sx = (int)(in->x - s->ox - g_cam_x);
        int sy = (int)(in->y - s->oy - g_cam_y);
        if (sx <= -(int)s->w || sx >= SCREEN_W ||
            sy <= -(int)s->h || sy >= SCREEN_H)
            continue;                            /* fully off-screen */
        u16 tile = s->tile + (u16)frame * s->tiles_per_frame;
        OAM[oi].attr0 = (u16)(sy & 0x00FF) | (u16)(s->shape << 14);
        OAM[oi].attr1 = (u16)(sx & 0x01FF) | OBJ_SIZE(s->size);
        OAM[oi].attr2 = OBJ_TILE(tile) | OBJ_PALBANK(s->palbank) | (1 << 10);
        oi++;
    }
    for (; oi < 128; oi++) OAM[oi].attr0 = OBJ_HIDE;
}

void rt_run(void) {
    rt_video_init();
    rt_sound_init();
    rt_clear_instances();
    rt_room_load(nb_start_room);
    u16 stepc = 0;
    for (;;) {
        rt_vsync();
        rt_sound_update();
        /* Run the game step at the room's speed. Input is sampled on step frames
           so rt_key_pressed() edges line up with the steps that read them. */
        if (++stepc >= g_step_frames) {
            stepc = 0;
            rt_input_update();
            rt_step_all();
        }
        rt_camera_update();
        REG_BG0HOFS = (u16)g_cam_x;
        REG_BG0VOFS = (u16)g_cam_y;
        rt_render();
        if (g_next_room >= 0 && g_next_room != g_cur_room) {
            s16 nr = g_next_room; g_next_room = -1;
            rt_room_load(nr);
            stepc = 0;
        } else {
            g_next_room = -1;
        }
    }
}

/* Entry: crt0 branches here after RAM setup. */
int main(void) {
    rt_run();
    return 0;
}
