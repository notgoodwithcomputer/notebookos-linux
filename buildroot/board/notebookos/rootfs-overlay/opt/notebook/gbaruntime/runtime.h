/* runtime.h — the Notebook OS GBA SDK game engine contract.

   A generated game supplies the data tables (nb_sprites, nb_objects, nb_rooms,
   nb_sounds, nb_start_room) and the per-object event functions; the runtime owns
   the game loop, the instance list, the mode-0 hardware renderer, the camera,
   input, collision, sound and saving. The model deliberately mirrors Game
   Maker: objects have Create / Step / Destroy events (keyboard, collision and
   alarm logic are folded into Step by the code generator), instances carry
   position + speed + user variables, and rooms place instances.

   COMPATIBILITY RULE FOR THE CODE GENERATOR
   Every field added to nb_Sprite / nb_Object / nb_Room / nb_Sound since the
   first release is APPENDED, and zero always means "behave the way you did
   before". A generated game that initialises only the original leading fields
   still compiles and still runs identically, because C zero-fills the rest. Add
   new values at the END of an initialiser, never in the middle. */
#ifndef NB_RUNTIME_H
#define NB_RUNTIME_H

#include "gba.h"

#define NB_MAX_INSTANCES 128
#define NB_MAX_VARS      12      /* user variables per instance */
#define NB_MAX_ALARMS    4
#define NB_MAX_GLOBALS   32      /* persistent global.* variables */
#define NB_MAX_DEPTH     8       /* drawing layers, 0 = frontmost */

struct Instance;
typedef void (*nb_event_fn)(struct Instance*);

/* A sprite: nframes images rendered as hardware OBJ tiles (4bpp, one 16-colour
   OBJ palette bank each). Placed so its origin lands on the instance position.
   Its frames' tiles live in nb_obj_tiles starting at `tile`, tiles_per_frame
   apart. */
typedef struct {
    u16 w, h;
    s16 ox, oy;             /* origin (the sprite is placed so origin = x,y) */
    u16 nframes;
    u16 tile;               /* base OBJ tile index of frame 0 */
    u16 tiles_per_frame;
    u16 shape;              /* OBJ shape: 0=square 1=wide 2=tall */
    u16 size;               /* OBJ size code 0..3 (with shape gives w x h) */
    u16 palbank;            /* OBJ palette bank 0..15 (its own 16 colours) */
    u16 anim_speed;         /* default image_speed for a new instance */
} nb_Sprite;

/* An object class. Event fns may be 0. If draw is 0 the runtime draws the
   instance's current sprite frame. */
typedef struct {
    s16 sprite;             /* default sprite index, or -1 */
    u8 visible;
    u8 solid;               /* other instances with tilecol >= 2 cannot pass */
    nb_event_fn create;
    nb_event_fn step;
    nb_event_fn draw;
    nb_event_fn destroy;    /* run when an instance of this object is destroyed */
    /* ---- appended; 0 = the original behaviour ---- */
    u8 depth;               /* drawing layer 0..7; 0 = in front (default) */
    u8 tilecol;             /* 0 = move freely, 1 = solid tiles block this
                               object, 2 = solid tiles AND solid instances */
    u8 bb_l, bb_t, bb_r, bb_b;  /* collision-box inset in px from the sprite's
                                   left/top/right/bottom (0 = whole sprite) */
} nb_Object;

typedef struct {
    s16 object;
    s16 x, y;
} nb_InstanceDef;

typedef struct {
    u16 w, h;               /* room size in pixels; the hardware camera scrolls a
                               room larger than the 240x160 screen, and the tile
                               layer streams in, so a room may be any size */
    u16 bg;                 /* BGR555 backdrop colour (shows behind tiles/sprites) */
    u8  speed;              /* game steps per second (0 => 60); the loop runs the
                               step/event logic 60/speed frames apart */
    u16 ninst;
    const nb_InstanceDef* insts;
    const u16* tiles;       /* BG0 tile layer, (w/8)*(h/8) entries row-major,
                               0 = empty; or 0 for no tile layer */
    /* ---- appended; 0 = the original behaviour ---- */
    const u8*  tile_solid;  /* one byte per tileset tile index: nonzero = solid.
                               0 here means the tile layer is decoration only */
    const u16* far_tiles;   /* 32x32 (256x256 px) repeating parallax layer drawn
                               behind everything, or 0 for none */
    u8  far_div;            /* parallax slowdown 1..8 (0 => 2) */
    u8  edge_open;          /* 0 = the room's outside edge is solid, 1 = open */
} nb_Room;

/* A chiptune. Three tracks over `nsteps` steps, each `tempo` frames long:
   `lead` and `bass` are MIDI note numbers (0 = rest, 255 = hold the previous
   note) and `drum` is a percussion code per step. Music plays on square 1
   (lead), square 2 (bass) and noise (drum); a sound marked kind=1 is a sound
   effect and plays on the wave channel instead, so it layers over the music
   rather than stopping it. */
