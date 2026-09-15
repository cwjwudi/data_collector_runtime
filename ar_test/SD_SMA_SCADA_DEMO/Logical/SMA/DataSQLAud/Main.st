
PROGRAM _INIT
	AuditDataSQLQuery.Config.PointName[0] := 'AuditTime';
	AuditDataSQLQuery.Config.PointName[1] := 'AuditOperator';
	AuditDataSQLQuery.Config.PointName[2] := 'AuditText';
	bSimEnable := TRUE;
END_PROGRAM

PROGRAM _CYCLIC
	(*Fill AuditInsertBuffer arrays (AuditInsertBuffer_typ), then set InsertTrigger[i]*)
	UtcDTGetTime_0.enable := TRUE;
	UtcDTGetTime_0();
	IF UtcDTGetTime_0.status = 0 THEN
		dtNow := UtcDTGetTime_0.DT1;
	END_IF

	tonAudit(IN := bSimEnable AND gDataSQLBatchOpen AND NOT tonAudit.Q, PT := T#15s);

	IF tonAudit.Q OR bManualTrigger THEN
		IF bManualTrigger THEN
			iEventCount := 1;
			iIdx := UINT_TO_INT(uiManualIndex MOD DATA_SQL_PARALLEL_SIZE);
			bManualTrigger := FALSE;
		ELSE
			iEventCount := DINT_TO_INT(RandRange(1, 3));
			iIdx := DINT_TO_INT(RandRange(0, 99));
		END_IF

		FOR iEvent := 0 TO iEventCount - 1 DO
			usiIdx := INT_TO_USINT((iIdx + (iEvent * 23)) MOD 100);
			IF NOT AuditInsertBuffer.InsertTrigger[usiIdx] THEN
				diAuditIdx := RandRange(1, 12);
				strTmp := '';
				brsitoa(diAuditIdx, ADR(strTmp));
				AuditInsertBuffer.AuditTime[usiIdx] := dtNow;
				AuditInsertBuffer.AuditOperator[usiIdx] := 'Operator';
				brsstrcat(ADR(AuditInsertBuffer.AuditOperator[usiIdx]), ADR(strTmp));
				AuditInsertBuffer.AuditText[usiIdx] := 'PARAMETER_CHANGE_';
				brsstrcat(ADR(AuditInsertBuffer.AuditText[usiIdx]), ADR(strTmp));
				AuditInsertBuffer.AuditBatch[usiIdx] := gDataSQLBatch.BatchCode;
				AuditInsertBuffer.InsertTrigger[usiIdx] := TRUE;
			END_IF
		END_FOR
	END_IF
END_PROGRAM

PROGRAM _EXIT

END_PROGRAM
