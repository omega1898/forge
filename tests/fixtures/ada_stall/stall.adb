package body Stall is
   --  Implements: HLR-1, HLR-2
   function Warn (AoA : Float; Airspeed : Float; Flaps_Extended : Boolean) return Boolean is
   begin
      return (AoA > 15.0 and then Airspeed < 120.0)
        or else (Flaps_Extended and then AoA > 12.0);
   end Warn;
end Stall;
