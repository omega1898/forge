--  Stall warning decision logic.
package Stall is
   --  Implements: HLR-1, HLR-2
   function Warn (AoA : Float; Airspeed : Float; Flaps_Extended : Boolean) return Boolean;
end Stall;
