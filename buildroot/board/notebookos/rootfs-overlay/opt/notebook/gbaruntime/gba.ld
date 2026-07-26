OUTPUT_FORMAT("elf32-littlearm")
ENTRY(_start)

MEMORY {
	rom   : ORIGIN = 0x08000000, LENGTH = 32M
	iwram : ORIGIN = 0x03000000, LENGTH = 32K
	ewram : ORIGIN = 0x02000000, LENGTH = 256K
}

SECTIONS {
	.gbaheader : { KEEP(*(.gbaheader)) } > rom

	.text : {
		*(.text*)
		*(.rodata*)
		. = ALIGN(4);
	} > rom

	/* initialised data: lives in ROM, copied to IWRAM by crt0 */
	.data : {
		__data_start = .;
		*(.data*)
		. = ALIGN(4);
		__data_end = .;
	} > iwram AT> rom
	__data_lma = LOADADDR(.data);

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
