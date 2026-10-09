;===== A1 minimal end G-code =====
M400               ; wait for all moves to finish
M83
G92 E0
G1 E-2 F300        ; retract to reduce oozing
M104 S0            ; nozzle heater off
M140 S0            ; bed heater off
M106 P1 S0         ; part fan off
G91
G1 Z10 F1200       ; lift nozzle 10 mm (reduce if the print is near max height 256)
G90
G1 X128 F18000     ; move head to the middle
G1 Y250 F3000      ; slide bed forward to present the print
M400
M18 X Y Z          ; motors off
;===== end =====
