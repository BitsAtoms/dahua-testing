// ParkingspacelightstateDlg.cpp : 实现文件
//

#include "stdafx.h"
#include "ParkingSpaceDetector.h"
#include "ParkingspacelightstateDlg.h"
#include "Controller.h"


// ParkingspacelightstateDlg 对话框
CString g_LightColor[] = {"Red", "Yellow", "Blue", "Green", "Purple", "White", "Pink","Cyan", "Close",""};

IMPLEMENT_DYNAMIC(ParkingspacelightstateDlg, CDialog)

ParkingspacelightstateDlg::ParkingspacelightstateDlg(CController* pCtl, CWnd* pParent /*=NULL*/)
	: CDialog(ParkingspacelightstateDlg::IDD, pParent)
	,m_pCtl(pCtl)
{
	memset(&m_stuParkLightState, 0, sizeof(m_stuParkLightState));
	m_stuParkLightState.dwSize = sizeof(m_stuParkLightState);

	memset(&m_stuParkLightState.stuSpaceFreeInfo, 0, sizeof(m_stuParkLightState.stuSpaceFreeInfo));
	memset(&m_stuParkLightState.stuSpaceFullInfo, 0, sizeof(m_stuParkLightState.stuSpaceFullInfo));
	memset(&m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[0], 0, sizeof(m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[0]));
	memset(&m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[1], 0, sizeof(m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[1]));
	memset(&m_stuParkLightState.stuAbnormalAlarmInfo.stuSmokeFire, 0, sizeof(m_stuParkLightState.stuAbnormalAlarmInfo.stuSmokeFire));
	memset(&m_stuParkLightState.stuSpaceAlarmInfo, 0, sizeof(m_stuParkLightState.stuSpaceAlarmInfo));
	m_stuParkLightState.stuSpaceFreeInfo.nLightKeepTime = -1;
	m_stuParkLightState.stuSpaceFullInfo.nLightKeepTime = -1;
	m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[0].nLightKeepTime = -1;
	m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[1].nLightKeepTime = -1;
	//m_stuParkLightState.stuAbnormalAlarmInfo.stuSmokeFire.nLightKeepTime = -1;
	m_stuParkLightState.stuSpaceAlarmInfo.nLightKeepTime = 60;
}

ParkingspacelightstateDlg::~ParkingspacelightstateDlg()
{
}

void ParkingspacelightstateDlg::DoDataExchange(CDataExchange* pDX)
{
	CDialog::DoDataExchange(pDX);
	DDX_Control(pDX, IDC_COMBO_SPACEFREE, m_combo_spacefree);
	DDX_Control(pDX, IDC_COMBO_SPACEFULL, m_combo_spacefull);
	DDX_Control(pDX, IDC_COMBO_NETPORT1ABORT, m_combo_netport1abort);
	DDX_Control(pDX, IDC_COMBO_NETPORT2ABORT, m_combo_netport2abort);
	DDX_Control(pDX, IDC_COMBO_FIREDETECTION, m_combo_firedetection);
	DDX_Control(pDX, IDC_COMBO_ABNORMALALARM, m_combo_abnormalalarm);
	DDX_Control(pDX, IDC_COMBO_SPACEFREE2, m_combo_spacefree1);
	DDX_Control(pDX, IDC_COMBO_SPACEFULL2, m_combo_spacefull1);
	DDX_Control(pDX, IDC_COMBO_FIREDETECTION2, m_combo_firedetection1);
	DDX_Control(pDX, IDC_COMBO_ABNORMALALARM2, m_combo_abnormalalarm1);
}


BEGIN_MESSAGE_MAP(ParkingspacelightstateDlg, CDialog)
	ON_BN_CLICKED(IDC_BUTTON_SETSTATE, &ParkingspacelightstateDlg::OnBnClickedButtonSetstate)
	ON_BN_CLICKED(IDC_BUTTON_GETSTATE, &ParkingspacelightstateDlg::OnBnClickedButtonGetstate)
END_MESSAGE_MAP()


// ParkingspacelightstateDlg 消息处理程序
BOOL ParkingspacelightstateDlg::OnInitDialog()
{
	CDialog::OnInitDialog();
	g_SetWndStaticText(this);

	getConfig();
	InitControl();
	showDataToControl();

	return TRUE;  // return TRUE unless you set the focus to a control
	// 异常: OCX 属性页应返回 FALSE
}

