;===== A1 minimal start G-code (classroom quick start) =====
;===== Prerequisites:
;=====   1. Filament already loaded from the printer screen
;=====   2. Bed leveling done at least once via screen Calibration
;=====      (this code reuses the stored mesh, no G29)
G392 S0            ; stock: detection toggle, kept as-is
M975 S1            ; vibration compensation on
G90                ; absolute coordinates
M83                ; RELATIVE extrusion for the purge/prime-line below
M220 S100          ; reset speed override
M221 S100          ; reset flow override

;--- start heating bed and nozzle in parallel ---
M1002 gcode_claim_action : 2
M140 S65           ; <-- CHANGE bed temp here
M104 S140          ; nozzle to 140C only - safe for bed-contact Z homing
M109 S140          ; wait for 140C (if nozzle is still hot from last print, let it cool below 140 first)

;--- home ---
M1002 gcode_claim_action : 13
G28                ; home all axes (A1 homes Z by pressing nozzle on the bed)

;--- short purge into the chute, then wipe ---
M211 X0 Y0 Z0      ; soft endstops off (chute is at negative X)
G1 Z10 F1200
G1 X-28.5 F18000
G1 X-48.2 F3000    ; park over the purge chute
M190 S65           ; <-- CHANGE bed temp here (wait)
M109 S220          ; <-- CHANGE nozzle print temp here (wait)
M106 P1 S178       ; fan on so the purge solidifies and drops off
G1 E10 F200        ; purge 10 mm (relative E)
G1 E-1 F300        ; small retract
M400
G1 X-28.5 F18000   ; wipe and shake
G1 X-48.2 F3000
G1 X-28.5 F18000
M400
M106 P1 S0

;--- prime line along the left edge of the bed ---
G1 X3.0 Y20 F18000 ; travel to start point (still at Z10)
G1 Z0.3 F1200      ; drop to first-layer height
G1 E1 F300         ; undo the retract above
G1 Y180 E10 F1500  ; draw first line  (160 mm needs ~E8, E10 is a bit generous on purpose)
G1 X3.4 F18000     ; shift over 0.4 mm
G1 Y20 E10 F1500   ; draw second line back
G1 Z1 F1200        ; lift off the line
G1 X10 F18000      ; quick move away to break the string

M83                ; RELATIVE extrusion mode - matches the generated print files
G92 E0             ; reset E, print body starts from here
;===== end of start G-code =====

;===== Example: draw a 60 mm square on the bed (one layer, PLA) =====
; What happens, in order:
;   1. A1_start_minimal (above): heat up, home, purge into the chute, draw a prime line on the left edge
;   2. travel to the square's first corner and lower the nozzle to 0.2 mm
;   3. extrude along the four sides. E = side length x 0.037 mm of filament per mm of line
;      (0.037 = 0.45 mm line width x 0.2 mm layer height / 2.405 mm^2 filament cross-section)
;   4. A1_end_minimal (below): heaters off, lift, present the bed, motors off
; The square is 60 x 60 mm centred on the 256 x 256 bed: X98..158, Y98..158
; F1200 = 20 mm/s, slow on purpose so students can watch the nozzle
G90                          ; absolute XY positions
M83                          ; relative extrusion: each E is "how much filament for THIS move"
G0 F18000 X98 Y98 Z1.0       ; travel to the first corner, 1 mm above the bed (no filament)
G1 F1200 Z0.2                ; lower to layer height
G1 F600 E0.8                 ; prime: push 0.8 mm of filament so the line starts right at the corner
G1 F1200 X158 Y98  E2.22 ; side 1 -> right  (60 mm)
G1 F1200 X158 Y158 E2.22 ; side 2 -> back
G1 F1200 X98  Y158 E2.22 ; side 3 -> left
G1 F1200 X98  Y98  E2.22 ; side 4 -> front, square closed
G1 F600 E-0.8                ; retract so the nozzle does not drool while lifting
G0 F1200 Z5                  ; lift away from the print
;===== end of example body =====

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
