#include "stdafx.h"
#include "Alaram.h"
#include "ParkingSpaceUtils.h"

class CAlaramImpl
{
public:
	CAlaramImpl(void);
	virtual ~CAlaramImpl(void);

	bool			SetAlarmData(LLONG lAnalyzerHandle, DWORD dwAlarmType, void* pAlarmInfo, BYTE* pBuffer, DWORD dwBufSize, int nSequence);

	BYTE*			GetPictureInfo( DWORD& dwPictureBufferSize);

	void			GetEventInfo(StuEventInfo& stuEventInfo);

	void			DestroyParam();

	std::string		GetGuid() const;

private:
	std::string		m_strGuid;
	AlarmData*		m_pAlarmData;
	StuEventInfo*	m_pEventInfo;
};

CAlaramImpl::CAlaramImpl(void):m_strGuid("")
{
	m_pAlarmData = NEW AlarmData;
	m_pEventInfo = NEW StuEventInfo;
}

CAlaramImpl::~CAlaramImpl(void)
{
	DestroyParam();
}

bool CAlaramImpl::SetAlarmData(LLONG lAnalyzerHandle, DWORD dwAlarmType, void* pAlarmInfo, BYTE* pBuffer, DWORD dwBufSize, int nSequence)
{
	if (NULL == m_pEventInfo || NULL == m_pAlarmData)
	{
		return false;
	}

	if (NULL == pAlarmInfo || 0 == dwBufSize)
	{
		return false;
	}

	switch(dwAlarmType)
	{		
	case EVENT_IVS_TRAFFIC_PARKINGSPACEPARKING://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_TRAFFIC_PARKINGSPACEPARKING_INFO);

			DEV_EVENT_TRAFFIC_PARKINGSPACEPARKING_INFO* pInfo = (DEV_EVENT_TRAFFIC_PARKINGSPACEPARKING_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strFileIndex =Int2Str(pInfo->stuFileInfo.bIndex);
			m_pEventInfo->stuEventInfoToDisplay.strFileCount =Int2Str(pInfo->stuFileInfo.bCount);
			m_pEventInfo->stuEventInfoToDisplay.strGroupId = Int2Str(pInfo->stuFileInfo.nGroupId);				

			m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stTrafficCar.szPlateNumber ;
			m_pEventInfo->stuEventInfoToDisplay.strPlateColor = pInfo->stTrafficCar.szPlateColor;
			m_pEventInfo->stuEventInfoToDisplay.strPlateType = pInfo->stTrafficCar.szPlateType;
			m_pEventInfo->stuEventInfoToDisplay.strVehicleType = pInfo->stuVehicle.szObjectSubType;			
			m_pEventInfo->stuEventInfoToDisplay.strVehicleColor = pInfo->stTrafficCar.szVehicleColor;

			m_pEventInfo->stuEventInfoToDisplay.strVehicleSize = VehicleSizeToString(pInfo->stTrafficCar.nVehicleSize);
			m_pEventInfo->stuEventInfoToDisplay.strLane  = pInfo->stTrafficCar.szCustomParkNo;

			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);

			m_pEventInfo->strDeviceAddress = (NULL == pInfo->stTrafficCar.szDeviceAddress) ?  "" : pInfo->stTrafficCar.szDeviceAddress;

			m_pEventInfo->rectBoundingBox = pInfo->stuObject.BoundingBox;

			m_pEventInfo->dwOffset		= pInfo->stuObject.stPicInfo.dwOffSet;
			m_pEventInfo->dwOffsetLength = pInfo->stuObject.stPicInfo.dwFileLenth;
		}
		break;
	case EVENT_IVS_TRAFFIC_PARKINGSPACENOPARKING://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_TRAFFIC_PARKINGSPACENOPARKING_INFO);

			DEV_EVENT_TRAFFIC_PARKINGSPACENOPARKING_INFO* pInfo = (DEV_EVENT_TRAFFIC_PARKINGSPACENOPARKING_INFO*)pAlarmInfo;

			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strFileIndex =Int2Str(pInfo->stuFileInfo.bIndex);
			m_pEventInfo->stuEventInfoToDisplay.strFileCount =Int2Str(pInfo->stuFileInfo.bCount);
			m_pEventInfo->stuEventInfoToDisplay.strGroupId = Int2Str(pInfo->stuFileInfo.nGroupId);				

			// No car information
			/*m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stTrafficCar.szPlateNumber ;
			m_pEventInfo->stuEventInfoToDisplay.strPlateColor = pInfo->stTrafficCar.szPlateColor;
			m_pEventInfo->stuEventInfoToDisplay.strPlateType = pInfo->stTrafficCar.szPlateType;
			m_pEventInfo->stuEventInfoToDisplay.strVehicleType = pInfo->stuVehicle.szObjectSubType;			
			m_pEventInfo->stuEventInfoToDisplay.strVehicleColor = pInfo->stTrafficCar.szVehicleColor;

			m_pEventInfo->stuEventInfoToDisplay.strVehicleSize = VehicleSizeToString(pInfo->stTrafficCar.nVehicleSize);
			m_pEventInfo->stuEventInfoToDisplay.strLane  = Int2Str(pInfo->stTrafficCar.szCustomParkNo);
			m_pEventInfo->strDeviceAddress = (NULL == pInfo->stTrafficCar.szDeviceAddress) ?  "" : pInfo->stTrafficCar.szDeviceAddress;*/

			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);

			m_pEventInfo->rectBoundingBox = pInfo->stuObject.BoundingBox;

			m_pEventInfo->dwOffset		= pInfo->stuObject.stPicInfo.dwOffSet;
			m_pEventInfo->dwOffsetLength = pInfo->stuObject.stPicInfo.dwFileLenth;
		}
		break;
	case EVENT_IVS_TRAFFICJUNCTION://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_TRAFFICJUNCTION_INFO);

			DEV_EVENT_TRAFFICJUNCTION_INFO* pInfo = (DEV_EVENT_TRAFFICJUNCTION_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strFileIndex =Int2Str(pInfo->stuFileInfo.bIndex);
			m_pEventInfo->stuEventInfoToDisplay.strFileCount =Int2Str(pInfo->stuFileInfo.bCount);
			m_pEventInfo->stuEventInfoToDisplay.strGroupId = Int2Str(pInfo->stuFileInfo.nGroupId);				

			m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stTrafficCar.szPlateNumber ;
			m_pEventInfo->stuEventInfoToDisplay.strPlateColor = pInfo->stTrafficCar.szPlateColor;
			m_pEventInfo->stuEventInfoToDisplay.strPlateType = pInfo->stTrafficCar.szPlateType;
			m_pEventInfo->stuEventInfoToDisplay.strVehicleType = pInfo->stuVehicle.szObjectSubType;			
			m_pEventInfo->stuEventInfoToDisplay.strVehicleColor = pInfo->stTrafficCar.szVehicleColor;

			m_pEventInfo->stuEventInfoToDisplay.strVehicleSize = VehicleSizeToString(pInfo->stTrafficCar.nVehicleSize);
			m_pEventInfo->stuEventInfoToDisplay.strLane  = pInfo->stTrafficCar.szCustomParkNo;

			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);

			m_pEventInfo->strDeviceAddress = (NULL == pInfo->stTrafficCar.szDeviceAddress) ?  "" : pInfo->stTrafficCar.szDeviceAddress;

			m_pEventInfo->rectBoundingBox = pInfo->stuObject.BoundingBox;

			m_pEventInfo->dwOffset		= pInfo->stuObject.stPicInfo.dwOffSet;
			m_pEventInfo->dwOffsetLength = pInfo->stuObject.stPicInfo.dwFileLenth;
		}
		break;
	case EVENT_IVS_SMOKEDETECTION://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_SMOKE_INFO);

			DEV_EVENT_SMOKE_INFO* pInfo = (DEV_EVENT_SMOKE_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strFileIndex =Int2Str(pInfo->stuFileInfo.bIndex);
			m_pEventInfo->stuEventInfoToDisplay.strFileCount =Int2Str(pInfo->stuFileInfo.bCount);
			m_pEventInfo->stuEventInfoToDisplay.strGroupId = Int2Str(pInfo->stuFileInfo.nGroupId);				

			m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stTrafficCar.szPlateNumber ;
			m_pEventInfo->stuEventInfoToDisplay.strPlateColor = pInfo->stTrafficCar.szPlateColor;
			m_pEventInfo->stuEventInfoToDisplay.strPlateType = pInfo->stTrafficCar.szPlateType;
			m_pEventInfo->stuEventInfoToDisplay.strVehicleType = pInfo->stuVehicle.szObjectSubType;			
			m_pEventInfo->stuEventInfoToDisplay.strVehicleColor = pInfo->stTrafficCar.szVehicleColor;

			m_pEventInfo->stuEventInfoToDisplay.strVehicleSize = VehicleSizeToString(pInfo->stTrafficCar.nVehicleSize);
			m_pEventInfo->stuEventInfoToDisplay.strLane  = pInfo->stTrafficCar.szCustomParkNo;

			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);

			m_pEventInfo->strDeviceAddress = (NULL == pInfo->stTrafficCar.szDeviceAddress) ?  "" : pInfo->stTrafficCar.szDeviceAddress;

			m_pEventInfo->rectBoundingBox = pInfo->stuObject.BoundingBox;

			m_pEventInfo->dwOffset		= pInfo->stuObject.stPicInfo.dwOffSet;
			m_pEventInfo->dwOffsetLength = pInfo->stuObject.stPicInfo.dwFileLenth;
		}
		break;
	case EVENT_IVS_FIREDETECTION://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_FIRE_INFO);

			DEV_EVENT_FIRE_INFO* pInfo = (DEV_EVENT_FIRE_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strFileIndex =Int2Str(pInfo->stuFileInfo.bIndex);
			m_pEventInfo->stuEventInfoToDisplay.strFileCount =Int2Str(pInfo->stuFileInfo.bCount);
			m_pEventInfo->stuEventInfoToDisplay.strGroupId = Int2Str(pInfo->stuFileInfo.nGroupId);				

			m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stTrafficCar.szPlateNumber ;
			m_pEventInfo->stuEventInfoToDisplay.strPlateColor = pInfo->stTrafficCar.szPlateColor;
			m_pEventInfo->stuEventInfoToDisplay.strPlateType = pInfo->stTrafficCar.szPlateType;
			m_pEventInfo->stuEventInfoToDisplay.strVehicleType = pInfo->stuVehicle.szObjectSubType;			
			m_pEventInfo->stuEventInfoToDisplay.strVehicleColor = pInfo->stTrafficCar.szVehicleColor;

			m_pEventInfo->stuEventInfoToDisplay.strVehicleSize = VehicleSizeToString(pInfo->stTrafficCar.nVehicleSize);
			m_pEventInfo->stuEventInfoToDisplay.strLane  = pInfo->stTrafficCar.szCustomParkNo;

			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);

			m_pEventInfo->strDeviceAddress = (NULL == pInfo->stTrafficCar.szDeviceAddress) ?  "" : pInfo->stTrafficCar.szDeviceAddress;

			m_pEventInfo->rectBoundingBox = pInfo->stuObject.BoundingBox;

			m_pEventInfo->dwOffset		= pInfo->stuObject.stPicInfo.dwOffSet;
			m_pEventInfo->dwOffsetLength = pInfo->stuObject.stPicInfo.dwFileLenth;
		}
		break;
	case EVENT_IVS_CITY_MOTORPARKING://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_CITY_MOTORPARKING_INFO);

			DEV_EVENT_CITY_MOTORPARKING_INFO* pInfo = (DEV_EVENT_CITY_MOTORPARKING_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strFileIndex =Int2Str(pInfo->stuFileInfo.bIndex);
			m_pEventInfo->stuEventInfoToDisplay.strFileCount =Int2Str(pInfo->stuFileInfo.bCount);
			m_pEventInfo->stuEventInfoToDisplay.strGroupId = Int2Str(pInfo->stuFileInfo.nGroupId);				

			/*m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stTrafficCar.szPlateNumber ;
			m_pEventInfo->stuEventInfoToDisplay.strPlateColor = pInfo->stTrafficCar.szPlateColor;
			m_pEventInfo->stuEventInfoToDisplay.strPlateType = pInfo->stTrafficCar.szPlateType;
			m_pEventInfo->stuEventInfoToDisplay.strVehicleType = pInfo->stuVehicle.szObjectSubType;			
			m_pEventInfo->stuEventInfoToDisplay.strVehicleColor = pInfo->stTrafficCar.szVehicleColor;

			m_pEventInfo->stuEventInfoToDisplay.strVehicleSize = VehicleSizeToString(pInfo->stTrafficCar.nVehicleSize);
			m_pEventInfo->stuEventInfoToDisplay.strLane  = Int2Str(pInfo->stTrafficCar.szCustomParkNo);*/

			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);

			//m_pEventInfo->strDeviceAddress = (NULL == pInfo->stTrafficCar.szDeviceAddress) ?  "" : pInfo->stTrafficCar.szDeviceAddress;

			//m_pEventInfo->rectBoundingBox = pInfo->stuObject.BoundingBox;

			//m_pEventInfo->dwOffset		= pInfo->stuObject.stPicInfo.dwOffSet;
			//m_pEventInfo->dwOffsetLength = pInfo->stuObject.stPicInfo.dwFileLenth;
		}
		break;
	case EVENT_IVS_CITY_NONMOTORPARKING://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_CITY_NONMOTORPARKING_INFO);

			DEV_EVENT_CITY_NONMOTORPARKING_INFO* pInfo = (DEV_EVENT_CITY_NONMOTORPARKING_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strFileIndex =Int2Str(pInfo->stuFileInfo.bIndex);
			m_pEventInfo->stuEventInfoToDisplay.strFileCount =Int2Str(pInfo->stuFileInfo.bCount);
			m_pEventInfo->stuEventInfoToDisplay.strGroupId = Int2Str(pInfo->stuFileInfo.nGroupId);				

			/*m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stTrafficCar.szPlateNumber ;
			m_pEventInfo->stuEventInfoToDisplay.strPlateColor = pInfo->stTrafficCar.szPlateColor;
			m_pEventInfo->stuEventInfoToDisplay.strPlateType = pInfo->stTrafficCar.szPlateType;
			m_pEventInfo->stuEventInfoToDisplay.strVehicleType = pInfo->stuVehicle.szObjectSubType;			
			m_pEventInfo->stuEventInfoToDisplay.strVehicleColor = pInfo->stTrafficCar.szVehicleColor;

			m_pEventInfo->stuEventInfoToDisplay.strVehicleSize = VehicleSizeToString(pInfo->stTrafficCar.nVehicleSize);
			m_pEventInfo->stuEventInfoToDisplay.strLane  = Int2Str(pInfo->stTrafficCar.szCustomParkNo);*/

			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);

			//m_pEventInfo->strDeviceAddress = (NULL == pInfo->stTrafficCar.szDeviceAddress) ?  "" : pInfo->stTrafficCar.szDeviceAddress;

			//m_pEventInfo->rectBoundingBox = pInfo->stuObject.BoundingBox;

			//m_pEventInfo->dwOffset		= pInfo->stuObject.stPicInfo.dwOffSet;
			//m_pEventInfo->dwOffsetLength = pInfo->stuObject.stPicInfo.dwFileLenth;
		}
		break;
	case EVENT_ALARM_MOTIONDETECT://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_ALARM_INFO);

			DEV_EVENT_ALARM_INFO* pInfo = (DEV_EVENT_ALARM_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strFileIndex =Int2Str(pInfo->stuFileInfo.bIndex);
			m_pEventInfo->stuEventInfoToDisplay.strFileCount =Int2Str(pInfo->stuFileInfo.bCount);
			m_pEventInfo->stuEventInfoToDisplay.strGroupId = Int2Str(pInfo->stuFileInfo.nGroupId);				

			/*m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stTrafficCar.szPlateNumber ;
			m_pEventInfo->stuEventInfoToDisplay.strPlateColor = pInfo->stTrafficCar.szPlateColor;
			m_pEventInfo->stuEventInfoToDisplay.strPlateType = pInfo->stTrafficCar.szPlateType;
			m_pEventInfo->stuEventInfoToDisplay.strVehicleType = pInfo->stuVehicle.szObjectSubType;			
			m_pEventInfo->stuEventInfoToDisplay.strVehicleColor = pInfo->stTrafficCar.szVehicleColor;

			m_pEventInfo->stuEventInfoToDisplay.strVehicleSize = VehicleSizeToString(pInfo->stTrafficCar.nVehicleSize);
			m_pEventInfo->stuEventInfoToDisplay.strLane  = Int2Str(pInfo->stTrafficCar.szCustomParkNo);*/

			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);

			//m_pEventInfo->strDeviceAddress = (NULL == pInfo->stTrafficCar.szDeviceAddress) ?  "" : pInfo->stTrafficCar.szDeviceAddress;

			//m_pEventInfo->rectBoundingBox = pInfo->stuObject.BoundingBox;

			//m_pEventInfo->dwOffset		= pInfo->stuObject.stPicInfo.dwOffSet;
			//m_pEventInfo->dwOffsetLength = pInfo->stuObject.stPicInfo.dwFileLenth;
		}
		break;
	case EVENT_IVS_FOREIGN_OBJECT_OCCUPY_SPACE://
		{
			m_pEventInfo->nStructSize = sizeof(NET_DEV_EVENT_FOREIGN_OBJECT_OCCUPY_SPACE_INFO);

			NET_DEV_EVENT_FOREIGN_OBJECT_OCCUPY_SPACE_INFO* pInfo = (NET_DEV_EVENT_FOREIGN_OBJECT_OCCUPY_SPACE_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->stuUTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			//m_pEventInfo->stuEventInfoToDisplay.strFileIndex =Int2Str(pInfo->stuFileInfo.bIndex);
			//m_pEventInfo->stuEventInfoToDisplay.strFileCount =Int2Str(pInfo->stuFileInfo.bCount);
			//m_pEventInfo->stuEventInfoToDisplay.strGroupId = Int2Str(pInfo->stuFileInfo.nGroupId);				

			m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stuTrafficCar.szPlateNumber ;
			m_pEventInfo->stuEventInfoToDisplay.strPlateColor = pInfo->stuTrafficCar.szPlateColor;
			m_pEventInfo->stuEventInfoToDisplay.strPlateType = pInfo->stuTrafficCar.szPlateType;
			//m_pEventInfo->stuEventInfoToDisplay.strVehicleType = pInfo->stuTrafficCar.szObjectSubType;			
			m_pEventInfo->stuEventInfoToDisplay.strVehicleColor = pInfo->stuTrafficCar.szVehicleColor;

			m_pEventInfo->stuEventInfoToDisplay.strVehicleSize = VehicleSizeToString(pInfo->stuTrafficCar.nVehicleSize);
			m_pEventInfo->stuEventInfoToDisplay.strLane  = pInfo->stuTrafficCar.szCustomParkNo;

			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->stuUTC);

			m_pEventInfo->strDeviceAddress = (NULL == pInfo->stuTrafficCar.szDeviceAddress) ?  "" : pInfo->stuTrafficCar.szDeviceAddress;

			//m_pEventInfo->rectBoundingBox = pInfo->stuObject.BoundingBox;

			//m_pEventInfo->dwOffset		= pInfo->stuObject.stPicInfo.dwOffSet;
			//m_pEventInfo->dwOffsetLength = pInfo->stuObject.stPicInfo.dwFileLenth;
		}
		break;
	case EVENT_IVS_ROADOCCUPATION_BY_FOREIGNOBJECT://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_ROADOCCUPATION_BY_FOREIGNOBJECT_INFO);
			DEV_EVENT_ROADOCCUPATION_BY_FOREIGNOBJECT_INFO* pInfo = (DEV_EVENT_ROADOCCUPATION_BY_FOREIGNOBJECT_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strViolationSnapSource  = ViolationSnapSourceToString(pInfo->nViolationSnapSource);
			m_pEventInfo->stuEventInfoToDisplay.strDetectRegionName  = pInfo->szDetectRegionName;
			m_pEventInfo->stuEventInfoToDisplay.strObjectType  = ConvertString(pInfo->szObjectType);
			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);
		}
		break;
	case EVENT_IVS_TRAFFICJAM://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_TRAFFICJAM_INFO);
			DEV_EVENT_TRAFFICJAM_INFO* pInfo = (DEV_EVENT_TRAFFICJAM_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strViolationSnapSource  = ViolationSnapSourceToString(pInfo->nViolationSnapSource);
			m_pEventInfo->stuEventInfoToDisplay.strDetectRegionName  = pInfo->szDetectRegionName;
			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);
		}
		break;
	case EVENT_IVS_WANDERDETECTION://
		{
			m_pEventInfo->nStructSize = sizeof(DEV_EVENT_WANDER_INFO);
			DEV_EVENT_WANDER_INFO* pInfo = (DEV_EVENT_WANDER_INFO*)pAlarmInfo;
			m_pEventInfo->stuEventInfoToDisplay.strCountNum = "0";
			m_pEventInfo->stuEventInfoToDisplay.strTime = NetTimeEx2Str(pInfo->UTC);
			m_pEventInfo->stuEventInfoToDisplay.strEventType = EventType2Str(dwAlarmType);
			m_pEventInfo->stuEventInfoToDisplay.strViolationSnapSource  = ViolationSnapSourceToString(pInfo->nViolationSnapSource);
			m_pEventInfo->stuEventInfoToDisplay.strDetectRegionName  = pInfo->szDetectRegionName;
			int nPlateNum = 0;
			for(int i = 0;i<pInfo->nObjectNum;i++)
			{
				if(strlen(pInfo->stuObjectIDs[i].szObjectType) > 0)
				{
					//m_pEventInfo->stuEventInfoToDisplay.strObjectType += ConvertString(pInfo->stuObjectIDs[i].szObjectType);
					if(strcmp(pInfo->stuObjectIDs[i].szObjectType,"Plate") == 0)
					{
						if(nPlateNum > 0)
						{
							m_pEventInfo->stuEventInfoToDisplay.strPlateNumber += "; ";
							m_pEventInfo->stuEventInfoToDisplay.strPlateNumber += pInfo->stuObjectIDs[i].szText;
						}
						else
						{
							m_pEventInfo->stuEventInfoToDisplay.strPlateNumber = pInfo->stuObjectIDs[i].szText;
						}
						nPlateNum++;
					}
				}
			}
			m_pEventInfo->strUTCTime = NetTimeEx2StrEx(pInfo->UTC);
		}
		break;
	default:
		return false;
	}			
	m_pAlarmData->lAnalyzerHadnle = lAnalyzerHandle;
	m_pAlarmData->dwAlarmType = dwAlarmType;

	m_pAlarmData->pAlarmInfo = NEW char[m_pEventInfo->nStructSize];
	memcpy(m_pAlarmData->pAlarmInfo, pAlarmInfo, m_pEventInfo->nStructSize);

	m_pAlarmData->pPictureBuffer = NEW BYTE[dwBufSize];
	memcpy(m_pAlarmData->pPictureBuffer, pBuffer, dwBufSize);
	m_pAlarmData->dwPictureBufferSize = dwBufSize;
	m_pAlarmData->nSequence = nSequence;


	std::string strUTCTime = m_pEventInfo->strUTCTime;
	GUID guid = {0};
	HRESULT hr =  CoCreateGuid(&guid);
	m_strGuid = strUTCTime;
	m_strGuid.append(GuidToString(guid));	
	return true;

}

