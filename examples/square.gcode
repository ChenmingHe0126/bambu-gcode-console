;===== Example: trace a 60 mm square in the air (dry run - no heating, no filament) =====
; Safe first test: the nozzle stays 10 mm above the bed and nothing is extruded.
; Load this file in the console, look at the preview, then Stream it (or Print now).
G28                       ; home all axes - the printer must know where it is before any move
G90                       ; absolute coordinates: X/Y/Z are positions on the bed, not offsets
G1 Z10 F1200              ; lift to 10 mm (F1200 = 1200 mm/min = 20 mm/s)
G1 X98 Y98 F6000          ; travel to the first corner (the bed is 256 x 256 mm, so this is centred)
G1 X158 Y98 F3000         ; side 1 -> right   (60 mm at 50 mm/s)
G1 X158 Y158              ; side 2 -> back    (F is remembered until you change it)
G1 X98 Y158               ; side 3 -> left
G1 X98 Y98                ; side 4 -> front, square closed
G1 Z30 F1200              ; lift away
G1 X128 Y128 F6000        ; park over the middle of the bed
M400                      ; wait until every move above has finished
; To draw the same square in plastic you would heat up (M104 / M140), lower to Z0.2
; and add an E value to each side - see star.gcode and hello_world.gcode for real prints.
