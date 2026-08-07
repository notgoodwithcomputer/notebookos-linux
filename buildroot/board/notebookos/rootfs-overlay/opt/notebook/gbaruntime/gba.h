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

/* ---- interrupts ---------------------------------------------------------
 * The runtime had none at all: it spun on REG_VCOUNT to find VBlank, which
 * burns the CPU through the whole wait and — the reason this matters — makes
 * everything interrupt-driven impossible. Timer IRQs are how DMA sound is
 * clocked, HBlank IRQs are how per-scanline effects are done, and SIO needs
 * one to be usable at all. See Phase 6 of docs/GBA-SDK-SPEC.md.
 *
 * The BIOS reads the handler address out of 0x03007FFC and jumps there in ARM
 * state with IRQs disabled. It also keeps its own pending-flag word at
 * 0x03007FF8, which SWI VBlankIntrWait consults — acknowledging REG_IF alone
 * leaves that call waiting for ever. */
#define REG_IE       (*(volatile u16*)0x04000200)
#define REG_IF       (*(volatile u16*)0x04000202)
#define REG_IME      (*(volatile u16*)0x04000208)
#define BIOS_IF      (*(volatile u16*)0x03007FF8)
#define BIOS_IRQ_VEC (*(volatile u32*)0x03007FFC)

#define IRQ_VBLANK   0x0001
#define IRQ_HBLANK   0x0002
#define IRQ_VCOUNT   0x0004
#define IRQ_TIMER0   0x0008
#define IRQ_TIMER1   0x0010
#define IRQ_TIMER2   0x0020
#define IRQ_TIMER3   0x0040
#define IRQ_SERIAL   0x0080
#define IRQ_DMA0     0x0100
#define IRQ_DMA1     0x0200
#define IRQ_DMA2     0x0400
#define IRQ_DMA3     0x0800
#define IRQ_KEYPAD   0x1000
#define IRQ_GAMEPAK  0x2000
#define RT_IRQ_SLOTS 14

/* DISPSTAT bits that arm the display interrupts */
#define DSTAT_VBLANK_IRQ 0x0008
#define DSTAT_HBLANK_IRQ 0x0010
#define DSTAT_VCOUNT_IRQ 0x0020

/* ---- timers -------------------------------------------------------------
 * Four 16-bit up-counters. TM_FREQ_1 counts every cycle (16.78 MHz); the
 * others prescale. CASCADE makes a timer tick when the one below it overflows,
 * which is how a 32-bit count is built out of two 16-bit timers. */
#define REG_TM0CNT_L (*(volatile u16*)0x04000100)
#define REG_TM0CNT_H (*(volatile u16*)0x04000102)
#define REG_TM1CNT_L (*(volatile u16*)0x04000104)
#define REG_TM1CNT_H (*(volatile u16*)0x04000106)
#define REG_TM2CNT_L (*(volatile u16*)0x04000108)
#define REG_TM2CNT_H (*(volatile u16*)0x0400010A)
#define REG_TM3CNT_L (*(volatile u16*)0x0400010C)
#define REG_TM3CNT_H (*(volatile u16*)0x0400010E)
#define TM_FREQ_1     0x0000
#define TM_FREQ_64    0x0001
#define TM_FREQ_256   0x0002
#define TM_FREQ_1024  0x0003
#define TM_CASCADE    0x0004
#define TM_IRQ        0x0040
#define TM_ENABLE     0x0080
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

/* BLDCNT in full. The register names two SETS of layers -- what is blended
   (1st target, low bits) and what it is blended WITH (2nd target, high bits) --
   and a mode. Alpha needs both sets; the two brightness modes need only the
   first. Naming only BLD_ALL meant a fade could be written and nothing else. */
