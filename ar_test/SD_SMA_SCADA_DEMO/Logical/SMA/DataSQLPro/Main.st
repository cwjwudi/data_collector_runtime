
PROGRAM _INIT
	ProductDataSQLQuery.Config.PointName[0] := 'DataProductTime';
	ProductDataSQLQuery.Config.PointName[1] := 'DataProductState';
	ProductDataSQLQuery.Config.PointName[2] := 'DataProductCH01';
	ProductDataSQLQuery.Config.PointName[3] := 'DataProductCH02';
	ProductDataSQLQuery.Config.PointName[4] := 'DataProductCH03';
	ProductDataSQLQuery.Config.PointName[5] := 'DataProductCH04';
	ProductDataSQLQuery.Config.PointName[6] := 'DataProductCH05';
	ProductDataSQLQuery.Config.PointName[7] := 'DataProductCH06';
	bSimEnable := TRUE;
END_PROGRAM

PROGRAM _CYCLIC
	(*Fill gDataSQLProduct (DataSQLProduct_typ), then set ProductInsert*)
	UtcDTGetTime_0.enable := TRUE;
	UtcDTGetTime_0();
	IF UtcDTGetTime_0.status = 0 THEN
		dtNow := UtcDTGetTime_0.DT1;
	END_IF

	(*Refresh process values every second so 5 s time collection sees changing data.*)
	tonProductData(IN := bSimEnable AND gDataSQLBatchOpen AND NOT tonProductData.Q, PT := T#1s);
	IF tonProductData.Q THEN
		rProcessDrift := rProcessDrift + RandRangeReal(-0.004, 0.004);
		IF rProcessDrift > 0.030 THEN
			rProcessDrift := 0.030;
		ELSIF rProcessDrift < -0.030 THEN
			rProcessDrift := -0.030;
		END_IF
		gDataSQLProduct.TimeStamp := dtNow;
		gDataSQLProduct.State := 2;
		gDataSQLProduct.CH01 := gDataSQLRecipe.CH00 + rProcessDrift + RandRangeReal(-0.010, 0.010);
		gDataSQLProduct.CH02 := gDataSQLRecipe.CH01 + RandRangeReal(-2.0, 2.0);
		gDataSQLProduct.CH03 := gDataSQLRecipe.CH03 + RandRangeReal(-0.08, 0.08);
		gDataSQLProduct.CH04 := gDataSQLRecipe.CH02 + RandRangeReal(-0.05, 0.05);
		gDataSQLProduct.CH05 := gDataSQLRecipe.CH05 + RandRangeReal(-0.5, 0.5);
		gDataSQLProduct.CH06 := gDataSQLRecipe.CH07 + RandRangeReal(-0.05, 0.05);
	END_IF

	(*Variable-trigger cadence is deliberately slower than the 5 s time trigger.*)
	tonProductTrigger(IN := bSimEnable AND gDataSQLBatchOpen AND NOT gDataSQLOperate.ProductInsert, PT := T#25s);
	IF EDGEPOS(tonProductTrigger.Q) OR (bManualTrigger AND NOT gDataSQLOperate.ProductInsert) THEN
		bManualTrigger := FALSE;
		gDataSQLProduct.TimeStamp := dtNow;
		gDataSQLOperate.ProductInsert := TRUE;
	END_IF
END_PROGRAM

PROGRAM _EXIT

END_PROGRAM
