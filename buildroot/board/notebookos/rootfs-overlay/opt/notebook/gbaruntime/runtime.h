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

/* Put a function in IWRAM: 32-bit bus, no wait states, against ROM's 16-bit
   bus with them. For the few functions called thousands of times a frame --
   there are 32 KB of IWRAM shared with every variable in the game, so this is
   not free and not for everything. */
#define IWRAM_CODE __attribute__((section(".iwram"), long_call))

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
    u8 hurt_frames;         /* mercy frames granted when a step of this object
                               costs health; while they count down the sprite
                               blinks and its collision tests report nothing.
                               0 = no mercy (the original behaviour) */
    nb_event_fn on_no_health;   /* fired once when health reaches zero; fires
                                   again only after health has risen above
                                   zero. 0 = nothing happens (the original
                                   behaviour) */
} nb_Object;

typedef struct {
    s16 object;
    s16 x, y;
} nb_InstanceDef;

/* A room-to-room link: a rectangle in this room, and where arriving in the
   target room puts the traveller. Checked against the instance the camera
   follows -- the player, by convention -- because a warp that any instance
   could trip would fire on every wandering enemy. */
typedef struct {
    u16 x, y, w, h;         /* the rectangle, in room pixels */
    s16 room;               /* destination room index */
    u16 tx, ty;             /* where the traveller lands there */
} nb_Warp;

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
    const nb_Warp* warps;   /* room-to-room links, or 0 for none */
    u16 nwarps;
    /* ---- appended; 0 = the original behaviour ---- */
    const u8* aff_map;      /* an AFFINE ground layer: one 8-bit tile index
                               per cell, or 0 for the flat tile layer. A room
                               with one gives up its parallax layer and its
                               own flat tiles -- mode 1 has two text layers
                               where mode 0 has four. */
    u8 aff_size;            /* 0 = 16x16 cells, 1 = 32x32 */
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
    u8 prio;                /* 0..7; a playing effect is only replaced by one
                               of EQUAL OR HIGHER priority. 0 means "anything
                               may interrupt me", which is what every sound did
                               before this existed. */
    const signed char* pcm; /* sampled audio, signed 8-bit at 16384 Hz, or 0 */
    u32 pcm_len;
    /* ---- appended; 0 = the original behaviour ---- */
    u8 pcm_loop;            /* 1 = a soundtrack: plays looping on the second
                               PCM voice, under one-shot effects, until
                               rt_stop_music(). 0 = a one-shot sample. */
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
    /* ---- appended; 0 = the original behaviour ---- */
    s16 gx, gy;             /* glide target */
    u16 glide;              /* frames of glide left, 0 = not gliding */
    u8  inv;                /* mercy frames left; collision tests of an object
                               with hurt_frames report nothing while set */
    u8  objwin;             /* 1 = this sprite is the OBJ window stencil
                               rather than a drawn image */
    u8  palbank;            /* OBJ palette bank OVERRIDE, held as bank+1 so
                               that 0 means "use the sprite's own" -- bank 0
                               is a real bank and could not signal it */
    u32 serial;             /* identity of this occupancy of its pool slot */
} Instance;

/* ---- supplied by the generated game ---- */
extern const int       nb_save_type;   /* 0 SRAM 32K, 1 Flash 64K, 2 Flash 128K */
extern const nb_Sprite nb_sprites[];
extern const int       nb_sprite_count;
extern const u16       nb_obj_palette[256]; /* 16 banks of 16 (per-sprite) */
extern const u16       nb_obj_tiles[];      /* all sprite tiles, 4bpp */
extern const int       nb_obj_tile_count;
extern const u16       nb_bg_palette[16];   /* shared 4bpp BG (tileset) palette */
extern const u16       nb_bg_tiles[];       /* tileset tiles, 4bpp; tile 0 = blank */
extern const int       nb_bg_tile_count;
extern const u16       nb_aff_tiles[];      /* affine tileset, 8bpp (64 bytes a tile) */
extern const int       nb_aff_tile_count;
extern const u16       nb_aff_palette[256]; /* the affine layer's own 256 colours */
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
Instance* rt_create(s16 object, s32 x, s32 y); /* the new instance, or 0 if 128 are already live */
void      rt_destroy(Instance* self);         /* gone at the end of this frame, not immediately */
void      rt_destroy_object(s16 object);      /* destroy every instance of it */
int       rt_instance_count(s16 object);      /* active instances of object */
Instance* rt_find(s16 object);                /* the first live one, or 0 */
Instance* rt_nearest(Instance* self, s16 object); /* nearest live instance of that object, or 0 */
Instance* rt_other(void);                     /* what the last collision hit */
s32       rt_x_of(Instance* in);              /* 0-safe position readers */
s32       rt_y_of(Instance* in);              /* y in room pixels */
s32       rt_var_of(Instance* in, int slot);  /* instance variable, slot 0..11 */
void      rt_set_var_of(Instance* in, int slot, s32 v); /* store to slot 0..11; out-of-range is ignored */

