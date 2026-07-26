#!/bin/sh
D="DISPLAY=:0"
click() { DISPLAY=:0 xdotool mousemove "$1" "$2" click 1; sleep 1; }
click 1225 475   # AC (clear)
click 907 609    # 7
click 1225 676   # ×
click 1013 609   # 8
click 1119 810   # =
echo done
