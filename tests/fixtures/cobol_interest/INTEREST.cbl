      * Interest per account: AMOUNT x RATE, COBOL ROUNDED to cents.
      * Input: line sequential records (ID, signed amount, rate).
      * Output: ID,INTEREST per line. The legacy reference for F20.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. INTEREST.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT ACCOUNTS ASSIGN TO DYNAMIC WS-PATH
               ORGANIZATION IS LINE SEQUENTIAL
               FILE STATUS IS WS-STATUS.
       DATA DIVISION.
       FILE SECTION.
       FD  ACCOUNTS.
       01  ACCOUNT-REC.
           05  ACCT-ID          PIC X(6).
           05  AMOUNT           PIC S9(9)V999 SIGN LEADING SEPARATE.
           05  RATE             PIC 9V9999.
       WORKING-STORAGE SECTION.
       01  WS-PATH              PIC X(256).
       01  WS-STATUS            PIC XX.
       01  WS-EOF               PIC X VALUE "N".
       01  WS-INTEREST          PIC S9(9)V99.
       01  WS-EDITED            PIC -(9)9.99.
       PROCEDURE DIVISION.
           ACCEPT WS-PATH FROM ARGUMENT-VALUE
           OPEN INPUT ACCOUNTS
           IF WS-STATUS NOT = "00"
               DISPLAY "cannot open input " WS-STATUS UPON SYSERR
               MOVE 66 TO RETURN-CODE
               STOP RUN
           END-IF
           PERFORM UNTIL WS-EOF = "Y"
               READ ACCOUNTS
                   AT END MOVE "Y" TO WS-EOF
                   NOT AT END
                       COMPUTE WS-INTEREST ROUNDED = AMOUNT * RATE
                       MOVE WS-INTEREST TO WS-EDITED
                       DISPLAY FUNCTION TRIM(ACCT-ID) ","
                           FUNCTION TRIM(WS-EDITED)
               END-READ
           END-PERFORM
           CLOSE ACCOUNTS
           STOP RUN.