/* --- input --- */
int       rt_key_held(u16 key);               /* down this frame; KEY_A .. KEY_R from gba.h */
int       rt_key_pressed(u16 key);            /* went down ON this frame only */
int       rt_key_released(u16 key);           /* pressed last step, not this one */

/* --- collision --- */
Instance* rt_meeting(Instance* self, s16 object);  /* bbox overlap, -1 = any */
Instance* rt_place_meeting(Instance* self, s32 x, s32 y, s16 object); /* what would be hit at x,y, or 0 -- test before moving */
int       rt_tile_solid(s32 px, s32 py);      /* is the room solid at a pixel */
int       rt_place_free(Instance* self, s32 x, s32 y);   /* clear of solid tiles */
int       rt_place_free_all(Instance* self, s32 x, s32 y); /* + solid instances */
int       rt_blocked_h(Instance* self);       /* a wall stopped it this step */
int       rt_blocked_v(Instance* self);       /* solid tile above or below */
int       rt_on_ground(Instance* self);       /* solid tile directly beneath */
void      rt_jump(Instance* self, s32 power); /* jump if standing on something */
void      rt_bounce(Instance* self);          /* reverse the blocked direction */

/* --- movement --- */
void      rt_move_toward(Instance* self, s32 tx, s32 ty, s32 speed); /* speed in whole pixels per frame */
void      rt_move_toward8(Instance* self, s32 tx, s32 ty, s32 speed8); /* speed in 8.8 fixed point: 256 is one pixel/frame */
void      rt_set_speed_dir(Instance* self, s32 dir, s32 speed8); /* dir is 0..255 for a full turn, not degrees */
void      rt_chase(Instance* self, s16 object, s32 speed8); /* steer toward the nearest one each frame */
s32       rt_dir_to(Instance* self, s32 tx, s32 ty);   /* 0..255 */
s32       rt_dist_to(Instance* self, s32 tx, s32 ty); /* distance in whole pixels */
s32       rt_dist_to_object(Instance* self, s16 object); /* to the nearest one; -1 if none is live */
s32       rt_sin8(s32 dir);                   /* 8.8 fixed point, dir 0..255 */
s32       rt_cos8(s32 dir);                   /* 8.8 fixed point, dir 0..255 */

/* --- presentation --- */
void      rt_set_flip(Instance* self, int h, int v); /* mirror the sprite; costs nothing, no extra tiles */
void      rt_face_motion(Instance* self);     /* mirror to match hspeed's sign */
void      rt_set_visible(Instance* self, int on); /* hidden instances still run their events */
void      rt_set_depth(Instance* self, int depth); /* 0..3 sprite priority; lower draws in front */
void      rt_set_angle(Instance* self, s32 dir);       /* 0..255 */
void      rt_set_scale(Instance* self, s32 scale8);    /* 256 = normal size */
void      rt_anim_range(Instance* self, int lo, int hi); /* loop frames lo..hi of the sprite */
void      rt_anim_once(Instance* self, int lo, int hi); /* play lo..hi and stop on hi */
int       rt_anim_done(Instance* self);       /* true once a once-through animation has finished */

