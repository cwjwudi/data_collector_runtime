
TYPE
    MpAlarmErrorEnum : 
        ( (* Error numbers of library MpAlarm *)
        mpALARM_NO_ERROR := 0, (* No error *)
        mpALARM_ERR_ACTIVATION := -1064239103, (* Could not create component [Error: 1, 0xc0910001] *)
        mpALARM_ERR_MPLINK_NULL := -1064239102, (* MpLink is NULL pointer [Error: 2, 0xc0910002] *)
        mpALARM_ERR_MPLINK_INVALID := -1064239101, (* MpLink connection not allowed [Error: 3, 0xc0910003] *)
        mpALARM_ERR_MPLINK_CHANGED := -1064239100, (* MpLink modified [Error: 4, 0xc0910004] *)
        mpALARM_ERR_MPLINK_CORRUPT := -1064239099, (* Invalid MpLink contents [Error: 5, 0xc0910005] *)
        mpALARM_ERR_MPLINK_IN_USE := -1064239098, (* MpLink already in use [Error: 6, 0xc0910006] *)
        mpALARM_ERR_CONFIG_INVALID := -1064239091, (* Invalid Configuration [Error: 13, 0xc091000d] *)
        mpALARM_ERR_ALARM_ID_OVER_MAX := -1064157184, (* Alarm number too high [Error: 16384, 0xc0924000] *)
        mpALARM_ERR_IMAGE_NULL := -1064157183, (* Image is NULL [Error: 16385, 0xc0924001] *)
        mpALARM_WRN_IMAGE_SIZE := -2137899006, (* Image smaller than maximum numbers of alarms [Warning: 16386, 0x80924002] *)
        mpALARM_ERR_NO_ALARMS_FOUND := -1064157181, (* Component has no alarms [Error: 16387, 0xc0924003] *)
        mpALARM_ERR_NUMBER_OF_ALARMS := -1064157180, (* Invalid number of alarms [Error: 16388, 0xc0924004] *)
        mpALARM_INF_WAIT_BASIC_FB := 1083326469 (* Waiting for parent component [Informational: 16389, 0x40924005] *)
        );
END_TYPE
