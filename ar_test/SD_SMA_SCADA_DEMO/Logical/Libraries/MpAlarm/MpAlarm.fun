
FUNCTION_BLOCK MpAlarmBasic (*mapp component for a basic centralized alarm collection and management*) (* $GROUP=mapp Services,$CAT=VC Alarm Extension,$GROUPICON=Icon_mapp.png,$CATICON=Icon_MpAlarm.png *)
	VAR_INPUT
		MpLink : REFERENCE TO MpComIdentType; (*Connection to mapp*) (* *) (*#PAR#;*)
		Enable : BOOL; (*Enables/Disables the function block*) (* *) (*#PAR#;*)
		ErrorReset : BOOL; (*Resets function block errors*) (* *) (*#PAR#;*)
	END_VAR
	VAR_OUTPUT
		Active : BOOL; (*Indicates whether the function block is active*) (* *) (*#PAR#;*)
		Error : BOOL; (*Indicates that the function block is in an error state or a command was not executed correctly*) (* *) (*#PAR#;*)
		StatusID : DINT; (*Status information about the function block*) (* *) (*#PAR#; *)
		ActiveAlarms : UDINT; (*Number of currently active alarms*) (* *) (*#CMD#;*)
		PendingAlarms : UDINT; (*Indicates the number of alarms that are either active or not yet acknowledged. In this way, it is an exact reproduction of the alarms being shown on the visualization device*) (* *) (*#CMD#;*)
		Reaction : UDINT; (*Bit-coded reaction to currently active alarms*) (* *) (*#CMD#;*)
		Info : MpAlarmInfoType; (*Additional information about the component*) (* *) (*#CMD#;*)
	END_VAR
	VAR
		Internal : {REDUND_UNREPLICABLE} MpComInternalDataType; (*Internal data*) (* *) (*#OMIT#;*)
	END_VAR
END_FUNCTION_BLOCK

FUNCTION_BLOCK MpAlarmBasicUI (*mapp component for connecting alarms to the VC4 alarm system*) (* $GROUP=mapp Services,$CAT=VC Alarm Extension,$GROUPICON=Icon_mapp.png,$CATICON=Icon_MpAlarm.png *)
	VAR_INPUT
		MpLink : REFERENCE TO MpComIdentType; (*Connection to mapp*) (* *) (*#PAR#;*)
		Enable : BOOL; (*Enables/Disables the function block*) (* *) (*#PAR#;*)
		ErrorReset : BOOL; (*Resets function block errors*) (* *) (*#PAR#;*)
		UISetup : {REDUND_UNREPLICABLE} MpAlarmBasicUISetupType; (*Used to configure the elements connected to the HMI application*) (* *) (*#PAR#;*)
		Mode : MpAlarmBasicUIModeEnum; (*Mode: Show mapp alarms / user alarms / all alarms - This must be decided before the function block is enabled*) (* *) (*#PAR#;*)
	END_VAR
	VAR_OUTPUT
		Active : BOOL; (*Indicates whether the function block is active*) (* *) (*#PAR#;*)
		Error : BOOL; (*Indicates that the function block is in an error state or a command was not executed correctly*) (* *) (*#PAR#;*)
		StatusID : DINT; (*Status information about the function block*) (* *) (*#PAR#; *)
		Info : MpAlarmInfoType; (*Additional information about the component*) (* *) (*#CMD#;*)
	END_VAR
	VAR
		Internal : {REDUND_UNREPLICABLE} MpAlarmBasicUIInternalType; (*Internal data*) (* *) (*#OMIT#;*)
	END_VAR
END_FUNCTION_BLOCK

FUNCTION MpAlarmReaction : BOOL (*Indicates whether one of the filtered reactions is active*) (* $GROUP=mapp Services,$CAT=VC Alarm Extension,$GROUPICON=Icon_mapp.png,$CATICON=Icon_MpAlarm.png *)
	VAR_INPUT
		MpLink : MpComIdentType; (*Connection to mapp*) (* *) (*#PAR#;*)
		Filter : UDINT; (*Filter (bit-coded) that can be used to check special reactions*) (* *) (*#PAR#;*)
	END_VAR
END_FUNCTION

FUNCTION MpAlarmSet : DINT (*Add-On function to set (raise) a user alarm. By calling this function the alarm system is informed that a specific alarm is active. Returns result code.*) (* $GROUP=mapp Services,$CAT=VC Alarm Extension,$GROUPICON=Icon_mapp.png,$CATICON=Icon_MpAlarm.png *)
	VAR_INPUT
		MpLink : MpComIdentType; (*Connection to mapp*) (* *) (*#PAR#;*)
		AlarmID : UDINT; (*User alarm (starts with 0)*) (* *) (*#PAR#;*)
		AddInfo : STRING[100]; (*Additional information in text form (e.g. internal error number, alarm text, temperature, etc.)*) (* *) (*#PAR#OPT#;*)
	END_VAR
END_FUNCTION

FUNCTION MpAlarmReset : DINT (*Add-On function to reset a user alarm. By calling this function the alarm system is informed that a specific alarm is inactive. Returns result code.*) (* $GROUP=mapp Services,$CAT=VC Alarm Extension,$GROUPICON=Icon_mapp.png,$CATICON=Icon_MpAlarm.png *)
	VAR_INPUT
		MpLink : MpComIdentType; (*Connection to mapp*) (* *) (*#PAR#;*)
		AlarmID : UDINT; (*User alarm ID (starts with 0)*) (* *) (*#PAR#;*)
	END_VAR
END_FUNCTION

FUNCTION_BLOCK MpAlarmRegUser (*Add-On function block used to register user alarms with an empty mapp component*) (* $GROUP=mapp Services,$CAT=VC Alarm Extension,$GROUPICON=Icon_mapp.png,$CATICON=Icon_MpAlarm.png *)
	VAR_INPUT
		MpLink : REFERENCE TO MpComIdentType; (*Connection to mapp*) (* *) (*#PAR#;*)
		Enable : BOOL; (*Enables/Disables the function block*) (* *) (*#PAR#;*)
		ErrorReset : BOOL; (*Resets function block errors*) (* *) (*#PAR#;*)
		NumberOfAlarms : UINT; (*Number of alarms for which space should be made*) (* *) (*#PAR#;*)
	END_VAR
	VAR_OUTPUT
		Active : BOOL; (*Indicates whether the function block is active*) (* *) (*#PAR#;*)
		Error : BOOL; (*Indicates that the function block is in an error state or a command was not executed correctly*) (* *) (*#PAR#;*)
		StatusID : DINT; (*Status information about the function block*) (* *) (*#PAR#; *)
		Info : MpAlarmInfoType; (*Additional information about the component*) (* *) (*#CMD#;*)
	END_VAR
	VAR
		Internal : {REDUND_UNREPLICABLE} MpComInternalDataType; (* *) (* *) (*#OMIT#;*)
	END_VAR
END_FUNCTION_BLOCK
