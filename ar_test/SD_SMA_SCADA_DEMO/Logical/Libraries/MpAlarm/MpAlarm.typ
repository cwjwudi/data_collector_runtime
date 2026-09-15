
TYPE
	MpAlarmBasicUIModeEnum : 
		(
		mpALARM_BASIC_UI_MODE_ALL := 0, (*Displays all alarms of the connected component*)
		mpALARM_BASIC_UI_MODE_MAPP := 1, (*Displays only mapp alarms of the connected component*)
		mpALARM_BASIC_UI_MODE_USER := 2 (*Displays only user-defined alarms of the connected component*)
		);
	MpAlarmBasicUISetupType : 	STRUCT 
		ImageSize : UDINT; (*Number of alarms. The alarm/acknowledgment image must be of this size*)
		AlarmImage : UDINT; (*Pointer to the alarm image (BOOL array that can be connected to the VC 4 alarm image)*)
		AcknowledgeImage : UDINT; (*Pointer to the acknowledgment image (BOOL array that can be connected to the VC 4 alarm image)*)
		AddInfoStringSize : UINT; (*Size of a string from the "AddInfo" input (number of characters including null terminator)*)
		AddInfo : UDINT; (*Pointer to additional alarm information (array of STRING[AddInfoStringSize]) that can be added as a text snippet*)
		ImageUpdateTime : UDINT := 50; (*VisualComponents Alarm Image Update Time (Refresh rate)*)
	END_STRUCT;
	MpAlarmBasicUIConnect20Type : 	STRUCT  (*Ready-made UIConnect structure for max 20 alarms. Fits for all mapp components.*)
		AlarmImage : ARRAY[0..19]OF BOOL; (*Alarm image (BOOL array that can be connected to the VC 4 alarm image)*)
		AcknowledgeImage : ARRAY[0..19]OF BOOL; (*Acknowledgment image (BOOL array that can be connected to the VC 4 alarm image)*)
		AddInfo : ARRAY[0..19]OF STRING[100]; (*Additional alarm information that can be added as a text snippet*)
	END_STRUCT;
	MpAlarmBasicUIConnect100Type : 	STRUCT  (*Ready-made UIConnect structure for max 100 alarms. Fits for the user alarms of MpAlarmBasic.*)
		AlarmImage : ARRAY[0..99]OF BOOL; (*Alarm image (BOOL array that can be connected to the VC 4 alarm image)*)
		AcknowledgeImage : ARRAY[0..99]OF BOOL; (*Acknowledgment image (BOOL array that can be connected to the VC 4 alarm image)*)
		AddInfo : ARRAY[0..99]OF STRING[100]; (*Additional alarm information that can be added as a text snippet*)
	END_STRUCT;
END_TYPE

(*FB Info Types*)

TYPE
	MpAlarmStatusIDType : 	STRUCT 
		ID : MpAlarmErrorEnum; (*Error code for mapp component*)
		Severity : MpComSeveritiesEnum; (*Describes the type of information supplied by the status ID (success, information, warning, error)*)
		Code : UINT; (*Code for the status ID. This error number can be used to search for additional information in the help system*)
	END_STRUCT;
	MpAlarmDiagType : 	STRUCT 
		StatusID : MpAlarmStatusIDType; (*StatusID diagnostic structure*)
	END_STRUCT;
	MpAlarmInfoType : 	STRUCT 
		Diag : MpAlarmDiagType; (*Diagnostic structure for the status ID*)
	END_STRUCT;
END_TYPE

(*FB Internal Types*)

TYPE
	MpAlarmBasicUIInternalType : 	STRUCT 
		pObject : UDINT;
		pInstance : UDINT;
		State : DINT;
	END_STRUCT;
END_TYPE