void ParkingspacelightstateDlg::InitControl()
{
	for (int i = 0; i < sizeof(g_LightColor) / sizeof(g_LightColor[0]); i++)
	{
		m_combo_spacefree.AddString(ConvertString(g_LightColor[i]));
		m_combo_spacefull.AddString(ConvertString(g_LightColor[i]));
		m_combo_netport1abort.AddString(ConvertString(g_LightColor[i]));
		m_combo_netport2abort.AddString(ConvertString(g_LightColor[i]));
		m_combo_firedetection.AddString(ConvertString(g_LightColor[i]));
		m_combo_abnormalalarm.AddString(ConvertString(g_LightColor[i]));
		m_combo_spacefree1.AddString(ConvertString(g_LightColor[i]));
		m_combo_spacefull1.AddString(ConvertString(g_LightColor[i]));
		m_combo_firedetection1.AddString(ConvertString(g_LightColor[i]));
		m_combo_abnormalalarm1.AddString(ConvertString(g_LightColor[i]));
	}
}

void ParkingspacelightstateDlg::OnBnClickedButtonSetstate()
{
	getDataFromControl();
	setConfig();
}

void ParkingspacelightstateDlg::OnBnClickedButtonGetstate()
{
	getConfig();
	showDataToControl();

}

void ParkingspacelightstateDlg::showDataToControl()
{
	setComboBoxInfoFromLightInfo(m_stuParkLightState.stuSpaceFreeInfo, &m_combo_spacefree, &m_combo_spacefree1, IDC_CHECK_FREE,1);
	setComboBoxInfoFromLightInfo(m_stuParkLightState.stuSpaceFullInfo, &m_combo_spacefull, &m_combo_spacefull1,  IDC_CHECK_FULL,1);
	setComboBoxInfoFromLightInfo(m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[0], &m_combo_netport1abort, IDC_CHECK_SIGNAL);
	setComboBoxInfoFromLightInfo(m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[1], &m_combo_netport2abort, IDC_CHECK_DOUBLE);
	setComboBoxInfoFromLightInfo(m_stuParkLightState.stuAbnormalAlarmInfo.stuSmokeFire, &m_combo_firedetection,&m_combo_firedetection1, IDC_CHECK_FIRE);
	setComboBoxInfoFromLightInfo(m_stuParkLightState.stuSpaceAlarmInfo, &m_combo_abnormalalarm, &m_combo_abnormalalarm1,IDC_CHECK_ALARM);
}

