OUTPUT_FORMAT("elf32-littlearm")
ENTRY(_start)
MEMORY {
	rom   : ORIGIN = 0x08000000, LENGTH = 32M
	iwram : ORIGIN = 0x03000000, LENGTH = 32K
}
SECTIONS {
	.gbaheader : { KEEP(*(.gbaheader)) } > rom
	.text : { *(.text*) *(.rodata*) *(.data*) } > rom
	.bss (NOLOAD) : { *(.bss*) *(COMMON) } > iwram
	/DISCARD/ : { *(.comment) *(.ARM.exidx*) *(.note*) }
}
