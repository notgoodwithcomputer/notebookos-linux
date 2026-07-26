int main(void){
	*(volatile unsigned int*)0x04000000 = 0x0403;          /* mode3 + BG2 */
	volatile unsigned short* vram = (volatile unsigned short*)0x06000000;
	for (int i = 0; i < 240*160; i++)
		vram[i] = (10) | (15<<5) | (31<<10);           /* light blue */
	while (1) {}
	return 0;
}
