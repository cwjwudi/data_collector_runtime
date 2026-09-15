
PROGRAM _INIT
	AlarmDataSQLQuery.Config.PointName[0] := 'AlarmTime';
	AlarmDataSQLQuery.Config.PointName[1] := 'AlarmCode';
	AlarmDataSQLQuery.Config.PointName[2] := 'AlarmState';
	AlarmDataSQLQuery.Config.PointName[3] := 'AlarmText';
	bSimEnable := TRUE;
END_PROGRAM

PROGRAM _CYCLIC
	(*Fill AlarmInsertBuffer arrays (AlarmInsertBuffer_typ), then set InsertTrigger[i]*)
	UtcDTGetTime_0.enable := TRUE;
	UtcDTGetTime_0();
	IF UtcDTGetTime_0.status = 0 THEN
		dtNow := UtcDTGetTime_0.DT1;
	END_IF

	tonAlarm(IN := bSimEnable AND gDataSQLBatchOpen AND NOT tonAlarm.Q, PT := T#12s);

	IF tonAlarm.Q OR bManualTrigger THEN
		IF bManualTrigger THEN
			iEventCount := 1;
			iIdx := UINT_TO_INT(uiManualIndex MOD DATA_SQL_PARALLEL_SIZE);
			bManualTrigger := FALSE;
		ELSE
			iEventCount := DINT_TO_INT(RandRange(1, 3));
			iIdx := DINT_TO_INT(RandRange(0, 99));
		END_IF

		FOR iEvent := 0 TO iEventCount - 1 DO
			usiIdx := INT_TO_USINT((iIdx + (iEvent * 17)) MOD 100);
			IF NOT AlarmInsertBuffer.InsertTrigger[usiIdx] THEN
				diAlarmCode := RandRange(1000, 1049);
				AlarmInsertBuffer.AlarmTime[usiIdx] := dtNow;
				AlarmInsertBuffer.AlarmCode[usiIdx] := DINT_TO_UDINT(diAlarmCode);
				AlarmInsertBuffer.AlarmState[usiIdx] := DINT_TO_UDINT(RandRange(0, 1));
				strTmp := '';
				brsitoa(diAlarmCode, ADR(strTmp));
				AlarmInsertBuffer.AlarmText[usiIdx] := 'PRESS_FAULT_';
				brsstrcat(ADR(AlarmInsertBuffer.AlarmText[usiIdx]), ADR(strTmp));
				AlarmInsertBuffer.AlarmBatch[usiIdx] := gDataSQLBatch.BatchCode;
				AlarmInsertBuffer.InsertTrigger[usiIdx] := TRUE;
			END_IF
		END_FOR
	END_IF
END_PROGRAM

PROGRAM _EXIT

END_PROGRAM