#define BLD_A_BG0    0x0001
#define BLD_A_BG1    0x0002
#define BLD_A_BG2    0x0004
#define BLD_A_BG3    0x0008
#define BLD_A_OBJ    0x0010
#define BLD_A_BD     0x0020   /* the backdrop: colour 0 of palette 0 */
#define BLD_B_BG0    0x0100
#define BLD_B_BG1    0x0200
#define BLD_B_BG2    0x0400
#define BLD_B_BG3    0x0800
#define BLD_B_OBJ    0x1000
#define BLD_B_BD     0x2000
#define BLD_B_ALL    0x3F00
#define BLD_OFF      0x0000
#define BLD_ALPHA    0x0040   /* mode 1: 1st and 2nd target mixed */

/* ---- windows ------------------------------------------------------------
 * Two rectangles and an OBJ-shaped one, each choosing which layers are drawn
 * inside it and which outside. This is how a spotlight, a status bar that
 * hides the map behind it, or a dialogue box that dims the world is done --
 * none of which is a drawing operation at all.
 *
 * H and V registers pack the two edges into one halfword: (left << 8) | right.
 * The right and bottom edges are EXCLUSIVE. A right edge of 0, or one past the
 * left edge, is treated as 240/160 by the hardware -- so an "empty" window is
 * a full-screen one, which is not what an author who set width 0 expects. */
#define REG_WIN0H    (*(volatile u16*)0x04000040)
#define REG_WIN1H    (*(volatile u16*)0x04000042)
#define REG_WIN0V    (*(volatile u16*)0x04000044)
#define REG_WIN1V    (*(volatile u16*)0x04000046)
#define REG_WININ    (*(volatile u16*)0x04000048)
#define REG_WINOUT   (*(volatile u16*)0x0400004A)
#define WIN0_ON      0x2000   /* in DISPCNT */
#define WIN1_ON      0x4000
#define WINOBJ_ON    0x8000
#define WIN_BG0      0x0001   /* the layer bits, used in WININ and WINOUT */
#define WIN_BG1      0x0002
#define WIN_BG2      0x0004
#define WIN_BG3      0x0008
#define WIN_OBJ      0x0010
#define WIN_BLEND    0x0020   /* colour effects apply in this region */
#define WIN_ALL      0x003F
#define WIN_NONE     0x0000

/* ---- mosaic -------------------------------------------------------------
 * Enlarges pixels in blocks. A cheap dissolve, a pixelate-in transition, or a
 * damage flash. It applies only to layers with the mosaic bit set in their own
 * control register, which is the part usually missed: writing REG_MOSAIC alone
 * does nothing visible. */
#define REG_MOSAIC   (*(volatile u16*)0x0400004C)
#define BGCNT_MOSAIC 0x0040
#define OBJ_MOSAIC   0x1000   /* in an OAM attribute 0 */

/* ---- affine backgrounds -------------------------------------------------
 * BG2 (modes 1 and 2) and BG3 (mode 2) can rotate and scale. The four P
 * registers are a 2x2 matrix in 8.8 fixed point that maps SCREEN space to
 * TEXTURE space -- the inverse of the transform being pictured, which is why
 * scaling up means dividing. The X/Y registers are 20.8 fixed point and hold
 * where texture (0,0) lands, not the centre of anything.
 *
 * Setting these by hand is the classic place a rotation drifts across the
 * screen instead of turning on the spot; rt_bg_affine does the arithmetic. */
#define REG_BG2PA    (*(volatile s16*)0x04000020)
#define REG_BG2PB    (*(volatile s16*)0x04000022)
#define REG_BG2PC    (*(volatile s16*)0x04000024)
#define REG_BG2PD    (*(volatile s16*)0x04000026)
#define REG_BG2X     (*(volatile s32*)0x04000028)
#define REG_BG2Y     (*(volatile s32*)0x0400002C)
#define REG_BG3PA    (*(volatile s16*)0x04000030)
#define REG_BG3PB    (*(volatile s16*)0x04000032)
#define REG_BG3PC    (*(volatile s16*)0x04000034)
#define REG_BG3PD    (*(volatile s16*)0x04000036)
#define REG_BG3X     (*(volatile s32*)0x04000038)
#define REG_BG3Y     (*(volatile s32*)0x0400003C)
#define MODE_1       0x0001   /* BG0, BG1 tiled + BG2 affine */
#define MODE_2       0x0002   /* BG2 and BG3 both affine */