typedef struct {
    u8 tempo;               /* frames per step */
    u8 loop;
    u16 nsteps;
    const u8* lead;
    const u8* bass;
    /* ---- appended; 0 = the original behaviour ---- */
    const u8* drum;         /* per step: 0 none, 1 kick, 2 snare, 3 hat, 4 crash */
    u8 kind;                /* 0 = music, 1 = sound effect (plays over music) */
    u8 duty;                /* 0 = 50% (default), 1..4 = 12.5/25/50/75% */
    u8 vol;                 /* 0 = full (default), else 1..15 */
    u8 decay;               /* 0 = notes hold, 1..7 = plucked (fast..slow) */
} nb_Sound;

/* Built-in sound effects, playable with no data at all: rt_sfx(NB_SFX_COIN). */
enum {
    NB_SFX_BLIP, NB_SFX_JUMP, NB_SFX_COIN, NB_SFX_SHOOT, NB_SFX_HURT,
    NB_SFX_EXPLODE, NB_SFX_POWERUP, NB_SFX_LAND, NB_SFX_SELECT, NB_SFX_ERROR,
    NB_SFX_WARP, NB_SFX_STEP, NB_SFX_COUNT
};

/* Text ink colours for rt_draw_text_c / rt_draw_box. 0 is the default white,
   so plain rt_draw_text is unchanged. */
enum {
    NB_WHITE, NB_BLACK, NB_YELLOW, NB_RED, NB_GREEN, NB_CYAN, NB_BLUE,
    NB_GREY, NB_COLOURS
};

/* Instance flag bits (read them with the rt_* helpers below). */
#define NB_F_BLOCK_H   0x01     /* a wall stopped horizontal movement */
#define NB_F_BLOCK_V   0x02     /* a wall or floor stopped vertical movement */
#define NB_F_GROUND    0x04     /* standing on something solid */
#define NB_F_ANIM_ONCE 0x08     /* stop at the last frame instead of looping */
#define NB_F_ANIM_DONE 0x10     /* a once-through animation has finished */

typedef struct Instance {
    u8  active;
    s16 object;
    s16 sprite;             /* current sprite (-1 = none) */
    s16 image_index;        /* current frame */
    s16 image_speed;        /* frames/step * 16 (0 = no animation) */
    s16 image_accum;        /* sub-frame accumulator for image_speed */
    s32 x, y;               /* position, integer pixels */
    s32 hspeed, vspeed;     /* per-step movement, whole pixels */
    s32 grav;               /* downward accel added to vspeed each step */
    s32 alarm[NB_MAX_ALARMS];
    s32 var[NB_MAX_VARS];   /* user variables, by index (codegen assigns) */
    /* ---- sub-pixel motion: 1/256 px per step, added on top of hspeed/vspeed,
       so a game can move slower than a pixel a step and at any angle ---- */
    s16 hspd8, vspd8;
    s16 grav8;
    s16 xsub, ysub;         /* 1/256 px accumulators */
    /* ---- presentation ---- */
    u8  hidden;             /* 0 = drawn (default) */
    u8  flip;               /* 1 = mirrored horizontally, 2 = vertically */
    u8  depth;              /* drawing layer 0..7, 0 = in front */
    u8  flags;              /* NB_F_* */
    u8  angle;              /* 0..255 = 0..360 degrees (0 with scale 0 = none) */
    s16 scale;              /* 8.8 size, 0 or 256 = normal, 128 = half */
    u8  anim_lo, anim_hi;   /* animation frame range; hi == 0 = all frames */
} Instance;

/* ---- supplied by the generated game ---- */
extern const nb_Sprite nb_sprites[];
extern const int       nb_sprite_count;
extern const u16       nb_obj_palette[256]; /* 16 banks of 16 (per-sprite) */
extern const u16       nb_obj_tiles[];      /* all sprite tiles, 4bpp */
extern const int       nb_obj_tile_count;
extern const u16       nb_bg_palette[16];   /* shared 4bpp BG (tileset) palette */
extern const u16       nb_bg_tiles[];       /* tileset tiles, 4bpp; tile 0 = blank */
extern const int       nb_bg_tile_count;
extern const nb_Object nb_objects[];
extern const int       nb_object_count;
extern const nb_Room   nb_rooms[];
extern const int       nb_room_count;
extern const int       nb_start_room;
extern const nb_Sound  nb_sounds[];
extern const int       nb_sound_count;

/* ---- global game state (Game Maker score/lives/health) + persistent globals ---- */
extern s32 nb_score, nb_lives, nb_health;
extern s32 nb_global[NB_MAX_GLOBALS];

/* ================= engine API used by generated event code ================= */

/* --- the loop --- */
void      rt_run(void);                       /* the game loop (call from main) */

/* --- instances --- */
Instance* rt_create(s16 object, s32 x, s32 y);
void      rt_destroy(Instance* self);
void      rt_destroy_object(s16 object);      /* destroy every instance of it */
int       rt_instance_count(s16 object);      /* active instances of object */
Instance* rt_find(s16 object);                /* the first live one, or 0 */
Instance* rt_nearest(Instance* self, s16 object);
Instance* rt_other(void);                     /* what the last collision hit */
s32       rt_x_of(Instance* in);              /* 0-safe position readers */
s32       rt_y_of(Instance* in);
s32       rt_var_of(Instance* in, int slot);
void      rt_set_var_of(Instance* in, int slot, s32 v);