/* --- rooms and camera --- */
void      rt_room_goto(s16 room);             /* takes effect at the end of the frame */
void      rt_room_goto_fade(s16 room);        /* fade out, switch, fade in */
void      rt_room_goto_at(s16 room, s32 x, s32 y); /* change room and land at x,y */
void      rt_room_restart(void);              /* reload the current room; score and lives survive */
s16       rt_room(void);                      /* index of the room now running */
void      rt_view_follow(Instance* self);     /* the camera tracks this instance */
void      rt_view_fixed(s32 x, s32 y);        /* pin the camera and stop tracking */
s32       rt_view_x(void);                    /* camera left edge in room pixels */
s32       rt_view_y(void);                    /* camera top edge in room pixels */

/* --- screen effects --- */
void      rt_fade(s32 amount);                /* -16 = black .. 0 = off .. 16 = white */
void      rt_flash(s32 frames);               /* white over everything, fading over that many frames */
void      rt_shake(s32 frames, s32 magnitude); /* magnitude in pixels of camera offset */

/* --- sound --- */
void      rt_play_sound(s16 sound);           /* by the sound's own kind */
void      rt_play_music(s16 sound);           /* music slot: square 1/2 + drums */
void      rt_play_sfx(s16 sound);             /* effect slot: the wave channel */
/* ---- measuring text --------------------------------------------------------
 * rt_text_width gives the width in PIXELS from each glyph's own width;
 * rt_text_cells gives it in whole 8-pixel cells, which is what the current
 * renderer advances by. Both ignore control codes, so a coloured string
 * measures as what it will look like rather than as what was typed.
 *
 * Drawing is still one glyph per cell. The widths exist because centring,
 * fitting and a proportional renderer all need them, and getting them right
 * first makes that renderer a small change. */
int  rt_text_width(const char* s);            /* width in pixels, from each glyph's own width */
int  rt_text_cells(const char* s);

/* ---- dialogue --------------------------------------------------------------
 * A message revealed a character at a time, in a panel, advanced by A. Written
 * once here rather than in every speaking object: by hand it is a timer, a
 * cursor, a page counter and a wait-for-button, which is five of the twelve
 * variables an instance has.
 *
 * Control codes live in the text, because dialogue is authored as text:
 *
 *   \n      a new line
 *   {p}     hold until A, then clear and carry on
 *   {s:N}   frames per character; 0 puts the rest of the line up at once
 *   {c:N}   colour (a TXT_ value)
 *   {v:N}   the value of global N, in decimal
 *   {w:N}   pause N frames without waiting for a button
 *
 * An unknown code prints AS WRITTEN. Swallowing it would erase the rest of a
 * sentence over a typo, which is the worst thing this can do to a writer.
 *
 * rt_say_step runs from the main loop; a game does not call it. */
void rt_say(const char *text);
void rt_say_end(void);                        /* close the panel now, whatever is left unsaid */
int  rt_say_active(void);                     /* 1 while a message is on screen */
int  rt_say_step(void);
void rt_say_voice(s16 sound);   /* played per character; -1 for silence */

/* Proportional text in the dialogue panel: each glyph advances by its own
   width rather than by a whole cell, which fits about half again as much in
   the same box.
   Only the panel, because proportional text needs a RAM copy of the tiles it
   draws into: the panel is 3.3 KB, the whole text layer would be 19 KB.
   COLOUR RESOLVES PER TILE -- 4bpp colour comes from the map entry's palette
   bank and a tile has one -- so a colour change takes effect at the next tile
   boundary. */
void rt_vwf(int on);
int  rt_vwf_enabled(void);                    /* 1 while the panel draws proportionally */

/* ---- the profiler ----------------------------------------------------------
 * Where the frame went. rt_prof(1) starts it; the engine then measures its own
 * step, movement and drawing, and slots 3..7 are the project's.
 *
 *   rt_prof_begin(3); my_work(); rt_prof_end(3);
 *   int pc = rt_prof_percent(3);     percent of one frame
 *
 * Uses TIMER 2: timer 0 belongs to the project and timer 1 clocks sampled
 * audio, so taking either would break the thing being measured.
 *
 * Figures are for the LAST WHOLE FRAME. Reading mid-frame would give a number
 * that changes depending on when it was asked for. */