void ParkingspacelightstateDlg::setComboBoxInfoFromLightInfo(NET_PARKINGSPACELIGHT_INFO lightInfo, CComboBox *comBox,int nCheckID)
{	
	BOOL bFlag = FALSE;
	int nEditCheckID = 0;
	switch(nCheckID)
	{
		case IDC_CHECK_FREE:
			nEditCheckID = IDC_EDIT_FREE;
			bFlag = TRUE;
			break;
		case IDC_CHECK_FULL:
			nEditCheckID = IDC_EDIT_FULL;
			bFlag = TRUE;
			break;
		case IDC_CHECK_FIRE:
			nEditCheckID = IDC_EDIT_FIRE;
			bFlag = TRUE;
			break;
		case IDC_CHECK_ALARM:
			nEditCheckID = IDC_EDIT_ALARM;
			bFlag = TRUE;
			break;
		default:
			break;
	}
	
	if(bFlag)
	{
		 // 获取编辑控件的指针
	    CEdit* pEdit = (CEdit*)GetDlgItem(nEditCheckID);
	    
	    // 将整数转换为 CString
	    CString strValue;
		
		strValue.Format(_T("%d"), lightInfo.nLightKeepTime); // 将整数格式化为字符串

	    // 将字符串设置到编辑控件中
	    pEdit->SetWindowText(strValue);
	}
	/*if (lightInfo.nRed >= 1)
	{
		int nCheck = lightInfo.nRed - 1;	
		((CButton*)GetDlgItem(nCheckID))->SetCheck(nCheck);
		
		comBox->SetCurSel(0);
		return ;
	}
	if (lightInfo.nYellow >= 1)
	{
		int nCheck = lightInfo.nYellow - 1;	
		((CButton*)GetDlgItem(nCheckID))->SetCheck(nCheck);
		
		comBox->SetCurSel(1);
		return ;
	}
	if (lightInfo.nBlue >= 1)
	{
		int nCheck = lightInfo.nBlue - 1;	
		((CButton*)GetDlgItem(nCheckID))->SetCheck(nCheck);

		comBox->SetCurSel(2);
		return ;
	}
	if (lightInfo.nGreen >= 1)
	{
		int nCheck = lightInfo.nGreen - 1;	
		((CButton*)GetDlgItem(nCheckID))->SetCheck(nCheck);

		comBox->SetCurSel(3);
		return ;
	}
	if (lightInfo.nPurple >= 1)
	{
		int nCheck = lightInfo.nPurple - 1;	
		((CButton*)GetDlgItem(nCheckID))->SetCheck(nCheck);

		comBox->SetCurSel(4);
		return ;
	}
	if (lightInfo.nWhite >= 1)
	{
		int nCheck = lightInfo.nWhite - 1;	
		((CButton*)GetDlgItem(nCheckID))->SetCheck(nCheck);

		comBox->SetCurSel(5);
		return ;
	}
	if (lightInfo.nPink >= 1)
	{
		int nCheck = lightInfo.nPink - 1;	
		((CButton*)GetDlgItem(nCheckID))->SetCheck(nCheck);

		comBox->SetCurSel(6);
		return ;
	}
	if (lightInfo.nColor[0] == 9)
	{
		if(lightInfo.nColor[1] == 1)
		{
			((CButton*)GetDlgItem(nCheckID))->SetCheck(1);
		}

		comBox->SetCurSel(7);
		return ;
	}*/

	
	switch (lightInfo.nColor[0])
	{
		case 1:
			comBox->SetCurSel(8);
			break;
		case 2:
			comBox->SetCurSel(0);
			break;
		case 3:
			comBox->SetCurSel(1);
			break;
		case 4:
			comBox->SetCurSel(2);
			break;
		case 5:
			comBox->SetCurSel(3);
			break;
		case 6:
			comBox->SetCurSel(4);
			break;
		case 7:
			comBox->SetCurSel(5);
			break;
		case 8:
			comBox->SetCurSel(6);
			break;
		case 9:
			comBox->SetCurSel(7);
			break;
		default:
			comBox->SetCurSel(9);
			break;
	}

	if(lightInfo.nColor[0] == 0 ||lightInfo.nColor[0] == 1)
	{
		((CButton*)GetDlgItem(nCheckID))->SetCheck(0);
	}
	else if(lightInfo.nColor[1] == 0)
	{
		((CButton*)GetDlgItem(nCheckID))->SetCheck(0);
	}
	else
	{
		((CButton*)GetDlgItem(nCheckID))->SetCheck(1);
	}

	if(lightInfo.nLightKeepTime == 0 )
	{
		lightInfo.nLightKeepTime = -1;
	}

	
	//comBox->SetCurSel(9);
	//((CButton*)GetDlgItem(nCheckID))->SetCheck(0);

}


