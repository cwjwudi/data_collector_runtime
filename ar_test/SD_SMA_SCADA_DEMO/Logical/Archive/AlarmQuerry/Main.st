
PROGRAM _INIT
	(* Insert code here *)
	stDbReadQuery.stConfig.strPointName[0] := 'msg';
	guiDBHeartBeat;

END_PROGRAM

PROGRAM _CYCLIC
	(* Insert code here *)
	//Data GEN - Alarm Group 1 Data Points
	// GET TIME
	UtcDTGetTime_0.enable := TRUE;
	UtcDTGetTime_0();
	IF UtcDTGetTime_0.status = 0 THEN
		ts := DT_TO_STRING(UtcDTGetTime_0.DT1);
	END_IF
	
	Code := RandRange(1,10);  // 报警码
	msg := RandRange(1,10);  // 报警信息
	severity := RandRange(1,4);  // 报警等级 (1=Critical, 2=Major, 3=Minor, 4=Info)
	Status := btrigger;  // 报警状态 (TRUE=Active, FALSE=Cleared)
	Batch ;  // 生产批号
	
	//Triggerrecord
	btrigger;
	
	//Data from DATABASE
	bExecuteRead;
	stDbReadQuery;
	
	//Para and button connect
	stDbReadQuery.stConfig.strStartTime[0] := '';
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR(HMIAlarmQuerrystYear));
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR('-'));
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR(HMIAlarmQuerrystMonth));
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR('-'));
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR(HMIAlarmQuerrystDay));
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR(' '));
	
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR(HMIAlarmQuerrystHour));
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR(':'));
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR(HMIAlarmQuerrystMinute));
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR(':'));
	brsstrcat(ADR(stDbReadQuery.stConfig.strStartTime[0]),ADR(HMIAlarmQuerrystSec));

	
	
	stDbReadQuery.stConfig.strEndTime[0] := '';
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR(HMIAlarmQuerryedYear));
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR('-'));
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR(HMIAlarmQuerryedMonth));
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR('-'));
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR(HMIAlarmQuerryedDay));
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR(' '));
	
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR(HMIAlarmQuerryedHour));
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR(':'));
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR(HMIAlarmQuerryedMinute));
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR(':'));
	brsstrcat(ADR(stDbReadQuery.stConfig.strEndTime[0]),ADR(HMIAlarmQuerryedSec));

	
	
	bExecuteRead := UINT_TO_BOOL(HMIAlarmQuerryMBBtn);

	//Response data
	FOR i := 0 TO MAX_ENTRIES_PERPAGE_MINUSONE DO
		IF stDbReadQuery.stRev[0].udiRevTime[i] <> 0 THEN
			//强制转换
			DT_TO_DTStructure(UDINT_TO_DT(stDbReadQuery.stRev[0].udiRevTime[i]),ADR(stRecvDT[i]));
			
			HMIAlarmQuerryTimestr[i] := '';
				
			brsitoa(UINT_TO_DINT(stRecvDT[i].year),ADR(strempYear));
			brsitoa(UINT_TO_DINT(stRecvDT[i].month),ADR(strempMonth));
			brsitoa(UINT_TO_DINT(stRecvDT[i].day),ADR(strempDay));
			brsitoa(UINT_TO_DINT(stRecvDT[i].hour),ADR(strempHour));
			brsitoa(UINT_TO_DINT(stRecvDT[i].minute),ADR(strempMinute));
			brsitoa(UINT_TO_DINT(stRecvDT[i].second),ADR(strtempSecond));
			
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR(strempYear));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR('-'));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR(strempMonth));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR('-'));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR(strempDay));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR(' '));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR(strempHour));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR(':'));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR(strempMinute));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR(':'));
			brsstrcat(ADR(HMIAlarmQuerryTimestr[i]),ADR(strtempSecond));
		
		ELSE
			brsmemset(ADR(HMIAlarmQuerryTimestr[i]),0,SIZEOF(HMIAlarmQuerryTimestr[i]));
		END_IF
		HMIAlarmQuerryIndex[i] :=  REAL_TO_UINT(stDbReadQuery.stRev[0].rRevBuffer[i]);

	END_FOR

	IF EDGEPOS(UINT_TO_BOOL(HMIAlarmClearMBBtn)) THEN
		brsmemset(ADR(stDbReadQuery.stRev), 0, SIZEOF(stDbReadQuery.stRev));
	END_IF


	
END_PROGRAM

PROGRAM _EXIT
	(* Insert code here *)
	 
END_PROGRAM

