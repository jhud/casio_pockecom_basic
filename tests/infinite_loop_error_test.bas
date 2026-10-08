1 "Infinite Loop Error Test"
2 REM This tests the infinite loop guard
3 REM It should trigger after 1000000 steps
4 FOR A = 1 TO 100001
5 GOTO 5
6 NEXT A
8 END