void ParkingspacelightstateDlg::setComboBoxInfoFromLightInfo(NET_PARKINGSPACELIGHT_INFO lightInfo, CComboBox *comBox, CComboBox *comBox1,int nCheckID,int flag/*=0*/)
{	
	BOOL bFlag = FALSE;
	int nEditCheckID = 0;
	switch(nCheckID)
	{
		case IDC_CHECK_FREE:
			nEditCheckID = IDC_EDIT_FREE;
			bFlag = TRUE;
			break;
		case IDC_CHECK_FULL:
			nEditCheckID = IDC_EDIT_FULL;
			bFlag = TRUE;
			break;
		case IDC_CHECK_FIRE:
			nEditCheckID = IDC_EDIT_FIRE;
			bFlag = TRUE;
			break;
		case IDC_CHECK_ALARM:
			nEditCheckID = IDC_EDIT_ALARM;
			bFlag = TRUE;
			break;
		default:
			break;
	}

	if(lightInfo.nLightKeepTime == 0 && flag == 1)
	{
		lightInfo.nLightKeepTime = -1;
	}

	if(bFlag)
	{
		 // 获取编辑控件的指针
	    CEdit* pEdit = (CEdit*)GetDlgItem(nEditCheckID);
	    
	    // 将整数转换为 CString
	    CString strValue;
	    strValue.Format(_T("%d"), lightInfo.nLightKeepTime); // 将整数格式化为字符串

	    // 将字符串设置到编辑控件中
	    pEdit->SetWindowText(strValue);
	}

	switch (lightInfo.nColor[0])
	{
		case 1:
			comBox->SetCurSel(8);
			break;
		case 2:
			comBox->SetCurSel(0);
			break;
		case 3:
			comBox->SetCurSel(1);
			break;
		case 4:
			comBox->SetCurSel(2);
			break;
		case 5:
			comBox->SetCurSel(3);
			break;
		case 6:
			comBox->SetCurSel(4);
			break;
		case 7:
			comBox->SetCurSel(5);
			break;
		case 8:
			comBox->SetCurSel(6);
			break;
		case 9:
			comBox->SetCurSel(7);
			break;
		default:
			comBox->SetCurSel(9);
			break;
	}

	switch (lightInfo.nColor[1])
	{
		case 1:
			comBox->SetCurSel(8);
			break;
		case 2:
			comBox1->SetCurSel(0);
			break;
		case 3:
			comBox1->SetCurSel(1);
			break;
		case 4:
			comBox1->SetCurSel(2);
			break;
		case 5:
			comBox1->SetCurSel(3);
			break;
		case 6:
			comBox1->SetCurSel(4);
			break;
		case 7:
			comBox1->SetCurSel(5);
			break;
		case 8:
			comBox1->SetCurSel(6);
			break;
		case 9:
			comBox1->SetCurSel(7);
			break;
		default:
			comBox1->SetCurSel(9);
			break;
	}
	
	if(lightInfo.nColor[0] == 0 ||lightInfo.nColor[0] == 1)
	{
		((CButton*)GetDlgItem(nCheckID))->SetCheck(0);
	}
	else if(lightInfo.nColor[1] == 0)
	{
		((CButton*)GetDlgItem(nCheckID))->SetCheck(0);
	}
	else
	{
		((CButton*)GetDlgItem(nCheckID))->SetCheck(1);
	}

}

void ParkingspacelightstateDlg::getDataFromControl()
{	
	//m_stuParkLightState.dwSize = sizeof(m_stuParkLightState);

	m_stuParkLightState.stuNetWorkExceptionInfo.nRetNetPortAbortNum = 2;	//At present, the device only supports dual network ports

	/*memset(&m_stuParkLightState.stuSpaceFreeInfo, 0, sizeof(m_stuParkLightState.stuSpaceFreeInfo));
	memset(&m_stuParkLightState.stuSpaceFullInfo, 0, sizeof(m_stuParkLightState.stuSpaceFullInfo));
	memset(&m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[0], 0, sizeof(m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[0]));
	memset(&m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[1], 0, sizeof(m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[1]));
	memset(&m_stuParkLightState.stuAbnormalAlarmInfo.stuSmokeFire, 0, sizeof(m_stuParkLightState.stuAbnormalAlarmInfo.stuSmokeFire));
	memset(&m_stuParkLightState.stuSpaceAlarmInfo, 0, sizeof(m_stuParkLightState.stuSpaceAlarmInfo));
	*/

	m_stuParkLightState.stuSpaceFreeInfo.nLightKeepTime = -1;
	m_stuParkLightState.stuSpaceFullInfo.nLightKeepTime = -1;
	m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[0].nLightKeepTime = -1;
	m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[1].nLightKeepTime = -1;
	m_stuParkLightState.stuAbnormalAlarmInfo.stuSmokeFire.nLightKeepTime = 0;
	m_stuParkLightState.stuSpaceAlarmInfo.nLightKeepTime = 60;
	
	getLightInfoFromComBox(m_stuParkLightState.stuSpaceFreeInfo, &m_combo_spacefree,&m_combo_spacefree1, IDC_CHECK_FREE,1);
	getLightInfoFromComBox(m_stuParkLightState.stuSpaceFullInfo, &m_combo_spacefull,&m_combo_spacefull1, IDC_CHECK_FULL,1);
	getLightInfoFromComBox(m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[0], &m_combo_netport1abort, IDC_CHECK_SIGNAL);
	getLightInfoFromComBox(m_stuParkLightState.stuNetWorkExceptionInfo.stNetPortAbortInfo[1], &m_combo_netport2abort, IDC_CHECK_DOUBLE);
	getLightInfoFromComBox(m_stuParkLightState.stuAbnormalAlarmInfo.stuSmokeFire, &m_combo_firedetection,  &m_combo_firedetection1, IDC_CHECK_FIRE);
	getLightInfoFromComBox(m_stuParkLightState.stuSpaceAlarmInfo, &m_combo_abnormalalarm,  &m_combo_abnormalalarm1,IDC_CHECK_ALARM);
}

