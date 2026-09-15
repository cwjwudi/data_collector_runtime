
PROGRAM _INIT
	RecipeDataSQLQuery.Config.PointName[0] := 'DataRecipeTime';
	RecipeDataSQLQuery.Config.PointName[1] := 'DataRecipeState';
	RecipeDataSQLQuery.Config.PointName[2] := 'DataRecipeCH00';
	RecipeDataSQLQuery.Config.PointName[3] := 'DataRecipeCH01';
	RecipeDataSQLQuery.Config.PointName[4] := 'DataRecipeCH02';
	RecipeDataSQLQuery.Config.PointName[5] := 'DataRecipeCH03';
	RecipeDataSQLQuery.Config.PointName[6] := 'DataRecipeCH04';
	RecipeDataSQLQuery.Config.PointName[7] := 'DataRecipeCH05';
	RecipeDataSQLQuery.Config.PointName[8] := 'DataRecipeCH06';
	RecipeDataSQLQuery.Config.PointName[9] := 'DataRecipeCH07';
	RecipeDataSQLQuery.Config.PointName[10] := 'DataRecipeCH08';
	RecipeDataSQLQuery.Config.PointName[11] := 'DataRecipeCH09';
	RecipeDataSQLQuery.Config.PointName[12] := 'DataRecipeCH10';
	RecipeDataSQLQuery.Config.PointName[13] := 'DataRecipeCH11';
	RecipeDataSQLQuery.Config.PointName[14] := 'DataRecipeCH12';
	RecipeDataSQLQuery.Config.PointName[15] := 'DataRecipeCH13';
	RecipeDataSQLQuery.Config.PointName[16] := 'DataRecipeCH14';
	RecipeDataSQLQuery.Config.PointName[17] := 'DataRecipeCH15';
	RecipeDataSQLQuery.Config.PointName[18] := 'DataRecipeCH16';
	RecipeDataSQLQuery.Config.PointName[19] := 'DataRecipeCH17';
	RecipeDataSQLQuery.Config.PointName[20] := 'DataRecipeCH18';
	RecipeDataSQLQuery.Config.PointName[21] := 'DataRecipeCH19';
	RecipeDataSQLQuery.Config.PointName[22] := 'DataRecipeCH20';
	RecipeDataSQLQuery.Config.PointName[23] := 'DataRecipeCH21';
	RecipeDataSQLQuery.Config.PointName[24] := 'DataRecipeCH22';
	RecipeDataSQLQuery.Config.PointName[25] := 'DataRecipeCH23';
	RecipeDataSQLQuery.Config.PointName[26] := 'DataRecipeCH24';
	RecipeDataSQLQuery.Config.PointName[27] := 'DataRecipeCH25';
	bSimEnable := TRUE;
END_PROGRAM

PROGRAM _CYCLIC
	(*Fill gDataSQLRecipe (DataSQLRecipe_typ), then set RecipeInsert*)
	UtcDTGetTime_0.enable := TRUE;
	UtcDTGetTime_0();
	IF UtcDTGetTime_0.status = 0 THEN
		dtNow := UtcDTGetTime_0.DT1;
	END_IF

	(*Recipes change less frequently than product samples. PVI can request an extra snapshot.*)
	tonRecipe(IN := bSimEnable AND gDataSQLBatchOpen AND NOT gDataSQLOperate.RecipeInsert, PT := T#30s);
	IF EDGEPOS(tonRecipe.Q) OR EDGEPOS(gDataSQLBatchOpen) OR (bManualTrigger AND NOT gDataSQLOperate.RecipeInsert) THEN
		bManualTrigger := FALSE;
		diRecipeProfile := RandRange(1, 3);
		gDataSQLRecipe.TimeStamp := dtNow;
		gDataSQLRecipe.State := DINT_TO_UDINT(diRecipeProfile);
		CASE diRecipeProfile OF
			1:
				gDataSQLRecipe.CH00 := 0.500;
				gDataSQLRecipe.CH01 := 55.0;
				gDataSQLRecipe.CH02 := 9.00;
				gDataSQLRecipe.CH03 := 3.50;
				gDataSQLRecipe.CH04 := 72.0;
			2:
				gDataSQLRecipe.CH00 := 0.650;
				gDataSQLRecipe.CH01 := 62.0;
				gDataSQLRecipe.CH02 := 10.00;
				gDataSQLRecipe.CH03 := 4.00;
				gDataSQLRecipe.CH04 := 64.0;
			ELSE
				gDataSQLRecipe.CH00 := 0.350;
				gDataSQLRecipe.CH01 := 48.0;
				gDataSQLRecipe.CH02 := 8.00;
				gDataSQLRecipe.CH03 := 3.00;
				gDataSQLRecipe.CH04 := 80.0;
		END_CASE
		gDataSQLRecipe.CH05 := RandRangeReal(8.0, 12.0);
		gDataSQLRecipe.CH06 := RandRangeReal(18.0, 24.0);
		gDataSQLRecipe.CH07 := RandRangeReal(0.8, 1.2);
		gDataSQLRecipe.CH08 := RandRangeReal(2.0, 4.0);
		gDataSQLRecipe.CH09 := RandRangeReal(4.0, 7.0);
		gDataSQLRecipe.CH10 := RandRangeReal(35.0, 45.0);
		gDataSQLRecipe.CH11 := RandRangeReal(0.5, 1.0);
		gDataSQLRecipe.CH12 := RandRangeReal(0.5, 1.0);
		gDataSQLRecipe.CH13 := RandRangeReal(2.0, 4.0);
		gDataSQLRecipe.CH14 := RandRangeReal(10.0, 16.0);
		gDataSQLRecipe.CH15 := RandRangeReal(0.2, 0.8);
		gDataSQLRecipe.CH16 := RandRangeReal(0.2, 0.8);
		gDataSQLRecipe.CH17 := RandRangeReal(7.0, 10.0);
		gDataSQLRecipe.CH18 := RandRangeReal(7.0, 10.0);
		gDataSQLRecipe.CH19 := RandRangeReal(20.0, 30.0);
		gDataSQLRecipe.CH20 := RandRangeReal(20.0, 30.0);
		gDataSQLRecipe.CH21 := RandRangeReal(80.0, 120.0);
		gDataSQLRecipe.CH22 := RandRangeReal(80.0, 120.0);
		gDataSQLRecipe.CH23 := DINT_TO_REAL(diRecipeProfile);
		gDataSQLRecipe.CH24 := 100000.0;
		gDataSQLRecipe.CH25 := 1.0;
		gDataSQLOperate.RecipeInsert := TRUE;
	END_IF
END_PROGRAM

PROGRAM _EXIT

END_PROGRAM