/* ---- affine sprites -----------------------------------------------------
 * 32 transformation groups, interleaved with the OAM entries rather than
 * stored in a table of their own: group g's four parameters sit in the unused
 * fourth halfword of OAM entries 4g..4g+3. Many sprites can share one group,
 * which is what makes rotating a whole formation cost four writes. */
#define OAM_AFF_PA(g) (*(volatile s16*)(0x07000006 + (g) * 32))
#define OAM_AFF_PB(g) (*(volatile s16*)(0x0700000E + (g) * 32))
#define OAM_AFF_PC(g) (*(volatile s16*)(0x07000016 + (g) * 32))
#define OAM_AFF_PD(g) (*(volatile s16*)(0x0700001E + (g) * 32))
#define OBJ_AFFINE       0x0100   /* attribute 0: use a transform group */
#define OBJ_AFFINE_BIG   0x0200   /* with it: double the drawn area */

/* DMA channel 3 — general-purpose memory copy (OAM flush, VRAM uploads) */
#define REG_DMA3SAD (*(volatile u32*)0x040000D4)
#define REG_DMA3DAD (*(volatile u32*)0x040000D8)
#define REG_DMA3CNT (*(volatile u32*)0x040000DC)
#define DMA_ENABLE  0x80000000
#define DMA_32      0x04000000

/* ---- DMA 0-2 ------------------------------------------------------------
 * Only channel 3 existed, which is the general-purpose one. The other three
 * are not spares:
 *   DMA0  highest priority, internal memory only — time-critical work
 *   DMA1/2 the SOUND channels: with DMA_SPECIAL they refill the FIFOs on a
 *         timer request, which is the only way to play PCM audio
 *   DMA3  general memory, and the only one that can write to ROM-side targets
 * DMA_HBLANK repeats the transfer at every HBlank, which is how a per-scanline
 * effect is done — a table of 160 scroll or palette values walked one line at a
 * time, with no CPU involvement at all. */
#define REG_DMA0SAD (*(volatile u32*)0x040000B0)
#define REG_DMA0DAD (*(volatile u32*)0x040000B4)
#define REG_DMA0CNT (*(volatile u32*)0x040000B8)
#define REG_DMA1SAD (*(volatile u32*)0x040000BC)
#define REG_DMA1DAD (*(volatile u32*)0x040000C0)
#define REG_DMA1CNT (*(volatile u32*)0x040000C4)
#define REG_DMA2SAD (*(volatile u32*)0x040000C8)
#define REG_DMA2DAD (*(volatile u32*)0x040000CC)
#define REG_DMA2CNT (*(volatile u32*)0x040000D0)
#define DMA_16          0x00000000
#define DMA_REPEAT      0x02000000
#define DMA_SRC_INC     0x00000000
#define DMA_SRC_DEC     0x00800000
#define DMA_SRC_FIX     0x01000000
#define DMA_DST_INC     0x00000000
#define DMA_DST_DEC     0x00200000
#define DMA_DST_FIX     0x00400000
#define DMA_DST_RELOAD  0x00600000
#define DMA_NOW         0x00000000
#define DMA_VBLANK      0x10000000
#define DMA_HBLANK      0x20000000
#define DMA_SPECIAL     0x30000000   /* sound FIFO / video capture */
#define DMA_IRQ         0x40000000

/* ---- sound FIFOs (the PCM side) ----------------------------------------- */
#define REG_FIFO_A  (*(volatile u32*)0x040000A0)
#define REG_FIFO_B  (*(volatile u32*)0x040000A4)

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
#define REG_SOUNDBIAS   (*(volatile u16*)0x04000088)

/* ---- Direct Sound (the PCM half of SOUNDCNT_H) --------------------------
 * The two DMA channels play sampled audio: a timer requests a FIFO refill at
 * the sample rate, DMA delivers four samples per request, and the CPU does
 * nothing at all. Samples are SIGNED 8-bit; feeding unsigned data plays a
 * loud buzz at the right pitch, which sounds like a broken sample rather than
 * a sign error.
 *
 * The timer bit is the one worth choosing deliberately: Direct Sound A can be
 * clocked by timer 0 or timer 1, and taking timer 0 would collide with the
 * one the Help's own interrupt example uses. */
