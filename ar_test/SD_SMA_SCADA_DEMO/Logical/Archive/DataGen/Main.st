
PROGRAM _INIT
	(* Insert code here *)
	UtcDTGetTime_0.enable := TRUE;
	UtcDTGetTime_0();
	IF UtcDTGetTime_0.status = 0 THEN
		dtBtachStartTime := UtcDTGetTime_0.DT1;
		dtBtachEndTime := UtcDTGetTime_0.DT1;
	END_IF
	strBatchCode := 'SCP173';
	bBatchTriger := FALSE;
END_PROGRAM

PROGRAM _CYCLIC
	(* Insert code here *)
	strTimeStamp;
	strTimeStamp := DT_TO_STRING(dtTimeStamp);
	IP1 := RandINT();
	IP2 := RandINT();

	FP1 := RandRangeReal(0, 100);
	FP2 := RandRangeReal(0, 100);

	CV := RandRangeReal(0, 100);
	VPF := RandRangeReal(0, 100);
	WT := RandRangeReal(0, 100);

	F1 := RandRangeReal(0, 100);
	F2 := RandRangeReal(0, 100);
	F3 := RandRangeReal(0, 100);
	F4 := RandRangeReal(0, 100);
	F5 := RandRangeReal(0, 100);
	F6 := RandRangeReal(0, 100);
	F7 := RandRangeReal(0, 100);
	F8 := RandRangeReal(0, 100);
	F9 := RandRangeReal(0, 100);
	F10 := RandRangeReal(0, 100);
	F11 := RandRangeReal(0, 100);
	F12 := RandRangeReal(0, 100);
	F13 := RandRangeReal(0, 100);
	F14 := RandRangeReal(0, 100);
	F15 := RandRangeReal(0, 100);

	FP := RandRangeReal(0, 100);

	AP1 := RandRangeReal(0, 100);
	BP1 := RandRangeReal(0, 100);
	AP2 := RandRangeReal(0, 100);

	ST1 := RandRangeReal(0, 100);
	ST2 := RandRangeReal(0, 100);
	ST3 := RandRangeReal(0, 100);
	ST4 := RandRangeReal(0, 100);
	ST5 := RandRangeReal(0, 100);
	ST6 := RandRangeReal(0, 100);
	ST7 := RandRangeReal(0, 100);
	ST8 := RandRangeReal(0, 100);
	ST9 := RandRangeReal(0, 100);
	ST10 := RandRangeReal(0, 100);
	ST11 := RandRangeReal(0, 100);
	ST12 := RandRangeReal(0, 100);
	ST13 := RandRangeReal(0, 100);
	ST14 := RandRangeReal(0, 100);
	ST15 := RandRangeReal(0, 100);

	AIR := RandRangeReal(0, 100);
	CP := RandRangeReal(0, 100);
	CT := RandRangeReal(0, 100);
	CRT := RandRangeReal(0, 100);
	ES := RandRangeReal(0, 100);
	FT := RandRangeReal(0, 100);
	FF := RandRangeReal(0, 100);

	FC := RandINT();

	HT1 := RandRangeReal(0, 100);
	HT2 := RandRangeReal(0, 100);
	HT3 := RandRangeReal(0, 100);
	HT4 := RandRangeReal(0, 100);
	HT5 := RandRangeReal(0, 100);
	HT6 := RandRangeReal(0, 100);
	HT7 := RandRangeReal(0, 100);
	HT8 := RandRangeReal(0, 100);
	HT9 := RandRangeReal(0, 100);
	HT10 := RandRangeReal(0, 100);
	HT11 := RandRangeReal(0, 100);
	HT12 := RandRangeReal(0, 100);
	HT13 := RandRangeReal(0, 100);
	HT14 := RandRangeReal(0, 100);
	HT15 := RandRangeReal(0, 100);
	HT16 := RandRangeReal(0, 100);
	HT17 := RandRangeReal(0, 100);
	HT18 := RandRangeReal(0, 100);
	HT19 := RandRangeReal(0, 100);
	HT20 := RandRangeReal(0, 100);

	MP := RandRangeReal(0, 100);
	MT := RandRangeReal(0, 100);
	MCT := RandRangeReal(0, 100);
	MCP := RandRangeReal(0, 100);
	EC := RandRangeReal(0, 100);
	EST := RandRangeReal(0, 100);
	ES1 := RandRangeReal(0, 100);
	TC := RandRangeReal(0, 100);
	TF := RandRangeReal(0, 100);
	TW := RandRangeReal(0, 100);	 
END_PROGRAM

PROGRAM _EXIT
	(* Insert code here *)
	 
END_PROGRAM
