/* gba_mb.ld -- linker script for a MULTIBOOT image.
 *
 * A multiboot image is sent over the link cable to a console with no cartridge
 * and runs from EWRAM, so every section is placed at 0x02000000 rather than
 * 0x08000000. It is otherwise the same program: this file is gba.ld with the
 * ROM region swapped for EWRAM, and nothing else, so the two cannot drift into
 * behaving differently.
 *
 * EWRAM is 256 KB and the loader will not send more, which is the limit the
 * builder checks before it wastes anyone's time.
 */
OUTPUT_FORMAT("elf32-littlearm")
ENTRY(_start)

MEMORY {
	rom   : ORIGIN = 0x08000000, LENGTH = 32M
	iwram : ORIGIN = 0x03000000, LENGTH = 32K
	ewram : ORIGIN = 0x02000000, LENGTH = 256K
}

SECTIONS {
	.gbaheader : { KEEP(*(.gbaheader)) } > ewram

	.text : {
		*(.text*)
		*(.rodata*)
		. = ALIGN(4);
	} > ewram

	/* CODE that runs from IWRAM, copied there by crt0 along with .data.
	 *
	 * ROM is on a 16-bit bus with wait states; IWRAM is 32-bit with none. A
	 * function called thousands of times a frame -- the collision test, the
	 * per-instance move -- runs appreciably faster from here, and there are
	 * 32 KB of it shared with every variable in the game, so this is for the
	 * few functions that earn it rather than for the runtime at large.
	 *
	 * It shares a load group with .data so crt0's existing copy loop moves
	 * both. A second region would need a second loop, and a section that is
	 * placed but never copied is a jump into whatever IWRAM happened to
	 * hold. */
	.iwram : {
		__data_start = .;
		*(.iwram*)
		. = ALIGN(4);
	} > iwram AT> ewram

	/* initialised data: lives in ROM, copied to IWRAM by crt0 */
	.data : {
		*(.data*)
		. = ALIGN(4);
		__data_end = .;
	} > iwram AT> ewram
	__data_lma = LOADADDR(.iwram);

	/* zero-initialised data: IWRAM, cleared by crt0 */
	.bss (NOLOAD) : {
		__bss_start = .;
		*(.bss*)
		*(COMMON)
		. = ALIGN(4);
		__bss_end = .;
	} > iwram

	/DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.note*) *(.init_array*) }
}
