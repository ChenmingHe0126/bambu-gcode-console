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
