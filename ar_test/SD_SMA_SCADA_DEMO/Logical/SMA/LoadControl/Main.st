PROGRAM _INIT
	bEnable := TRUE;
	uiTargetLoadPercent := 50;

	fbRuntimeInfo.enable := TRUE;
	fbRuntimeInfo();
	udCycleTimeUs := fbRuntimeInfo.cycle_time;
	IF udCycleTimeUs = 0 THEN
		(* Cyclic #4 is normally 100 ms; this fallback is only used if RTInfo fails. *)
		udCycleTimeUs := 100000;
	END_IF

	(* Start below the target; the feedback loop converges without an initial spike. *)
	udBusyBudgetUs := udCycleTimeUs / 5;

	fbIdleMeasure.enable := TRUE;
	fbIdleMeasure.measurementPeriod := 500;
	fbIdleMeasure.measurementLevel := 1;
	fbIdleMeasure();
END_PROGRAM

PROGRAM _CYCLIC
	(* Read the configured task-class period instead of relying on a fixed cycle time. *)
	fbRuntimeInfo.enable := TRUE;
	fbRuntimeInfo();
	IF (fbRuntimeInfo.status = 0) AND (fbRuntimeInfo.cycle_time > 0) THEN
		udCycleTimeUs := fbRuntimeInfo.cycle_time;
	END_IF

	(* Keep the operator setpoint in a range that leaves watchdog headroom. *)
	IF uiTargetLoadPercent < 5 THEN
		uiTargetLoadPercent := 5;
	ELSIF uiTargetLoadPercent > 75 THEN
		uiTargetLoadPercent := 75;
	END_IF

	(* Idle rate is averaged by Automation Runtime over a 500 ms interval. *)
	fbIdleMeasure.enable := TRUE;
	fbIdleMeasure.measurementPeriod := 500;
	fbIdleMeasure.measurementLevel := 1;
	fbIdleMeasure();
	uiIdleStatus := fbIdleMeasure.status;
	IF (uiIdleStatus = 0) AND (fbIdleMeasure.idleRate <= 100) THEN
		udMeasuredLoadPercent := 100 - fbIdleMeasure.idleRate;
	END_IF

	(* Update once per measurement interval. Gain 0.5 avoids oscillation. *)
	udControlElapsedUs := udControlElapsedUs + udCycleTimeUs;
	IF udControlElapsedUs >= 500000 THEN
		udControlElapsedUs := udControlElapsedUs - 500000;

		IF bEnable AND (uiIdleStatus = 0) THEN
			diLoadError := UINT_TO_DINT(uiTargetLoadPercent) - UDINT_TO_DINT(udMeasuredLoadPercent);
			diCorrectionUs := (diLoadError * UDINT_TO_DINT(udCycleTimeUs)) / 200;
			diNewBudgetUs := UDINT_TO_DINT(udBusyBudgetUs) + diCorrectionUs;
			udMaxBudgetUs := (udCycleTimeUs * 75) / 100;

			IF diNewBudgetUs < 0 THEN
				udBusyBudgetUs := 0;
			ELSIF DINT_TO_UDINT(diNewBudgetUs) > udMaxBudgetUs THEN
				udBusyBudgetUs := udMaxBudgetUs;
			ELSE
				udBusyBudgetUs := DINT_TO_UDINT(diNewBudgetUs);
			END_IF
		ELSIF NOT bEnable THEN
			udBusyBudgetUs := 0;
		END_IF
	END_IF

	IF bEnable AND (udBusyBudgetUs > 0) THEN
		(* Build a microsecond timestamp within the current second.  Re-read the
		   tick if it rolled while TIM_musec() was sampled. *)
		uiTick := TIM_ticks();
		uiMicrosecond := TIM_musec();
		uiTickCheck := TIM_ticks();
		IF uiTickCheck <> uiTick THEN
			uiTick := uiTickCheck;
			uiMicrosecond := TIM_musec();
		END_IF
		udStartUs := UINT_TO_UDINT(uiTick) * 10000 + UINT_TO_UDINT(uiMicrosecond);
		udElapsedUs := 0;

		WHILE udElapsedUs < udBusyBudgetUs DO
			uiTick := TIM_ticks();
			uiMicrosecond := TIM_musec();
			uiTickCheck := TIM_ticks();
			IF uiTickCheck <> uiTick THEN
				uiTick := uiTickCheck;
				uiMicrosecond := TIM_musec();
			END_IF
			udNowUs := UINT_TO_UDINT(uiTick) * 10000 + UINT_TO_UDINT(uiMicrosecond);

			IF udNowUs >= udStartUs THEN
				udElapsedUs := udNowUs - udStartUs;
			ELSE
				(* TIM_ticks() wraps every second. *)
				udElapsedUs := (1000000 - udStartUs) + udNowUs;
			END_IF
		END_WHILE
	END_IF
END_PROGRAM

PROGRAM _EXIT
	fbIdleMeasure.enable := FALSE;
	fbIdleMeasure();
END_PROGRAM