/* --- input --- */
int       rt_key_held(u16 key);
int       rt_key_pressed(u16 key);
int       rt_key_released(u16 key);           /* pressed last step, not this one */

/* --- collision --- */
Instance* rt_meeting(Instance* self, s16 object);  /* bbox overlap, -1 = any */
Instance* rt_place_meeting(Instance* self, s32 x, s32 y, s16 object);
int       rt_tile_solid(s32 px, s32 py);      /* is the room solid at a pixel */
int       rt_place_free(Instance* self, s32 x, s32 y);   /* clear of solid tiles */
int       rt_place_free_all(Instance* self, s32 x, s32 y); /* + solid instances */
int       rt_blocked_h(Instance* self);       /* a wall stopped it this step */
int       rt_blocked_v(Instance* self);
int       rt_on_ground(Instance* self);
void      rt_jump(Instance* self, s32 power); /* jump if standing on something */
void      rt_bounce(Instance* self);          /* reverse the blocked direction */

/* --- movement --- */
void      rt_move_toward(Instance* self, s32 tx, s32 ty, s32 speed);
void      rt_move_toward8(Instance* self, s32 tx, s32 ty, s32 speed8);
void      rt_set_speed_dir(Instance* self, s32 dir, s32 speed8);
void      rt_chase(Instance* self, s16 object, s32 speed8);
s32       rt_dir_to(Instance* self, s32 tx, s32 ty);   /* 0..255 */
s32       rt_dist_to(Instance* self, s32 tx, s32 ty);
s32       rt_dist_to_object(Instance* self, s16 object);
s32       rt_sin8(s32 dir);                   /* 8.8 fixed point, dir 0..255 */
s32       rt_cos8(s32 dir);

/* --- presentation --- */
void      rt_set_flip(Instance* self, int h, int v);
void      rt_face_motion(Instance* self);     /* mirror to match hspeed's sign */
void      rt_set_visible(Instance* self, int on);
void      rt_set_depth(Instance* self, int depth);
void      rt_set_angle(Instance* self, s32 dir);       /* 0..255 */
void      rt_set_scale(Instance* self, s32 scale8);    /* 256 = normal size */
void      rt_anim_range(Instance* self, int lo, int hi);
void      rt_anim_once(Instance* self, int lo, int hi);
int       rt_anim_done(Instance* self);

/* --- rooms and camera --- */
void      rt_room_goto(s16 room);
void      rt_room_goto_fade(s16 room);        /* fade out, switch, fade in */
void      rt_room_restart(void);
s16       rt_room(void);
void      rt_view_follow(Instance* self);     /* the camera tracks this instance */
void      rt_view_fixed(s32 x, s32 y);        /* pin the camera and stop tracking */
s32       rt_view_x(void);
s32       rt_view_y(void);

/* --- screen effects --- */
void      rt_fade(s32 amount);                /* -16 = black .. 0 = off .. 16 = white */
void      rt_flash(s32 frames);
void      rt_shake(s32 frames, s32 magnitude);

/* --- sound --- */
void      rt_play_sound(s16 sound);           /* by the sound's own kind */
void      rt_play_music(s16 sound);           /* music slot: square 1/2 + drums */
void      rt_play_sfx(s16 sound);             /* effect slot: the wave channel */
void      rt_sfx(int preset);                 /* a built-in NB_SFX_* effect */
void      rt_stop_music(void);
void      rt_stop_sfx(void);
void      rt_music_volume(int vol);           /* 0..7 */

/* --- text, HUD and dialogue (the BG1 layer, 30x20 cells of 8x8) --- */
void      rt_draw_text(int col, int row, const char* s);
void      rt_draw_text_c(int col, int row, const char* s, int colour);
void      rt_draw_text_centre(int row, const char* s, int colour);
void      rt_draw_int(int col, int row, s32 value);
void      rt_draw_int_c(int col, int row, s32 value, int colour);
void      rt_draw_int_pad(int col, int row, s32 value, int digits, int colour);
void      rt_draw_box(int col, int row, int w, int h, int colour);
void      rt_draw_panel(int col, int row, int w, int h, int fill, int border);
void      rt_clear_text(void);
void      rt_clear_box(int col, int row, int w, int h);

/* --- randomness --- */
s32       rt_random(s32 n);                   /* 0 .. n-1 */
void      rt_random_seed(s32 seed);

/* --- persistence (32 KiB battery-backed SRAM) --- */
void      rt_game_save(void);                 /* score/lives/health + globals -> SRAM */
int       rt_game_load(void);                 /* SRAM -> ...; 1 if a save existed */
s32       rt_highscore(void);                 /* best score ever saved (0 if none) */
int       rt_highscore_submit(void);          /* keep score if it beats the best */

#endif