#define DSA_VOL_HALF    0x0000
#define DSA_VOL_FULL    0x0004
#define DSB_VOL_HALF    0x0000
#define DSB_VOL_FULL    0x0008
#define DSA_RIGHT       0x0100
#define DSA_LEFT        0x0200
#define DSA_TIMER0      0x0000
#define DSA_TIMER1      0x0400
#define DSA_RESET       0x0800   /* clear the FIFO before starting */
#define DSB_RIGHT       0x1000
#define DSB_LEFT        0x2000
#define DSB_TIMER0      0x0000
#define DSB_TIMER1      0x4000
#define DSB_RESET       0x8000
#define PSG_VOL_FULL    0x0002

/* ---- serial: the link cable ---------------------------------------------
 * Multiplayer mode connects two to four units. One transfer moves ONE halfword
 * from every unit to every unit, including back to the sender -- so after a
 * transfer all four SIOMULTI registers hold the four units' words, and which
 * one is yours is read from the ID field.
 *
 * TWO REGISTERS SELECT THE MODE AND BOTH MATTER. RCNT bits 15-14 choose
 * between SIO, GPIO and JOY bus; only then do SIOCNT bits 13-12 choose which
 * SIO mode. Setting SIOCNT alone leaves the port in whatever RCNT last said,
 * which on a cartridge with a real-time clock is GPIO -- so the link silently
 * does nothing and the clock stops answering. */
#define REG_SIOMULTI0   (*(volatile u16*)0x04000120)
#define REG_SIOMULTI1   (*(volatile u16*)0x04000122)
#define REG_SIOMULTI2   (*(volatile u16*)0x04000124)
#define REG_SIOMULTI3   (*(volatile u16*)0x04000126)
#define REG_SIOCNT      (*(volatile u16*)0x04000128)
#define REG_SIOMLT_SEND (*(volatile u16*)0x0400012A)
#define REG_RCNT        (*(volatile u16*)0x04000134)

#define RCNT_SIO        0x0000   /* bits 15-14 = 00: SIOCNT picks the mode */
#define RCNT_GPIO       0x8000   /* what an RTC cartridge uses */
#define SIO_MULTI       0x2000   /* SIOCNT bits 13-12 = 01 */
#define SIO_NORMAL8     0x0000
#define SIO_UART        0x3000
#define SIO_9600        0x0000
#define SIO_38400       0x0001
#define SIO_57600       0x0002
#define SIO_115200      0x0003
#define SIO_SI_CHILD    0x0004   /* read-only: 0 = parent, 1 = child */
#define SIO_SD_READY    0x0008   /* read-only: every unit is connected */
#define SIO_ID_MASK     0x0030   /* read-only: this unit's 0..3 */
#define SIO_ID_SHIFT    4
#define SIO_ERROR       0x0040
#define SIO_START       0x0080   /* parent sets it; clears on all units when done */
#define SIO_IRQ         0x4000

/* ---- cartridge GPIO and the real-time clock -----------------------------
 * The RTC is a Seiko S-3511A ON THE CARTRIDGE, not in the console. Three GPIO
 * pins at the top of the ROM area are bit-banged to talk to it: a clock, a
 * bidirectional data line and a chip select.
 *
 * A cartridge without the chip answers with whatever the bus floats to, which
 * is why rt_rtc_read validates what comes back rather than trusting it. */
#define REG_GPIO_DATA   (*(volatile u16*)0x080000C4)
#define REG_GPIO_DIR    (*(volatile u16*)0x080000C6)
#define REG_GPIO_CTRL   (*(volatile u16*)0x080000C8)
#define GPIO_SCK        0x0001
#define GPIO_SIO        0x0002
#define GPIO_CS         0x0004
#define GPIO_READABLE   0x0001   /* in CTRL: without it the pins read as 0 */
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
