
PROGRAM _INIT
	(* Insert code here *)
	// 同一次查询，目前最好设定查询时间一致
	stDbReadQuery.stConfig.strPointName[0] := 'rAP1';
	stDbReadQuery.stConfig.strStartTime[0] := '2026-03-04 15:21:32';
	stDbReadQuery.stConfig.strEndTime[0]   := '2026-03-05 15:21:32'; 
	
	stDbReadQuery.stConfig.strPointName[1] := 'rF10';
	stDbReadQuery.stConfig.strStartTime[1] := '2026-03-04 15:21:32';
	stDbReadQuery.stConfig.strEndTime[1]   := '2026-03-04 15:21:32';
	
	stDbReadQuery.stConfig.strPointName[1] := 'rF11';
	stDbReadQuery.stConfig.strStartTime[1] := '2026-03-04 15:21:32';
	stDbReadQuery.stConfig.strEndTime[1]   := '2026-03-04 15:21:32'; 
	
	stDbReadQuery.stConfig.strPointName[1] := 'rF12';
	stDbReadQuery.stConfig.strStartTime[1] := '2026-03-04 15:21:32';
	stDbReadQuery.stConfig.strEndTime[1]   := '2026-03-04 15:21:32'; 
	
END_PROGRAM

PROGRAM _CYCLIC
	(* Insert code here *)
	
		
	guiDQHeartBeat;

END_PROGRAM

PROGRAM _EXIT
	(* Insert code here *)
	 
END_PROGRAM

