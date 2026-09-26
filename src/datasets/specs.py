"""
CWA Dataset Specifications definitions.
Defines metadata, API endpoint types, update frequencies, and schema notes for the 6 datasets.
"""

from dataclasses import dataclass
from typing import Dict, Optional

@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    official_name: str
    category: str
    api_type: str            # 'REST' or 'FILE'
    file_format: str          # 'JSON' or 'KMZ'
    update_frequency: str
    time_field: str
    geo_field: Optional[str]
    missing_value_codes: list
    unit_description: str
    ui_component: str
    description: str

DATASET_SPECS: Dict[str, DatasetSpec] = {
    "F-A0012-001": DatasetSpec(
        dataset_id="F-A0012-001",
        official_name="海面天氣預報-北部及東北部海面、臺灣海峽、臺灣東南部海面等",
        category="海面預報",
        api_type="FILE",
        file_format="JSON",
        update_frequency="每 6 小時更新",
        time_field="StartTime, EndTime",
        geo_field="LocationName (海域名稱)",
        missing_value_codes=["-", "", "無"],
        unit_description="風速 (級/m/s)、浪高 (公尺)、天氣描述",
        ui_component="海域篩選清單、風向風速與浪高卡片、資料明細表",
        description="包含台灣近海與鄰近海域之天氣現象、風向、風速、浪高與浪況預報。"
    ),
    "O-A0001-001": DatasetSpec(
        dataset_id="O-A0001-001",
        official_name="氣象觀測站-全測站逐時氣象資料",
        category="測站觀測",
        api_type="REST",
        file_format="JSON",
        update_frequency="每小時更新",
        time_field="ObsTime.DateTime",
        geo_field="GeoInfo.Coordinates (WGS84 經緯度)",
        missing_value_codes=["-99", "-999", "X", "-99.0", "-999.0"],
        unit_description="氣溫 (°C)、相對濕度 (%)、風速 (m/s)、降雨量 (mm)、氣壓 (hPa)",
        ui_component="Folium 互動測站地圖、縣市篩選、觀測指標趨勢卡片與數據圖表",
        description="全台灣自動與有人氣象站之即時氣溫、濕度、氣壓、風向風速與雨量觀測。"
    ),
    "E-A0014-001": DatasetSpec(
        dataset_id="E-A0014-001",
        official_name="海嘯資訊-海嘯警示與解除報告",
        category="海嘯資訊",
        api_type="REST",
        file_format="JSON",
        update_frequency="事件觸發更新 (無事件時顯示無警戒)",
        time_field="IssueTime, ValidTime.EndTime",
        geo_field="EarthquakeInfo.Epicenter (震央座標)",
        missing_value_codes=[],
        unit_description="震源深度 (km)、地震規模 (M_L/M_w)、報告燈號",
        ui_component="海嘯警戒燈號橫幅、地震與震央資訊卡、歷史警訊清單",
        description="海嘯警報發布、解除資訊及相關地震震央與規模紀錄。"
    ),
    "O-A0038-001": DatasetSpec(
        dataset_id="O-A0038-001",
        official_name="溫度分布圖-全台溫度分布影像產品",
        category="溫度分布",
        api_type="FILE",
        file_format="JSON",
        update_frequency="每小時更新",
        time_field="ObsTime.DateTime",
        geo_field="GeoInfo (涵蓋經緯度範圍)",
        missing_value_codes=[],
        unit_description="影像產品圖檔 (JPG) 與有效時間",
        ui_component="溫度分布圖影像呈現、資料發布時間標記與原圖連結",
        description="中央氣象署官方分析之全台灣即時溫度分布熱圖產品。"
    ),
    "W-C0034-003": DatasetSpec(
        dataset_id="W-C0034-003",
        official_name="颱風侵襲機率-暴風圈侵襲機率圖層 (KMZ)",
        category="颱風資訊",
        api_type="FILE",
        file_format="KMZ",
        update_frequency="颱風活動期間每 6~12 小時更新",
        time_field="Document/TimeStamp (或檔名時戳)",
        geo_field="KML GIS Polygon coordinates",
        missing_value_codes=[],
        unit_description="侵襲機率百分比級距 (20%, 40%, 60%, 80%)",
        ui_component="機率分級說明卡、官方圖層狀態標示與時效提示",
        description="颱風暴風圈侵襲各區域機率之官方 GIS 圖層檔案 (KMZ 格式)。"
    ),
    "W-C0034-005": DatasetSpec(
        dataset_id="W-C0034-005",
        official_name="熱帶氣旋路徑-過去分析與預測路徑",
        category="颱風資訊",
        api_type="REST",
        file_format="JSON",
        update_frequency="颱風活動期間每 3~6 小時更新",
        time_field="AnalysisData.Fix.FixTime, ForecastData.Fix.FixTime",
        geo_field="Fix.Coordinate (經緯度)",
        missing_value_codes=["-", "None"],
        unit_description="中心氣壓 (hPa)、最大風速 (m/s)、瞬間陣風 (m/s)",
        ui_component="颱風路徑地圖 (歷史定位 vs 預測路徑)、強度與時戳清單",
        description="現行熱帶氣旋/颱風之過去路徑分析與未來 24~72 小時預測路徑數據。"
    ),
}