BYTE* CAlaramImpl::GetPictureInfo(DWORD& dwPictureBufferSize)
{
	dwPictureBufferSize = m_pAlarmData->dwPictureBufferSize;
	return m_pAlarmData->pPictureBuffer;
}

void CAlaramImpl::DestroyParam()
{
	if (m_pAlarmData)
	{
		if (m_pAlarmData->pPictureBuffer)
		{
			delete[] m_pAlarmData->pPictureBuffer;
			m_pAlarmData->pPictureBuffer = NULL;
		}

		if (m_pAlarmData->pAlarmInfo)
		{
			delete[] m_pAlarmData->pAlarmInfo;
			m_pAlarmData->pAlarmInfo = NULL;
		}

		delete m_pAlarmData;
		m_pAlarmData = NULL;
	}

	if (m_pEventInfo)
	{
		delete m_pEventInfo;
		m_pEventInfo = NULL;
	}
}

void CAlaramImpl::GetEventInfo(StuEventInfo& stuEventInfo)
{
	stuEventInfo = *m_pEventInfo;
	//return *m_pEventInfo;
}

std::string		CAlaramImpl::GetGuid() const
{
	return m_strGuid;
}


CAlaram::CAlaram(void)
{
	m_pAlarmImpl = NEW CAlaramImpl;
}

