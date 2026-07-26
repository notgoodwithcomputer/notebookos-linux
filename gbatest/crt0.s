	.section .gbaheader, "ax"
	.global _start
	.align
	.arm
_start:
	b	rom_header_end
	.fill	156, 1, 0        @ Nintendo logo (0 = fine for emulator, gbafix leaves it)
	.fill	12, 1, 0         @ game title
	.byte	0,0,0,0          @ game code
	.byte	0,0              @ maker code
	.byte	0x96             @ fixed value
	.byte	0x00             @ main unit code
	.byte	0x00             @ device type
	.fill	7, 1, 0          @ reserved
	.byte	0x00             @ software version
	.byte	0x00             @ complement check (gbafix computes)
	.byte	0,0              @ reserved
rom_header_end:
	mov	r0, #0x12        @ IRQ mode
	msr	cpsr_c, r0
	ldr	sp, =0x03007FA0
	mov	r0, #0x1F        @ system mode
	msr	cpsr_c, r0
	ldr	sp, =0x03007F00
	ldr	r0, =main
	bx	r0
hang:
	b	hang
