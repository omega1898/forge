--  stall_check AOA AIRSPEED FLAPS(0|1): prints WARN or OK.
--  Implements: HLR-3 (malformed input exits with code 2)
with Ada.Command_Line; use Ada.Command_Line;
with Ada.Text_IO;      use Ada.Text_IO;
with Stall;

procedure Main is
   AoA, Speed : Float;
   Flaps      : Boolean;
begin
   if Argument_Count /= 3 then
      Put_Line (Standard_Error, "malformed: expected AOA AIRSPEED FLAPS");
      Set_Exit_Status (2);
      return;
   end if;
   begin
      AoA := Float'Value (Argument (1));
      Speed := Float'Value (Argument (2));
      if Argument (3) = "1" then
         Flaps := True;
      elsif Argument (3) = "0" then
         Flaps := False;
      else
         raise Constraint_Error;
      end if;
   exception
      when Constraint_Error =>
         Put_Line (Standard_Error, "malformed: not a number or flag");
         Set_Exit_Status (2);
         return;
   end;
   if Stall.Warn (AoA, Speed, Flaps) then
      Put_Line ("WARN");
   else
      Put_Line ("OK");
   end if;
end Main;