void rt_prof(int on);
void rt_prof_begin(int slot);                 /* start counting into a slot */
void rt_prof_end(int slot);                   /* stop counting; several sections may share a slot */
int  rt_prof_ticks(int slot);                 /* ticks that slot cost last frame; 4389 is a whole frame */
int  rt_prof_percent(int slot);
void rt_prof_overlay(void);      /* a corner read-out, drawn when called */

/* ---- cutscenes -------------------------------------------------------------
 * rt_glide moves an instance to a point over N frames. By hand that is a start,
 * a target, a frame count and a division per axis per frame -- four of the
 * twelve variables an instance has, spent on arithmetic.
 *
 * The remaining distance is divided by the remaining FRAMES, so the last frame
 * lands exactly on the target rather than a rounded pixel short of it. A glide
 * overrides speed while it runs.
 *
 * rt_input_lock stops the player walking out of a scripted scene. It is read by
 * the key calls rather than clearing the key state, so a pause menu can still
 * ask what is held while everything else is frozen. */
void rt_glide(Instance *in, s32 x, s32 y, s32 frames);
int  rt_gliding(Instance *in);                /* 1 while a glide is still running */
void rt_glide_stop(Instance *in);             /* abandon a glide where it is, without arriving */
void rt_input_lock(int on);
int  rt_input_locked(void);                   /* 1 while the key calls are refusing to answer */
/* Past the lock, for the one thing that must still work while it is on. */
int  rt_key_raw(u16 key);
int  rt_key_raw_pressed(u16 key);             /* went down this frame, past the lock */

/* ---- menus -----------------------------------------------------------------
 * A list with a cursor, drawn in a panel. Non-blocking, like the dialogue
 * engine: a menu that spins its own loop stops the music, the animation and
 * the link cable while it is open.
 *
 *   rt_menu_open(items, n, col, row, w);
 *   int r = rt_menu_step();     -1 still open, >= 0 chosen, -2 cancelled
 *
 * Shows up to 8 rows and scrolls to keep the cursor in view, with an arrow at
 * the edge when there is more above or below. Up and down wrap by default. */
void rt_menu_open(const char *const *items, int n, int col, int row, int w); /* no answer variable; rt_menu_step reports the choice */
void rt_menu_close(void);                     /* take it away without choosing anything */
int  rt_menu_step(void);
int  rt_menu_active(void);                    /* 1 while a menu is up */
int  rt_menu_index(void);                     /* the line the cursor is on right now */
void rt_menu_wrap(int on);                    /* whether up from the first goes to the last (on by default) */

/* Open a menu and put the answer in one of an instance's variables when it
   closes: the chosen index, or -2 if cancelled. Held at -1 while the menu is
   up, so a Step event can tell "still choosing" from "chose the first item".
   This is what the Show Menu action uses -- an action cannot wait for a
   choice, so it names a variable and the next Step event branches on it. */
void rt_menu_open_var(const char *const *items, int n, int col, int row, int w,
                      Instance *who, int slot);

/* ---- the cartridge clock ---------------------------------------------------
 * The real-time clock is on the CARTRIDGE, not in the console, so whether a
 * game can tell the time depends on the cartridge it is in. Both calls report
 * failure rather than returning a plausible date: a day-night cycle that
 * silently believes it is midnight on the 1st of January is worse than one
 * that knows it cannot tell the time.
 *
 * The bit-banged transfer has NOT been run against the chip. The command
 * encoding, the BCD conversion and the rejection of an absent clock are
 * covered by tools/gbaruntime_selftest.py. */
typedef struct {
    u16 year;               /* 2000..2099 */
    u8  month, day;         /* 1..12, 1..31 */
    u8  weekday;            /* 0..6 */
    u8  hour, minute, second;
} nb_DateTime;

int  rt_rtc_read(nb_DateTime *out);   /* 1 on success, 0 if there is no clock */
int  rt_rtc_present(void);                    /* 1 if this cartridge answers with a believable date */

