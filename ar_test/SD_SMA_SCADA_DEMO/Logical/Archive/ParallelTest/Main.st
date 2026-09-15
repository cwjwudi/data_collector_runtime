
PROGRAM _INIT
	(* Insert code here *)
	 
END_PROGRAM

PROGRAM _CYCLIC
	(* Insert code here *)
	IF usiTestModel = 10 THEN
		usiTestModel := 0;
		
		usiIdx := 0;
		bParallelTriger[usiIdx] := TRUE;
		F1[usiIdx] := usiIdx + 1;
		F2[usiIdx] := usiIdx + 2;
		F3[usiIdx] := usiIdx + 3;
		F4[usiIdx] := usiIdx + 4;
		F5[usiIdx] := usiIdx + 5;
		
		usiIdx := 10;
		bParallelTriger[usiIdx] := TRUE;
		F1[usiIdx] := usiIdx + 1;
		F2[usiIdx] := usiIdx + 2;
		F3[usiIdx] := usiIdx + 3;
		F4[usiIdx] := usiIdx + 4;
		F5[usiIdx] := usiIdx + 5;
		
		usiIdx := 49;
		bParallelTriger[usiIdx] := TRUE;
		F1[usiIdx] := usiIdx + 1;
		F2[usiIdx] := usiIdx + 2;
		F3[usiIdx] := usiIdx + 3;
		F4[usiIdx] := usiIdx + 4;
		F5[usiIdx] := usiIdx + 5;
	END_IF
	
END_PROGRAM

PROGRAM _EXIT
	(* Insert code here *)
	 
END_PROGRAM

