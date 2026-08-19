@ crt0.s — GBA cartridge startup for the Notebook OS GBA SDK runtime.
@ Lays down the ROM header, sets up the IRQ + system stacks, copies .data from
@ ROM into RAM and zeroes .bss (the runtime has mutable globals), then calls main.
	.section .gbaheader, "ax"
	.global _start
	.align
	.arm
_start:
	b	rom_header_end
	.fill	156, 1, 0        @ 0x04: Nintendo logo (0 is fine for the emulator)
	.fill	12, 1, 0         @ 0xA0: game title
	.byte	0,0,0,0          @ 0xAC: game code
	.byte	0,0              @ 0xB0: maker code
	.byte	0x96             @ 0xB2: fixed value
	.byte	0x00             @ 0xB3: main unit code
	.byte	0x00             @ 0xB4: device type
	.fill	7, 1, 0          @ 0xB5: reserved
	.byte	0x00             @ 0xBC: software version
	.byte	0x00             @ 0xBD: complement check (gbafix computes)
	.byte	0,0              @ 0xBE: reserved
rom_header_end:
	@ IRQ mode stack
	mov	r0, #0x12
	msr	cpsr_c, r0
	ldr	sp, =__sp_irq
	@ system mode stack
	mov	r0, #0x1F
	msr	cpsr_c, r0
	ldr	sp, =__sp_sys

	@ copy .data (ROM load addr -> RAM)
	ldr	r0, =__data_lma
	ldr	r1, =__data_start
	ldr	r2, =__data_end
1:	cmp	r1, r2
	ldrlt	r3, [r0], #4
	strlt	r3, [r1], #4
	blt	1b

	@ zero .bss
	ldr	r1, =__bss_start
	ldr	r2, =__bss_end
	mov	r3, #0
2:	cmp	r1, r2
	strlt	r3, [r1], #4
	blt	2b

	@ into C
	ldr	r0, =main
	bx	r0
hang:
	b	hang
