#ifndef	_PARKING_SPACE_UTILS_H
#define _PARKING_SPACE_UTILS_H

#include <vector>
#include <string>
#include "Utility.h"

#define CFG_SECTION_NAME			"INFO"
#define	CFG_KEY_NAME_ALARM_TYPE		"alarm_type"
#define	CFG_KEY_NAME_ALARM_INFO		"alarm_info"
#define	PC_DATA_STORE_FOLDER		"PCStore\\"
#define PC_DATA_STORE_FILE			"cfg.ini"
#define CFG_CFG_FILE_NAME			"Config.ini"
#define CFG_SECTION_CFG				"CFG"
#define CFG_KEY_DATA_PATH			"DATA_PATH"
#define MAX_GUID_LEN 55

#define GET_STRUCT_SIZE			0x01
#define GET_EVENT_CONTENT		0x02
#define GET_DEVICE_ADDRESS		0x04
#define GET_PICTURE_RECT		0x08
#define GET_PLATE_PICTURE_INFO	0x10

const COLORREF RESERVED_COLOR = RGB(255, 255, 255);

typedef struct __COL_DES
{
	std::string strColTitle; // title of column
	int			nColWidth;   // width of column
}ColDes;

inline ColDes ColDesObj(char* szColTitle, const int nColWidth)
{
	ColDes colDes;
	colDes.strColTitle = ConvertStr(szColTitle);
	colDes.nColWidth = nColWidth;
	return colDes;
}

struct EVENT_INFO_DISPLAY
{
	std::string strCountNum;			// count num
	std::string strTime;				// time
	std::string strEventType;			// event type

	std::string strFileIndex;			// file index
	std::string strFileCount;			// file count
	std::string strGroupId;				// group id

	std::string strPlateNumber;			// plate text 
	std::string strPlateColor;			// plate color
	std::string strPlateType;			// plate type

	std::string	strVehicleType;			// vehicle type
	std::string strVehicleColor;		// vehicle color
	std::string strVehicleSize;			// vehicle size

	std::string strLane;				// lane number	
	std::string strDeviceAddress;		// device address

	std::string strObjectType;			// Object Type
	std::string strDetectRegionName;	// Detect Region Name
	std::string strViolationSnapSource;	// Violation Snap Source

	EVENT_INFO_DISPLAY() :strCountNum(""),
		strTime(""),
		strEventType(""),
		strFileIndex(""),	
		strFileCount(""),	
		strGroupId(""),	
		strPlateNumber(""),
		strPlateColor(""),
		strPlateType(""),
		strVehicleType(""),
		strVehicleColor(""),
		strVehicleSize(""),
		strLane(""),
		strObjectType(""),
		strDetectRegionName(""),
		strViolationSnapSource(""),
		strDeviceAddress(""){}
};

const CString strEventList[] = {
	"Show All",
	"ParkingSpaceParking",
	"ParkingSpaceNoParking",
	"TrafficJunction",
	"SmokeDetection",
	"FireDetection",
	"CityMotorParking",
	"CityNonMotorParking",
	"VideoMotion",
	"ForeignObjectOccupySpace",
	"RoadOccupationByForeignObject",
	"TrafficJam",
	"WanderDetection"
};
const int nEventTypeList[]={
	EVENT_IVS_ALL, 
	EVENT_IVS_TRAFFIC_PARKINGSPACEPARKING,
	EVENT_IVS_TRAFFIC_PARKINGSPACENOPARKING,
	EVENT_IVS_TRAFFICJUNCTION,
	EVENT_IVS_SMOKEDETECTION,
	EVENT_IVS_FIREDETECTION,
	EVENT_IVS_CITY_MOTORPARKING,
	EVENT_IVS_CITY_NONMOTORPARKING,
	EVENT_ALARM_MOTIONDETECT,
	EVENT_IVS_FOREIGN_OBJECT_OCCUPY_SPACE,
	EVENT_IVS_ROADOCCUPATION_BY_FOREIGNOBJECT,
	EVENT_IVS_TRAFFICJAM,
	EVENT_IVS_WANDERDETECTION
};

/////////////////////////////////////////////////////////////////////////////
struct StuEventInfo
{
	int					nStructSize;			// event struct size
	EVENT_INFO_DISPLAY  stuEventInfoToDisplay;	// Event info to display
	std::string			strDeviceAddress;		// event address
	DH_RECT				rectBoundingBox;		
	DWORD				dwOffset;
	DWORD				dwOffsetLength;
	std::string			strUTCTime;

	StuEventInfo() : nStructSize(0), 
		strDeviceAddress(""), 
		dwOffset(0), 
		dwOffsetLength(0), 
		strUTCTime("")
		{
			rectBoundingBox.bottom = 0;
			rectBoundingBox.left = 0;
			rectBoundingBox.right = 0;
			rectBoundingBox.top = 0;
		}
};


#endif	//_PARKING_SPACE_UTILS_H