/* ---- the link cable --------------------------------------------------------
 * Multiplayer mode, two to four units, ONE HALFWORD from each per transfer.
 * That is the whole budget: about 16 bytes per frame shared by the session at
 * 9600 baud. The shape that fits is exchanging INPUT and running the same
 * simulation on every unit, not sending game state.
 *
 *   rt_link_open(SIO_9600)      once; returns 1 if every unit is connected
 *   rt_link_send(word)          latch what this unit sends next
 *   rt_link_start()             PARENT ONLY; a child may not start a transfer
 *   rt_link_poll()              1 when a transfer finished and words arrived
 *   rt_link_recv(0..3)          each unit's word; 0xFFFF where none answered
 *
 * Nothing blocks: a game that waits for a transfer drops frames on a cable
 * that is merely slow. */
int  rt_link_open(u16 baud);
void rt_link_close(void);                     /* give the port back; a game not linking should */
int  rt_link_ready(void);      /* every unit connected and in multiplayer mode */
int  rt_link_parent(void);     /* 1 on the unit that starts transfers */
int  rt_link_id(void);         /* this unit's 0..3 */
int  rt_link_busy(void);       /* a transfer is running */
void rt_link_send(u16 word);
int  rt_link_start(void);
int  rt_link_poll(void);
u16  rt_link_recv(int unit);
int  rt_link_players(void);    /* units that answered the last transfer */

/* ---- sampled audio ---------------------------------------------------------
 * One PCM voice on Direct Sound A. Samples are SIGNED 8-bit at 16384 Hz, which
 * is the only rate: the timer period is the sample rate, so the conversion
 * happens on import instead of at play time.
 *
 * It holds timer 1 and DMA1 while it plays. Timer 0 is deliberately left for
 * the project. Playback stops itself when the sample runs out; without that
 * the DMA repeats its buffer forever, which sounds like a stuck note. */
void      rt_pcm_play(const void *data, u32 nsamples); /* nsamples is a count of BYTES, one per sample */
void      rt_pcm_stop(void);                  /* silence and release timer 1 and DMA 1 */
int       rt_pcm_playing(void);           /* 1 while a sample is sounding */
void      rt_pcm_play_b(const void* data, u32 nsamples, int loop); /* the soundtrack voice */
/* Rotate a contiguous run of palette entries: slot number back, or -1 if
 * all four slots are busy or the range is bad. obj: 0 = background
 * palette, 1 = sprites. Steps happen in the VBlank flush, so a rotation
 * is never torn mid-frame.
 *
 * A CYCLE IS GLOBAL AND SURVIVES A ROOM CHANGE, like every other screen
 * effect here -- a fade, a shake and a mosaic all persist too, because a
 * fade-out has to survive the transition it is covering. A cycle started
 * for one room's waterfall therefore keeps rotating in the next room,
 * where it looks like colour corruption rather than an effect. Stop it
 * with rt_pal_cycle_stop(-1) in the room's own Create event if it
 * belongs to the scene rather than the game. */
int       rt_pal_cycle(int obj, int first, int count, int frames);
void      rt_window_obj(int inside);          /* layers visible inside the sprite-shaped window */

/* ---- power, and the cartridge's own hardware ----------------------------
 * rt_sleep stops the console until a button wakes it; rt_wait_vblank idles
 * the CPU for a frame instead of spinning. Rumble, the solar sensor and the
 * gyro are all CARTRIDGE hardware on the same four GPIO pins the clock uses,
 * so a cartridge carries at most one of them; rt_gpio_release hands the pins
 * back when done. */
void      rt_sleep(void);                     /* stop until a button is pressed */
void      rt_wait_vblank(void);               /* idle the CPU until the next interrupt */
void      rt_rumble(int on);                  /* the motor, on cartridges that have one */
int       rt_solar(void);                     /* 0..255; small means bright */
int       rt_gyro(void);                      /* 12-bit angular rate, centre about 0x6C0 */
void      rt_gpio_release(void);              /* return the pins to the clock */
void      rt_window_obj_off(void);
void      rt_set_objwin(Instance* self, int on); /* this sprite becomes the stencil */
void      rt_pal_cycle_stop(int slot);        /* one slot, or -1 for all */

