/* runtime.h — the Notebook OS GBA IDE game engine contract.

   A generated game supplies the data tables (nb_sprites, nb_objects, nb_rooms,
   nb_start_room) and the per-object event functions; the runtime owns the game
   loop, the instance list, software sprite blitting, input and collision. The
   model deliberately mirrors Game Maker: objects have Create / Step / Draw
   events (keyboard, collision and alarm logic are folded into Step by the code
   generator), instances carry position + speed + user variables, and rooms
   place instances. */
#ifndef NB_RUNTIME_H
#define NB_RUNTIME_H

#include "gba.h"

#define NB_MAX_INSTANCES 128
#define NB_MAX_VARS      12      /* user variables per instance */
#define NB_MAX_ALARMS    4
#define NB_MAX_GLOBALS   32      /* persistent global.* variables */

struct Instance;
typedef void (*nb_event_fn)(struct Instance*);

/* A sprite: nframes images rendered as hardware OBJ tiles (4bpp, shared 16-colour
   OBJ palette). Placed so its origin lands on the instance position. Its frames'
   tiles live in nb_obj_tiles starting at `tile`, tiles_per_frame apart. */
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
    u8 solid;
    nb_event_fn create;
    nb_event_fn step;
    nb_event_fn draw;
    nb_event_fn destroy;    /* run when an instance of this object is destroyed */
} nb_Object;

typedef struct {
    s16 object;
    s16 x, y;
} nb_InstanceDef;

typedef struct {
    u16 w, h;               /* room size in pixels; the hardware camera scrolls a
                               room larger than the 240x160 screen */
    u16 bg;                 /* BGR555 backdrop colour (shows behind tiles/sprites) */
    u8  speed;              /* game steps per second (0 => 60); the loop runs the
                               step/event logic 60/speed frames apart */
    u16 ninst;
    const nb_InstanceDef* insts;
    const u16* tiles;       /* BG0 tile layer, (w/8)*(h/8) entries row-major,
                               0 = empty; or 0 for no tile layer */
} nb_Room;

/* A chiptune: two monophonic voices (lead on square 1, bass on square 2) over
   `nsteps` steps, each `tempo` frames long. A note is a MIDI number (0 = rest,
   255 = hold the previous note). Plays via the GBA PSG square channels. */
typedef struct {
    u8 tempo;               /* frames per step */
    u8 loop;
    u16 nsteps;
    const u8* lead;
    const u8* bass;
} nb_Sound;

typedef struct Instance {
    u8  active;
    s16 object;
    s16 sprite;             /* current sprite (-1 = none) */
    s16 image_index;        /* current frame */
    s16 image_speed;        /* frames/step * 16 (0 = no animation) */
    s16 image_accum;        /* sub-frame accumulator for image_speed */
    s32 x, y;               /* position, integer pixels */
    s32 hspeed, vspeed;     /* per-step movement */
    s32 grav;               /* downward accel added to vspeed each step */
    s32 alarm[NB_MAX_ALARMS];
    s32 var[NB_MAX_VARS];   /* user variables, by index (codegen assigns) */
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

/* ---- engine API used by generated event code ---- */
void      rt_run(void);                       /* the game loop (call from main) */
Instance* rt_create(s16 object, s32 x, s32 y);
void      rt_destroy(Instance* self);
int       rt_key_held(u16 key);
int       rt_key_pressed(u16 key);
Instance* rt_meeting(Instance* self, s16 object);  /* bbox overlap, -1 = any */
void      rt_room_goto(s16 room);
s32       rt_random(s32 n);                   /* 0 .. n-1 */
int       rt_instance_count(s16 object);      /* active instances of object */
void      rt_play_sound(s16 sound);           /* start a chiptune (-1 = stop) */
void      rt_move_toward(Instance* self, s32 tx, s32 ty, s32 speed);
void      rt_destroy_object(s16 object);      /* destroy every instance of it */
int       rt_key_released(u16 key);           /* pressed last step, not this one */
void      rt_draw_text(int col, int row, const char* s);  /* BG1 text (8px cells) */
void      rt_clear_text(void);
void      rt_draw_int(int col, int row, s32 value);
void      rt_game_save(void);                 /* score/lives/health + globals -> SRAM */
int       rt_game_load(void);                 /* SRAM -> ...; 1 if a save existed */

#endif
