(* Run-length encoding. "encode" turns "aaab" into "3a1b"; "decode" reverses it.
   Digits cannot be encoded (they would be read as counts). Malformed input
   exits with code 2 and a message on stderr; usage errors exit 64. *)

exception Malformed of string

let encode s =
  String.iter (fun c -> if c >= '0' && c <= '9' then raise (Malformed "digits cannot be encoded")) s;
  let buffer = Buffer.create (String.length s) in
  let n = String.length s in
  let i = ref 0 in
  while !i < n do
    let c = s.[!i] in
    let j = ref !i in
    while !j < n && s.[!j] = c do incr j done;
    Buffer.add_string buffer (string_of_int (!j - !i));
    Buffer.add_char buffer c;
    i := !j
  done;
  Buffer.contents buffer

let decode s =
  let buffer = Buffer.create (2 * String.length s) in
  let n = String.length s in
  let i = ref 0 in
  while !i < n do
    let start = !i in
    while !i < n && s.[!i] >= '0' && s.[!i] <= '9' do incr i done;
    if !i = start then raise (Malformed "missing count");
    if !i >= n then raise (Malformed "count without a character");
    let count =
      match int_of_string_opt (String.sub s start (!i - start)) with
      | Some c when c > 0 && c <= 1_000_000 -> c
      | Some 0 -> raise (Malformed "zero count")
      | _ -> raise (Malformed "count too large")
    in
    Buffer.add_string buffer (String.make count s.[!i]);
    incr i
  done;
  Buffer.contents buffer

let () =
  let mode = if Array.length Sys.argv = 2 then Sys.argv.(1) else "" in
  let input = In_channel.input_all stdin in
  match mode with
  | "encode" | "decode" -> (
      try print_string (if mode = "encode" then encode input else decode input)
      with Malformed reason -> prerr_endline ("malformed: " ^ reason); exit 2)
  | _ -> prerr_endline "usage: rle encode|decode < input"; exit 64
