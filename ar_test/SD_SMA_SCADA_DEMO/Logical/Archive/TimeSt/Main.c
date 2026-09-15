
#include <bur/plctypes.h>
#include <AsBrStr.h>

#ifdef _DEFAULT_INCLUDES
	#include <AsDefault.h>
#endif

static void append_usint_2d(char *dest, USINT v)
{
	char t[8];

	if (v < 10U)
		brsstrcat((UDINT)(unsigned long)dest, (UDINT)(unsigned long)"0");
	brsitoa((DINT)(UINT)v, (UDINT)(unsigned long)t);
	brsstrcat((UDINT)(unsigned long)dest, (UDINT)(unsigned long)t);
}

static void RTCtime_to_db_datetime_string(const RTCtime_typ *rtc, char *dest, UINT destSize)
{
	char ybuf[16];
	UINT y;

	if (destSize == 0U)
		return;

	brsmemset((UDINT)(unsigned long)dest, 0, (UDINT)destSize);

	y = rtc->year;
	if (y < 1000U)
		y += 1984U;

	brsitoa((DINT)y, (UDINT)(unsigned long)ybuf);
	brsstrcpy((UDINT)(unsigned long)dest, (UDINT)(unsigned long)ybuf);
	brsstrcat((UDINT)(unsigned long)dest, (UDINT)(unsigned long)"-");
	append_usint_2d(dest, rtc->month);
	brsstrcat((UDINT)(unsigned long)dest, (UDINT)(unsigned long)"-");
	append_usint_2d(dest, rtc->day);
	brsstrcat((UDINT)(unsigned long)dest, (UDINT)(unsigned long)" ");
	append_usint_2d(dest, rtc->hour);
	brsstrcat((UDINT)(unsigned long)dest, (UDINT)(unsigned long)":");
	append_usint_2d(dest, rtc->minute);
	brsstrcat((UDINT)(unsigned long)dest, (UDINT)(unsigned long)":");
	append_usint_2d(dest, rtc->second);
}

void _INIT ProgramInit(void)
{

}

void _CYCLIC ProgramCyclic(void)
{
	RTC_gettime(&stRTCtime);
	RTCtime_to_db_datetime_string(&stRTCtime, (char *)sDbDateTime, (UINT)sizeof(sDbDateTime));
}

void _EXIT ProgramExit(void)
{

}