/* ---- palettes at run time ------------------------------------------------
 * A sprite's colours without a second copy of its tiles: rt_set_palbank puts
 * ONE INSTANCE on a different OBJ palette bank, so the same enemy can wear
 * four team colours for 16 colours each instead of four tile sets. The
 * direct writes are for colours a game computes rather than an artist picks.
 * Palette RAM is not double-buffered, so call these from a Step event. */
void      rt_set_palbank(Instance* self, int bank);  /* 0..15 */
void      rt_clear_palbank(Instance* self);   /* back to the sprite's own */
void      rt_pal_set(int obj, int index, u16 colour); /* obj: 0 = BG, 1 = sprites */
u16       rt_pal_get(int obj, int index);
void      rt_pal_load(int obj, int first, const u16* colours, int count);
void      rt_pcm_stop_b(void);               /* silence the soundtrack voice */
int       rt_pcm_playing_b(void);            /* 1 while the soundtrack voice plays */
void      rt_sfx(int preset);                 /* a built-in NB_SFX_* effect */
void      rt_stop_music(void);                /* silence the music channels; effects keep playing */
void      rt_stop_sfx(void);                  /* silence effects; music keeps playing */
void      rt_music_volume(int vol);           /* 0..7 */

/* --- text, HUD and dialogue (the BG1 layer, 30x20 cells of 8x8) --- */
void      rt_draw_text(int col, int row, const char* s); /* col 0..29, row 0..19; clipped, never wrapped */
void      rt_draw_text_c(int col, int row, const char* s, int colour); /* colour is one of the TXT_ constants above */
void      rt_draw_text_centre(int row, const char* s, int colour); /* centred across all 30 columns */
void      rt_draw_int(int col, int row, s32 value); /* no padding; a shorter number leaves the old digits */
void      rt_draw_int_c(int col, int row, s32 value, int colour); /* as rt_draw_int, in a TXT_ colour */
void      rt_draw_int_pad(int col, int row, s32 value, int digits, int colour); /* zero-padded to that many digits -- use for a HUD */
void      rt_draw_box(int col, int row, int w, int h, int colour); /* fill a rectangle of cells with one colour */
void      rt_draw_panel(int col, int row, int w, int h, int fill, int border); /* filled box with a one-cell border, for dialogue */
void      rt_clear_text(void);                /* wipe the whole text layer */
void      rt_clear_box(int col, int row, int w, int h); /* wipe one rectangle of cells */

/* --- randomness --- */
s32       rt_random(s32 n);                   /* 0 .. n-1 */
void      rt_random_seed(s32 seed);           /* same seed, same sequence -- for a repeatable run */

/* --- persistence (32 KiB battery-backed SRAM) --- */
void      rt_game_save(void);                 /* score/lives/health + globals -> SRAM */
int       rt_game_load(void);                 /* SRAM -> ...; 1 if a save existed */
s32       rt_highscore(void);                 /* best score ever saved (0 if none) */
int       rt_highscore_submit(void);          /* keep score if it beats the best */


/* ---- interrupts (Phase 6) ------------------------------------------------
 * rt_irq_set(IRQ_TIMER0, my_fn) installs a handler; passing 0 removes it and
 * disables that source. `mask` may name several. Handlers run with IRQs
 * disabled and must be short — a long one delays every other source,
 * including the VBlank the display is waiting on. */
typedef void (*rt_irq_fn)(void);
void rt_irq_set(u16 mask, rt_irq_fn fn);
u32  rt_frame_count(void);          /* VBlanks since the cartridge started */

/* ---- timers (Phase 6) -----------------------------------------------------
 * rt_timer_start takes the PERIOD in ticks, not a reload value — the counter
 * counts up to overflow and the subtraction is done inside. Ticks are in the
 * units chosen by the frequency flag: TM_FREQ_1 is one CPU cycle
 * (16.78 MHz), so a 1/60 s period at TM_FREQ_1024 is 273 ticks.
 * Add TM_IRQ and install a handler with rt_irq_set(IRQ_TIMER0, fn) to be
 * called on overflow; TM_CASCADE chains a timer to the one below it, which is
 * how a counter longer than 16 bits is built. */
