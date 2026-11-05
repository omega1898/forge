! limiter VALUE LOW HIGH: prints IN when LOW <= VALUE <= HIGH, else OUT.
! Implements: LIM-1 (band check), LIM-2 (malformed input exits with code 2)
program limiter
  implicit none
  character(len=64) :: text
  real :: value, low, high
  integer :: status

  if (command_argument_count() /= 3) then
    write (0, '(A)') 'usage: limiter VALUE LOW HIGH'
    stop 2
  end if
  call get_command_argument(1, text)
  read (text, *, iostat=status) value
  if (status /= 0) stop 2
  call get_command_argument(2, text)
  read (text, *, iostat=status) low
  if (status /= 0) stop 2
  call get_command_argument(3, text)
  read (text, *, iostat=status) high
  if (status /= 0) stop 2

  if (value < low .or. value > high) then
    print '(A)', 'OUT'
  else
    print '(A)', 'IN'
  end if
end program limiter
