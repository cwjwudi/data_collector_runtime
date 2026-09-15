(*UserBridge: mappView clientInfo -> PLC session -> Runtime exchange*)
TYPE
	SdSmaClientInfo_typ : 	STRUCT  (*written by mappView list binding, selector = clientInfo.slotId*)
		isValid : BOOL;
		slotId : DINT;
		userId : STRING[128];
		ipAddress : STRING[64];
	END_STRUCT;
	SdSmaSession_typ : 	STRUCT  (*owned by UserBridge task, read by mappView (codeQuery) and Runtime (seq/userId/roles)*)
		seq : UDINT; (*incremented on every login/logout/user change*)
		active : BOOL; (*TRUE while a non-anonymous user is logged in*)
		userId : STRING[128];
		roles : STRING[255]; (*comma separated AR roles*)
		rolesPending : BOOL; (*ArUser lookup in progress*)
		rolesErrorId : DINT;
		codeQuery : STRING[80]; (*'sdsma_code=<32 hex>' bound to WebViewer.query, '' when inactive*)
		codeUsed : BOOL;
	END_STRUCT;
END_TYPE
