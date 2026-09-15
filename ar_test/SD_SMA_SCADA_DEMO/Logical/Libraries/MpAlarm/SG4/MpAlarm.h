/* Automation Studio generated header file */
/* Do not edit ! */
/* MpAlarm 5.24.3 */

#ifndef _MPALARM_
#define _MPALARM_
#ifdef __cplusplus
extern "C" 
{
#endif
#ifndef _MpAlarm_VERSION
#define _MpAlarm_VERSION 5.24.3
#endif

#include <bur/plctypes.h>

#ifndef _BUR_PUBLIC
#define _BUR_PUBLIC
#endif
#ifdef _SG3
		#include "MpBase.h"
#endif

#ifdef _SG4
		#include "MpBase.h"
#endif

#ifdef _SGC
		#include "MpBase.h"
#endif



/* Datatypes and datatypes of function blocks */
typedef enum MpAlarmErrorEnum
{	mpALARM_NO_ERROR = 0,
	mpALARM_ERR_ACTIVATION = -1064239103,
	mpALARM_ERR_MPLINK_NULL = -1064239102,
	mpALARM_ERR_MPLINK_INVALID = -1064239101,
	mpALARM_ERR_MPLINK_CHANGED = -1064239100,
	mpALARM_ERR_MPLINK_CORRUPT = -1064239099,
	mpALARM_ERR_MPLINK_IN_USE = -1064239098,
	mpALARM_ERR_CONFIG_INVALID = -1064239091,
	mpALARM_ERR_ALARM_ID_OVER_MAX = -1064157184,
	mpALARM_ERR_IMAGE_NULL = -1064157183,
	mpALARM_WRN_IMAGE_SIZE = -2137899006,
	mpALARM_ERR_NO_ALARMS_FOUND = -1064157181,
	mpALARM_ERR_NUMBER_OF_ALARMS = -1064157180,
	mpALARM_INF_WAIT_BASIC_FB = 1083326469
} MpAlarmErrorEnum;

typedef enum MpAlarmBasicUIModeEnum
{	mpALARM_BASIC_UI_MODE_ALL = 0,
	mpALARM_BASIC_UI_MODE_MAPP = 1,
	mpALARM_BASIC_UI_MODE_USER = 2
} MpAlarmBasicUIModeEnum;

typedef struct MpAlarmBasicUISetupType
{	unsigned long ImageSize;
	unsigned long AlarmImage;
	unsigned long AcknowledgeImage;
	unsigned short AddInfoStringSize;
	unsigned long AddInfo;
	unsigned long ImageUpdateTime;
} MpAlarmBasicUISetupType;

typedef struct MpAlarmBasicUIConnect20Type
{	plcbit AlarmImage[20];
	plcbit AcknowledgeImage[20];
	plcstring AddInfo[20][101];
} MpAlarmBasicUIConnect20Type;

typedef struct MpAlarmBasicUIConnect100Type
{	plcbit AlarmImage[100];
	plcbit AcknowledgeImage[100];
	plcstring AddInfo[100][101];
} MpAlarmBasicUIConnect100Type;

typedef struct MpAlarmStatusIDType
{	enum MpAlarmErrorEnum ID;
	MpComSeveritiesEnum Severity;
	unsigned short Code;
} MpAlarmStatusIDType;

typedef struct MpAlarmDiagType
{	struct MpAlarmStatusIDType StatusID;
} MpAlarmDiagType;

typedef struct MpAlarmInfoType
{	struct MpAlarmDiagType Diag;
} MpAlarmInfoType;

typedef struct MpAlarmBasicUIInternalType
{	unsigned long pObject;
	unsigned long pInstance;
	signed long State;
} MpAlarmBasicUIInternalType;

typedef struct MpAlarmBasic
{
	/* VAR_INPUT (analog) */
	struct MpComIdentType* MpLink;
	/* VAR_OUTPUT (analog) */
	signed long StatusID;
	unsigned long ActiveAlarms;
	unsigned long PendingAlarms;
	unsigned long Reaction;
	struct MpAlarmInfoType Info;
	/* VAR (analog) */
	struct MpComInternalDataType Internal;
	/* VAR_INPUT (digital) */
	plcbit Enable;
	plcbit ErrorReset;
	/* VAR_OUTPUT (digital) */
	plcbit Active;
	plcbit Error;
} MpAlarmBasic_typ;

typedef struct MpAlarmBasicUI
{
	/* VAR_INPUT (analog) */
	struct MpComIdentType* MpLink;
	struct MpAlarmBasicUISetupType UISetup;
	enum MpAlarmBasicUIModeEnum Mode;
	/* VAR_OUTPUT (analog) */
	signed long StatusID;
	struct MpAlarmInfoType Info;
	/* VAR (analog) */
	struct MpAlarmBasicUIInternalType Internal;
	/* VAR_INPUT (digital) */
	plcbit Enable;
	plcbit ErrorReset;
	/* VAR_OUTPUT (digital) */
	plcbit Active;
	plcbit Error;
} MpAlarmBasicUI_typ;

typedef struct MpAlarmRegUser
{
	/* VAR_INPUT (analog) */
	struct MpComIdentType* MpLink;
	unsigned short NumberOfAlarms;
	/* VAR_OUTPUT (analog) */
	signed long StatusID;
	struct MpAlarmInfoType Info;
	/* VAR (analog) */
	struct MpComInternalDataType Internal;
	/* VAR_INPUT (digital) */
	plcbit Enable;
	plcbit ErrorReset;
	/* VAR_OUTPUT (digital) */
	plcbit Active;
	plcbit Error;
} MpAlarmRegUser_typ;



/* Prototyping of functions and function blocks */
_BUR_PUBLIC void MpAlarmBasic(struct MpAlarmBasic* inst);
_BUR_PUBLIC void MpAlarmBasicUI(struct MpAlarmBasicUI* inst);
_BUR_PUBLIC void MpAlarmRegUser(struct MpAlarmRegUser* inst);
_BUR_PUBLIC plcbit MpAlarmReaction(struct MpComIdentType* MpLink, unsigned long Filter);
_BUR_PUBLIC signed long MpAlarmSet(struct MpComIdentType* MpLink, unsigned long AlarmID, plcstring* AddInfo);
_BUR_PUBLIC signed long MpAlarmReset(struct MpComIdentType* MpLink, unsigned long AlarmID);


#ifdef __cplusplus
};
#endif
#endif /* _MPALARM_ */

