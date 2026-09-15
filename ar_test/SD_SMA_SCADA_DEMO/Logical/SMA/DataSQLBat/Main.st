
PROGRAM _INIT
	UtcDTGetTime_0.enable := TRUE;
	UtcDTGetTime_0();
	IF UtcDTGetTime_0.status = 0 THEN
		dtNow := UtcDTGetTime_0.DT1;
	END_IF

	diBatchSeq := DT_TO_DINT(dtNow);
	bSimEnable := TRUE;
	bBatchOpened := FALSE;
	gDataSQLBatchOpen := FALSE;

	(*No phantom open batch after startup. The first open is emitted after the idle timer.*)
	gDataSQLBatch.BatchCode := '';
	gDataSQLBatch.BatchTimeStart := DT#1970-01-01-00:00:00;
	gDataSQLBatch.BatchTimeStop := DT#1970-01-01-00:00:00;

	(*Initial gDataSQLBatchInfo (DataSQLBatchInfo_typ)*)
	gDataSQLBatchInfo.InfoProductName := 'SMA Tablet';
	gDataSQLBatchInfo.InfoProductDesc := 'Production simulation';
	gDataSQLBatchInfo.InfoConfigure := 'CFG_CP1586';
	gDataSQLBatchInfo.InfoRecipe := 'RCP_AUTO';
	gDataSQLBatchInfo.InfoTabletMoldXY := 'ROUND';
	gDataSQLBatchInfo.InfoTabletMoldZ := 'FLAT';
	gDataSQLBatchInfo.InfoBatchStart := dtNow;
	gDataSQLBatchInfo.InfoBatchStop := DT#1970-01-01-00:00:00;

	(*Collector groups are enabled by default; external OPC UA clients may toggle each flag.*)
	gDataSQLOperate.RecipeCollectEnable := TRUE;
	gDataSQLOperate.ProductCollectEnable := TRUE;
	gDataSQLOperate.AlarmCollectEnable := TRUE;
	gDataSQLOperate.AuditCollectEnable := TRUE;
	gDataSQLOperate.BatchCollectEnable := TRUE;
	gDataSQLOperate.BatchInfoCollectEnable := TRUE;
END_PROGRAM

PROGRAM _CYCLIC
	UtcDTGetTime_0.enable := TRUE;
	UtcDTGetTime_0();
	IF UtcDTGetTime_0.status = 0 THEN
		dtNow := UtcDTGetTime_0.DT1;
	END_IF

	(*Shared heartbeat for collector*)
	gDataSQLHeartBeat := gDataSQLHeartBeat + 1;

	(*Automatic cycle: 5 s changeover followed by 90 s production.*)
	tonBatchIdle(IN := bSimEnable AND NOT bBatchOpened AND NOT gDataSQLOperate.BatchInsert, PT := T#5s);
	tonBatchRun(IN := bSimEnable AND bBatchOpened AND NOT gDataSQLOperate.BatchInsert, PT := T#90s);
	IF tonBatchIdle.Q OR (uiManualCommand = 1 AND NOT bBatchOpened AND NOT gDataSQLOperate.BatchInsert) THEN
		uiManualCommand := 0;
		diBatchSeq := DT_TO_DINT(dtNow);
		IF diBatchSeq < 0 THEN
			diBatchSeq := -diBatchSeq;
		END_IF
			strTmp := '';
			brsitoa(diBatchSeq, ADR(strTmp));
			gDataSQLBatch.BatchCode := 'SMA_';
			brsstrcat(ADR(gDataSQLBatch.BatchCode), ADR(strTmp));
			gDataSQLBatch.BatchTimeStart := dtNow;
			gDataSQLBatch.BatchTimeStop := DT#1970-01-01-00:00:00;
			gDataSQLBatchInfo.InfoBatchStart := dtNow;
			gDataSQLBatchInfo.InfoBatchStop := DT#1970-01-01-00:00:00;
			gDataSQLBatchInfo.InfoPieceAccept := 0;
			gDataSQLBatchInfo.InfoPieceDebug := 0;
			gDataSQLBatchInfo.InfoPieceTotal := 0;
			bBatchOpened := TRUE;
			gDataSQLBatchOpen := TRUE;
		gDataSQLOperate.BatchInsert := TRUE;
	ELSIF tonBatchRun.Q OR (uiManualCommand = 2 AND bBatchOpened AND NOT gDataSQLOperate.BatchInsert) THEN
		uiManualCommand := 0;
		gDataSQLBatch.BatchTimeStop := dtNow;
		gDataSQLBatchInfo.InfoBatchStop := dtNow;
		gDataSQLBatchInfo.InfoBatchRun := dtNow - gDataSQLBatchInfo.InfoBatchStart;
		bBatchOpened := FALSE;
		gDataSQLBatchOpen := FALSE;
		gDataSQLOperate.BatchInsert := TRUE;
	END_IF

	(*Monotonic batch counters and measurements correlated with the active recipe.*)
	tonBatchInfo(IN := bSimEnable AND bBatchOpened AND NOT gDataSQLOperate.BatchInfoInsert, PT := T#10s);
	IF EDGEPOS(tonBatchInfo.Q) THEN
		gDataSQLBatchInfo.InfoProductName := 'SMA Tablet';
		gDataSQLBatchInfo.InfoProductDesc := 'Production simulation';
		gDataSQLBatchInfo.InfoConfigure := 'CFG_CP1586';
		gDataSQLBatchInfo.InfoRecipe := 'RCP_AUTO';
		gDataSQLBatchInfo.InfoTabletWeight := gDataSQLProduct.CH01;
		gDataSQLBatchInfo.InfoTabletHardness := gDataSQLProduct.CH02;
		gDataSQLBatchInfo.InfoTabletMoldXY := 'ROUND';
		gDataSQLBatchInfo.InfoTabletDiameterX := gDataSQLProduct.CH04;
		gDataSQLBatchInfo.InfoTabletDiameterY := gDataSQLProduct.CH04 + RandRangeReal(-0.03, 0.03);
		gDataSQLBatchInfo.InfoTabletMoldZ := 'FLAT';
		gDataSQLBatchInfo.InfoTabletThicknessZ := gDataSQLProduct.CH03;
		gDataSQLBatchInfo.InfoTabletRadiusR := gDataSQLProduct.CH04 / 2.0;
		gDataSQLBatchInfo.InfoPieceAccept := gDataSQLBatchInfo.InfoPieceAccept + DINT_TO_UDINT(RandRange(80, 120));
		gDataSQLBatchInfo.InfoPieceDebug := gDataSQLBatchInfo.InfoPieceDebug + DINT_TO_UDINT(RandRange(0, 3));
		gDataSQLBatchInfo.InfoPieceTotal := gDataSQLBatchInfo.InfoPieceAccept + gDataSQLBatchInfo.InfoPieceDebug;
		IF bBatchOpened THEN
			gDataSQLBatchInfo.InfoBatchRun := dtNow - gDataSQLBatchInfo.InfoBatchStart;
		END_IF
		gDataSQLOperate.BatchInfoInsert := TRUE;
	END_IF
END_PROGRAM

PROGRAM _EXIT

END_PROGRAM
