OUTPUT_FORMAT("elf32-littlearm")
ENTRY(_start)

MEMORY {
	rom   : ORIGIN = 0x08000000, LENGTH = 32M
	/* 0x03007B00..0x03007EFF is the 1 KiB system stack;
	 * 0x03007F00..0x03007F9F is the IRQ stack. The final 96 bytes are
	 * reserved by the platform. ARM stacks descend, so merely stopping at the
	 * initial SP (0x7F00) would still let .bss occupy live stack memory. */
	iwram : ORIGIN = 0x03000000, LENGTH = 0x7B00
	ewram : ORIGIN = 0x02000000, LENGTH = 256K
}

__sp_sys = 0x03007F00;
__sp_irq = 0x03007FA0;
ASSERT(__bss_end <= ORIGIN(iwram) + LENGTH(iwram),
       "IWRAM data overlaps the reserved stacks")

SECTIONS {
	.gbaheader : { KEEP(*(.gbaheader)) } > rom

	.text : {
		*(.text*)
		*(.rodata*)
		. = ALIGN(4);
	} > rom

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
	} > iwram AT> rom

	/* initialised data: lives in ROM, copied to IWRAM by crt0 */
	.data : {
		*(.data*)
		. = ALIGN(4);
		__data_end = .;
	} > iwram AT> rom
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