void rt_timer_start(int ch, u32 ticks, u16 flags);
void rt_timer_stop(int ch);                   /* stop it; a stopped timer keeps its last value */
u16  rt_timer_read(int ch);                   /* the counter now, counting UP toward its overflow */

/* ---- DMA (Phase 6) --------------------------------------------------------
 * rt_dma copies `count` units (DMA_16 or DMA_32) and is faster than a CPU loop
 * for anything of size. Channel choice matters: 0 is highest priority and
 * internal memory only, 1 and 2 are reserved for sound if the project plays
 * PCM, 3 is the general one.
 *
 * rt_hdma_start arms a per-scanline transfer: one entry of `table` is written
 * to `reg` at every HBlank, for free. A 160-entry table of BG0 horizontal
 * offsets gives a wave or heat-haze effect; a table of palette values gives a
 * gradient sky. Arm it during VBlank and leave it armed. */
void rt_dma(int ch, void *dst, const void *src, u32 count, u32 flags);
void rt_dma_stop(int ch);                     /* disable the channel; a repeating DMA runs forever otherwise */
void rt_hdma_start(int ch, void *reg, const void *table, u32 units_per_line);

/* ---- BIOS calls (Phase 6) -------------------------------------------------
 * Decompression in ROM-resident BIOS code: costs no cartridge space and runs
 * faster than anything compiled here. Graphics are stored compressed and
 * expanded on the way to VRAM, which is how a project fits.
 *   rt_lz77_vram   LZ77 -> VRAM (writes 16 bits at a time; VRAM requires it)
 *   rt_lz77_wram   LZ77 -> EWRAM/IWRAM
 *   rt_huff        Huffman, better on data with few distinct values
 *   rt_rl_vram     run-length, best on flat tilemaps
 * The source must carry the BIOS header word the encoder writes; the SDK's
 * asset pipeline produces it.
 *
 * rt_div and rt_sqrt are integer maths with no divide instruction on this CPU.
 * rt_div returns 0 for a zero divisor rather than hanging in the BIOS. */
void rt_lz77_vram(const void *src, void *dst);
void rt_lz77_wram(const void *src, void *dst);
void rt_huff(const void *src, void *dst);
void rt_rl_vram(const void *src, void *dst);
s32  rt_div(s32 num, s32 den);
u16  rt_sqrt(u32 v);

/* ---- colour blending (Phase 7) --------------------------------------------
 * rt_blend_alpha mixes two SETS of layers: `top` names what is blended (the
 * BLD_A_ constants) and `bottom` what it is blended with (BLD_B_). Naming only
 * one produces no visible change and no error. eva/evb are the two weights,
 * 0..16 each, and they may sum past 16 to over-brighten.
 *
 * rt_blend_brightness fades the named layers alone: -16 to black, +16 to
 * white. It is what a room transition or a hit flash uses. */
void rt_blend_alpha(u16 top, u16 bottom, int eva, int evb);
void rt_blend_brightness(u16 layers, int amount);
void rt_blend_off(void);                      /* no blending at all, and no fade */

/* ---- windows (Phase 7) -----------------------------------------------------
 * rt_window(n, x, y, w, h, inside, outside) with n 0 or 1. `inside` and
 * `outside` are WIN_ layer masks: which layers are drawn in the rectangle and
 * which everywhere else. WIN_BLEND in a mask lets colour effects apply there,
 * which is how one region is dimmed and the rest left alone.
 *
 * A width of 0 really is nothing: the hardware would read it as full screen.
 * rt_window_off(n) disables one, or anything other than 0 or 1 disables all. */
void rt_window(int n, int x, int y, int w, int h, u16 inside, u16 outside);
void rt_window_off(int n);

/* ---- mosaic (Phase 7) ------------------------------------------------------
 * Block sizes 1..16, where 1 is off. Applies only to layers that also carry
 * the mosaic bit in their own control register (BGCNT_MOSAIC, OBJ_MOSAIC) --
 * setting sizes alone changes nothing visible. */
void rt_mosaic(int bg_w, int bg_h, int obj_w, int obj_h); /* sizes are 1..16, where 1 is off */