void ParkingspacelightstateDlg::getLightInfoFromComBox(NET_PARKINGSPACELIGHT_INFO &lightInfo, CComboBox *comBox,CComboBox *comBox1, int nCheckID,int flag/*=0*/)
{
	int nSel = comBox->GetCurSel();
	int nSel1 = comBox1->GetCurSel();
	lightInfo.nColorCount = 2;

	int nCheck = ((CButton*)GetDlgItem(nCheckID))->GetCheck();
	switch(nSel)
	{
	case 0:
		{
			
			lightInfo.nColor[0] = 2;
			break;
		}
	case 1:
		{
			lightInfo.nColor[0] = 3;
			break;
		}
	case 2:
		{
			lightInfo.nColor[0] = 4;
			break;
		}
	case 3:
		{
			lightInfo.nColor[0] = 5;
			break;
		}
	case 4:
		{
			lightInfo.nColor[0] = 6;
			break;
		}
	case 5:
		{
			lightInfo.nColor[0] = 7;
			break;
		}
	case 6:
		{
			lightInfo.nColor[0] = 8;
			break;
		}
	case 7:
		{
			lightInfo.nColor[0] = 9;
			break;
		}
	case 8:
		{
			lightInfo.nColor[0] = 1;
			break;
		}
	default:
		break;
	}

	switch(nSel1)
	{
	case 0:
		{
			
			lightInfo.nColor[1] = 2;
			break;
		}
	case 1:
		{
			lightInfo.nColor[1] = 3;
			break;
		}
	case 2:
		{
			lightInfo.nColor[1] = 4;
			break;
		}
	case 3:
		{
			lightInfo.nColor[1] = 5;
			break;
		}
	case 4:
		{
			lightInfo.nColor[1] = 6;
			break;
		}
	case 5:
		{
			lightInfo.nColor[1] = 7;
			break;
		}
	case 6:
		{
			lightInfo.nColor[1] = 8;
			break;
		}
	case 7:
		{
			lightInfo.nColor[1] = 9;
			break;
		}
	case 8:
		{
			lightInfo.nColor[1] = 1;
			break;
		}
	default:
		lightInfo.nColorCount = 1;
		break;
	}

	BOOL bFlag = FALSE;
	switch(nCheckID)
	{
		case IDC_CHECK_FREE:
			nCheckID = IDC_EDIT_FREE;
			bFlag = TRUE;
			break;
		case IDC_CHECK_FULL:
			nCheckID = IDC_EDIT_FULL;
			bFlag = TRUE;
			break;
		case IDC_CHECK_FIRE:
			nCheckID = IDC_EDIT_FIRE;
			bFlag = TRUE;
			break;
		case IDC_CHECK_ALARM:
			nCheckID = IDC_EDIT_ALARM;
			bFlag = TRUE;
			break;
		default:
			break;
	}

	if(bFlag)
	{
		// 获取编辑控件指针
	    CEdit* pEdit = (CEdit*)GetDlgItem(nCheckID);

	    // 将整数转换为 CString
	    CString strValue;
	    
		pEdit->GetWindowText(strValue); // 获取 Edit Control 内容
		sscanf(strValue, "%d", &lightInfo.nLightKeepTime); // 从 CString 读取 int 值

	}

	if(lightInfo.nLightKeepTime == 0 && flag == 1)
	{
		lightInfo.nLightKeepTime = -1;
	}
}

