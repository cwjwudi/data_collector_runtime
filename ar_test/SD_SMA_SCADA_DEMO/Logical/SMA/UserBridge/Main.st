(*
UserBridge: HMI user (mappView clientInfo, written per slot by list binding) -> PLC session
(seq / roles / one-time code) -> Runtime exchanges the code via OPC UA method SdSmaExchange.
See _Docs/UserBridge 开发计划.md
*)
PROGRAM _INIT
	fbMethod.MethodName := 'SdSmaExchange';
END_PROGRAM

PROGRAM _CYCLIC
	(* 1. login / logout / user change edge per client slot *)
	FOR i := 0 TO SDSMA_MAX_CLIENTS_MINUS DO
		bActive := gSdSmaClient[i].isValid AND gSdSmaClient[i].userId <> '' AND gSdSmaClient[i].userId <> 'Anonymous';
		IF bActive <> gSdSmaSession[i].active OR (bActive AND gSdSmaClient[i].userId <> gSdSmaSession[i].userId) THEN
			gSdSmaSession[i].seq := gSdSmaSession[i].seq + 1;
			gSdSmaSession[i].active := bActive;
			gSdSmaSession[i].roles := '';
			gSdSmaSession[i].rolesErrorId := 0;
			gSdSmaSession[i].codeUsed := FALSE;
			IF bActive THEN
				gSdSmaSession[i].userId := gSdSmaClient[i].userId;
				gSdSmaSession[i].rolesPending := TRUE;
				(* ponytail: libc rand() mixed with seq/clock is not a CSPRNG; upgrade = AR6 crypto RNG,
				   or Runtime-supplied challenge signed by PLC HMAC. Code is one-time and slot/seq bound. *)
				FOR k := 0 TO 3 DO
					rnd[k] := RandUDINT() XOR ROL(gSdSmaSession[i].seq * 2654435761, k * 8) XOR TIME_TO_UDINT(clock_ms());
				END_FOR
				FOR k := 0 TO 31 DO
					nibble := UDINT_TO_USINT(SHR(rnd[k / 8], (7 - (k MOD 8)) * 4) AND 16#F);
					IF nibble < 10 THEN
						codeBytes[k] := 48 + nibble;
					ELSE
						codeBytes[k] := 87 + nibble;
					END_IF
				END_FOR
				codeBytes[32] := 0;
				gSdSmaSession[i].codeQuery := 'sdsma_code=';
				brsstrcat(ADR(gSdSmaSession[i].codeQuery), ADR(codeBytes));
			ELSE
				gSdSmaSession[i].userId := '';
				gSdSmaSession[i].rolesPending := FALSE;
				gSdSmaSession[i].codeQuery := '';
			END_IF
		END_IF
	END_FOR

	(* 2. resolve AR roles for one pending slot at a time (ArUser, works on AR 4.x and 6.x) *)
	CASE roleStep OF
		0:
			FOR k := 0 TO SDSMA_MAX_CLIENTS_MINUS DO
				IF gSdSmaSession[k].rolesPending AND roleStep = 0 THEN
					roleSlot := k;
					roleSeq := gSdSmaSession[k].seq;
					roleStep := 1;
				END_IF
			END_FOR
		1:
			fbInitList.ListType := arUSER_USERROLES;
			fbInitList.ElementName := gSdSmaSession[roleSlot].userId;
			fbInitList.Execute := TRUE;
			fbInitList();
			(* pattern for every ArUser FB below: keep Execute TRUE until Done/Error, then reset with one extra call
			   in the same cycle. Never toggle Execute while Busy and never call a FB after its list is destroyed -
			   both crash ArUser (syncArUserGetFirst ArException seen on ARsim). *)
			IF fbInitList.Done THEN
				roleList := fbInitList.List;
				fbInitList.Execute := FALSE;
				fbInitList();
				roleStep := 2;
			ELSIF fbInitList.Error THEN
				gSdSmaSession[roleSlot].rolesErrorId := fbInitList.ErrorID;
				fbInitList.Execute := FALSE;
				fbInitList();
				roleStep := 5;
			END_IF
		2:
			fbGetFirst.List := roleList;
			fbGetFirst.Execute := TRUE;
			fbGetFirst();
			IF fbGetFirst.Done THEN
				IF gSdSmaSession[roleSlot].seq = roleSeq THEN
					gSdSmaSession[roleSlot].roles := fbGetFirst.ElementName;
				END_IF
				fbGetFirst.Execute := FALSE;
				fbGetFirst();
				roleStep := 3;
			ELSIF fbGetFirst.Error THEN
				IF fbGetFirst.ErrorID <> arUSER_WRN_END_OF_LIST THEN
					gSdSmaSession[roleSlot].rolesErrorId := fbGetFirst.ErrorID;
				END_IF
				fbGetFirst.Execute := FALSE;
				fbGetFirst();
				roleStep := 4;
			END_IF
		3:
			fbGetNext.List := roleList;
			fbGetNext.Execute := TRUE;
			fbGetNext();
			IF fbGetNext.Done THEN
				IF gSdSmaSession[roleSlot].seq = roleSeq THEN
					brsstrcat(ADR(gSdSmaSession[roleSlot].roles), ADR(sComma));
					brsstrcat(ADR(gSdSmaSession[roleSlot].roles), ADR(fbGetNext.ElementName));
				END_IF
				fbGetNext.Execute := FALSE;
				fbGetNext(); (* reset, re-triggered next cycle for the following element *)
			ELSIF fbGetNext.Error THEN
				IF fbGetNext.ErrorID <> arUSER_WRN_END_OF_LIST THEN
					gSdSmaSession[roleSlot].rolesErrorId := fbGetNext.ErrorID;
				END_IF
				fbGetNext.Execute := FALSE;
				fbGetNext();
				roleStep := 4;
			END_IF
		4:
			fbDestroyList.Execute := TRUE;
			fbDestroyList(List := roleList);
			IF fbDestroyList.Done OR fbDestroyList.Error THEN
				fbDestroyList.Execute := FALSE;
				fbDestroyList(List := roleList);
				roleStep := 5;
			END_IF
		5:
			IF gSdSmaSession[roleSlot].seq = roleSeq THEN
				gSdSmaSession[roleSlot].rolesPending := FALSE;
			END_IF
			roleStep := 0;
	END_CASE

	(* 3. OPC UA method SdSmaExchange: one-time code -> session identity *)
	CASE exStep OF
		0:
			fbMethod.Action := UaMoa_CheckIsCalled;
			fbMethod.Execute := TRUE;
			exStep := 1;
		1:
			fbMethod();
			IF NOT fbMethod.Busy AND (fbMethod.Done OR fbMethod.Error) THEN
				exMethodErrorId := fbMethod.ErrorID;
				IF fbMethod.Done AND fbMethod.IsCalled THEN
					exStatus := 1;
					exSlot := -1;
					exSeq := 0;
					exUserId := '';
					exRoles := '';
					exIp := '';
					IF exCode <> '' THEN
						FOR k := 0 TO SDSMA_MAX_CLIENTS_MINUS DO
							(* codeQuery = 'sdsma_code=' (11 chars) + code *)
							IF gSdSmaSession[k].active AND brsstrcmp(ADR(exCode), ADR(gSdSmaSession[k].codeQuery) + 11) = 0 THEN
								IF gSdSmaSession[k].codeUsed THEN
									exStatus := 2;
								ELSIF gSdSmaSession[k].rolesPending THEN
									exStatus := 3;
								ELSE
									exStatus := 0;
									exSlot := k;
									exSeq := gSdSmaSession[k].seq;
									exUserId := gSdSmaSession[k].userId;
									exRoles := gSdSmaSession[k].roles;
									exIp := gSdSmaClient[k].ipAddress;
									gSdSmaSession[k].codeUsed := TRUE;
								END_IF
							END_IF
						END_FOR
					END_IF
					exStep := 2;
				ELSE
					exStep := 9;
				END_IF
			END_IF
		2:
			fbMethod.Execute := FALSE;
			fbMethod();
			exStep := 3;
		3:
			fbMethod.Action := UaMoa_Finished;
			fbMethod.Execute := TRUE;
			exStep := 4;
		4:
			fbMethod();
			IF NOT fbMethod.Busy AND (fbMethod.Done OR fbMethod.Error) THEN
				exMethodErrorId := fbMethod.ErrorID;
				exStep := 9;
			END_IF
		9:
			fbMethod.Execute := FALSE;
			fbMethod();
			exStep := 0;
	END_CASE
END_PROGRAM

PROGRAM _EXIT
END_PROGRAM