/* ---- affine (Phase 7) ------------------------------------------------------
 * rt_bg_affine(bg, tx, ty, sx, sy, angle, scale): put texture pixel (tx,ty) at
 * screen pixel (sx,sy), turned by `angle` (0..255 for a full turn) and scaled
 * by `scale` in 8.8 -- 256 is life size, 512 is double, 128 is half. bg is 2
 * (modes 1 and 2) or 3 (mode 2 only).
 *
 * Stated this way because the registers underneath are stated the other way:
 * they hold the INVERSE transform and the position of texture (0,0), so
 * writing the rotation centre into them makes the picture swing across the
 * screen rather than turn on the spot.
 *
 * rt_obj_affine(group, angle, scale) fills one of the 32 sprite transform
 * groups. Sprites using that group need OBJ_AFFINE in attribute 0, and
 * OBJ_AFFINE_BIG as well if the rotated corners would otherwise be clipped. */
/* The display mode, 0..5. Setting REG_DISPCNT from game code does NOT stick:
 * the frame loop rewrites that register every VBlank, so a mode set directly
 * lasts one frame and then reverts with nothing to say so. Backgrounds are
 * affine only in modes 1 and 2. Their MAP data is a separate matter -- an
 * affine background reads 8-bit tile indices in a layout the room editor does
 * not emit, so mode 1 turns BG2 over whatever already occupies its
 * screenblock. */
void rt_video_mode(int mode);

/* The display mode in effect now, 0..5. */
int  rt_video_mode_get(void);

void rt_bg_affine(int bg, s32 tx, s32 ty, s32 sx, s32 sy,
                  s32 angle, s32 scale);
void rt_obj_affine(int group, s32 angle, s32 scale);

/* ---- bitmap modes 3, 4 and 5 ----------------------------------------------
 * A framebuffer on BG2: a shape that no tile and no sprite holds -- a particle
 * field past what 128 OAM entries can carry, a curve, a plotted graph.
 *
 * rt_bitmap_mode(3|4|5) enters one and turns BG2 on. What each costs:
 *
 *   3   240x160, u16 BGR555 per pixel, ONE page. Every edit is seen being made.
 *   4   240x160, palette index per pixel, TWO pages.
 *   5   160x128, u16 BGR555, TWO pages. THE SIZE IS SMALLER -- code written
 *       for 240x160 runs off each row's right edge and paints a diagonal.
 *
 * `colour` is BGR555 in modes 3 and 5 (RGB15) and a BG palette index 0..255 in
 * mode 4, where the palette is BG_PALETTE and is the game's to fill.
 * Coordinates outside the screen are dropped, and rectangles and blits are
 * clipped to it. `src` for rt_bitmap_blit is w*h pixels in the mode's own size
 * -- u16 in modes 3 and 5, bytes in mode 4.
 *
 * In modes 4 and 5 DRAWING GOES TO THE HIDDEN PAGE, never the one on screen:
 * rt_bitmap_page() says which (0 or 1), and rt_bitmap_flip() presents what was
 * drawn and turns the pair over. Pair it with rt_vsync() for a clean present.
 * Mode 3's buffer is 75 KiB and covers both pages' worth of VRAM, so there is
 * no hidden page and rt_bitmap_flip does nothing there rather than swapping
 * the framebuffer for tile memory.
 *
 * BG0, BG1 and BG3 do not exist in a bitmap mode, so the runtime's own text
 * and dialogue layers are not drawn; and OBJ tiles start at 0x06014000 instead
 * of 0x06010000, so sprite tiles numbered below 512 land in the framebuffer. */
void rt_bitmap_mode(int mode);
/* One pixel. In mode 4 this is a read-modify-write of the halfword holding
   it, because VRAM ignores 8-bit writes -- plotting a byte there loses every
   second pixel. */
void rt_bitmap_pixel(int x, int y, u16 colour);

/* A filled rectangle, clipped to the screen. */
void rt_bitmap_rect(int x, int y, int w, int h, u16 colour);

/* The whole drawing page in one colour. */
void rt_bitmap_clear(u16 colour);
void rt_bitmap_blit(int x, int y, int w, int h, const void *src);
void rt_bitmap_flip(void);
int  rt_bitmap_page(void);

#endif