CAlaram::~CAlaram(void)
{
	if (m_pAlarmImpl)
	{
		delete m_pAlarmImpl;
		m_pAlarmImpl = NULL;
	}
}

bool CAlaram::SetAlarmData(LLONG lAnalyzerHandle, DWORD dwAlarmType, void* pAlarmInfo, BYTE* pBuffer, DWORD dwBufSize, int nSequence)
{
	if (m_pAlarmImpl)
	{
		return m_pAlarmImpl->SetAlarmData(lAnalyzerHandle, dwAlarmType, pAlarmInfo, pBuffer, dwBufSize, nSequence);
	}
	return false;
}

BYTE* CAlaram::GetPictureInfo( DWORD& dwPictureBufferSize)
{
	if (m_pAlarmImpl)
	{
		return m_pAlarmImpl->GetPictureInfo(dwPictureBufferSize);
	}
	return NULL;
}

void CAlaram::DestroyParam()
{
	if (m_pAlarmImpl)
	{
		m_pAlarmImpl->DestroyParam();
	}
}

void CAlaram::GetEventInfo(StuEventInfo& stuEventInfo)
{
	m_pAlarmImpl->GetEventInfo(stuEventInfo);
}

std::string		CAlaram::GetGuid() const
{
	if (m_pAlarmImpl)
	{
		return m_pAlarmImpl->GetGuid();
	}
	return "";
}