void ParkingspacelightstateDlg::getLightInfoFromComBox(NET_PARKINGSPACELIGHT_INFO &lightInfo, CComboBox *comBox, int nCheckID)
{
	int nSel = comBox->GetCurSel();

	int nCheck = ((CButton*)GetDlgItem(nCheckID))->GetCheck();
	lightInfo.nColorCount = 2;
	switch(nSel)
	{
	case 0:
		{
			
			lightInfo.nColor[0] = 2;
			break;
		}
	case 1:
		{
			lightInfo.nColor[0] = 3;
			break;
		}
	case 2:
		{
			lightInfo.nColor[0] = 4;
			break;
		}
	case 3:
		{
			lightInfo.nColor[0] = 5;
			break;
		}
	case 4:
		{
			lightInfo.nColor[0] = 6;
			break;
		}
	case 5:
		{
			lightInfo.nColor[0] = 7;
			break;
		}
	case 6:
		{
			lightInfo.nColor[0] = 8;
			break;
		}
	case 7:
		{
			lightInfo.nColor[0] = 9;
			break;
		}
	case 8:
		{
			lightInfo.nColor[0] = 1;
			break;
		}
	default:
		break;
	}

	if (BST_CHECKED == nCheck)
	{
		lightInfo.nColor[1] = 1;
	}
	else
	{
		lightInfo.nColor[1] = 0;
	}
	/*switch(nSel)
	{
	case 0:
		{
			if (BST_CHECKED == nCheck)
			{
				lightInfo.nRed = 2;
			}
			else
			{
				lightInfo.nRed = 1;
			}
			break;
		}
	case 1:
		{
			if (BST_CHECKED == nCheck)
			{
				lightInfo.nYellow = 2;
			}
			else
			{
				lightInfo.nYellow = 1;
			}
			break;
		}
	case 2:
		{
			if (BST_CHECKED == nCheck)
			{
				lightInfo.nBlue = 2;
			}
			else
			{
				lightInfo.nBlue = 1;
			}
			break;
		}
	case 3:
		{
			if (BST_CHECKED == nCheck)
			{
				lightInfo.nGreen = 2;
			}
			else
			{
				lightInfo.nGreen = 1;
			}
			break;
		}
	case 4:
		{
			if (BST_CHECKED == nCheck)
			{
				lightInfo.nPurple = 2;
			}
			else
			{
				lightInfo.nPurple = 1;
			}
			break;
		}
	case 5:
		{
			if (BST_CHECKED == nCheck)
			{
				lightInfo.nWhite = 2;
			}
			else
			{
				lightInfo.nWhite = 1;
			}
			break;
		}
	case 6:
		{
			if (BST_CHECKED == nCheck)
			{
				lightInfo.nPink = 2;
			}
			else
			{
				lightInfo.nPink = 1;
			}
			break;
		}
	case 7:
		{
			lightInfo.nColorCount = 2;
			lightInfo.nColor[0] = 9;
			if (BST_CHECKED == nCheck)
			{
				lightInfo.nColor[1] = 1;
			}
			
			break;
		}
	default:
		break;
	}*/

	BOOL bFlag = FALSE;
	switch(nCheckID)
	{
		case IDC_CHECK_FREE:
			nCheckID = IDC_EDIT_FREE;
			bFlag = TRUE;
			break;
		case IDC_CHECK_FULL:
			nCheckID = IDC_EDIT_FULL;
			bFlag = TRUE;
			break;
		case IDC_CHECK_FIRE:
			nCheckID = IDC_EDIT_FIRE;
			bFlag = TRUE;
			break;
		case IDC_CHECK_ALARM:
			nCheckID = IDC_EDIT_ALARM;
			bFlag = TRUE;
			break;
		default:
			break;
	}

	if(bFlag)
	{
		// 获取编辑控件指针
	    CEdit* pEdit = (CEdit*)GetDlgItem(nCheckID);

	    // 将整数转换为 CString
	    CString strValue;
	    
		pEdit->GetWindowText(strValue); // 获取 Edit Control 内容
		sscanf(strValue, "%d", &lightInfo.nLightKeepTime); // 从 CString 读取 int 值

	}

	if(lightInfo.nLightKeepTime == 0)
	{
		lightInfo.nLightKeepTime = -1;
	}

}


void ParkingspacelightstateDlg::setConfig()
{
	if (m_pCtl->SetParkingSpaceLightState(&m_stuParkLightState) == false)
	{
		MessageBox(ConvertString("Set config failed"),ConvertString("Prompt"));
	}
}

void ParkingspacelightstateDlg::getConfig()
{
	memset(&m_stuParkLightState, 0, sizeof(m_stuParkLightState));
	m_stuParkLightState.dwSize = sizeof(m_stuParkLightState);
	if (m_pCtl->GetParkingSpaceLightState(&m_stuParkLightState) == false)
	{
		MessageBox(ConvertString("Get config failed"),ConvertString("Prompt"));
	}
}
