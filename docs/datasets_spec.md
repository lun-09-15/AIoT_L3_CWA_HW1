# 📊 中央氣象署 (CWA) 六大資料集規格清單 (Dataset Specifications)

本文件依據專案 [workflow.md](../workflow.md) 之**階段一：步驟 3「建立資料集規格清單」**編寫，詳細記錄各資料集之代碼、官方名稱、API 取得方式、格式、欄位規範、缺值定義與視覺化呈現元件。

---

## 📋 資料集規格總覽表

| 序號 | 儀表板區塊 | 資料集代碼 | 官方名稱 | 存取介面 | 回傳格式 | 更新頻率 | 缺值代碼 | 對應資料表 |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | **海面天氣預報** | `F-A0012-001` | 海面天氣預報 (北部及東北部海面、臺灣海峽等) | File API | JSON | 每 6 小時 | `-`, `""`, `無` | `marine_forecasts` |
| **2** | **氣象觀測站** | `O-A0001-001` | 全測站逐時氣象資料 | REST API | JSON | 每小時 | `-99`, `-999`, `X` | `station_observations` |
| **3** | **海嘯資訊** | `E-A0014-001` | 海嘯資訊-海嘯警示與解除報告 | REST API | JSON | 事件觸發 | 空陣列/無資料 | `tsunami_events` |
| **4** | **溫度分布狀態** | `O-A0038-001` | 溫度分布圖-溫度分布圖 | File API | JSON (圖片URL) | 每小時 | 無 | `temperature_maps` |
| **5** | **颱風侵襲機率** | `W-C0034-003` | 暴風圈侵襲機率圖層 | File API | KMZ (KML) | 颱風活動時 6 小時；警報期間 3 小時 | 無 | `typhoon_probabilities` |
| **6** | **熱帶氣旋路徑** | `W-C0034-005` | 熱帶氣旋路徑 (過去定位與未來預報) | REST API | JSON | 颱風活動時 6 小時；警報期間 3 小時 | `-`, `None` | `typhoon_tracks` |

---

## 🔍 各資料集詳細技術規格

### 1. 海面天氣預報 (`F-A0012-001`)
- **官方名稱**：海面天氣預報-北部及東北部海面、臺灣海峽、臺灣東南部海面等
- **存取端點**：
  `https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-A0012-001?Authorization={API_KEY}&downloadType=WEB&format=JSON`
- **解析入口**：`cwaopendata.Dataset.Locations.Location[]`
- **關鍵欄位**：
  - `LocationName`：海域名稱（共 32 處海域，例如釣魚台海面、臺灣海峽北部、宜蘭蘇澳沿海等）
  - `WeatherElement`：
    - `天氣現象` (`Weather`, `WeatherCode`)
    - `風向說明` (`WindDirection`)
    - `風速說明` (`WindSpeed`，如 4至5陣風7級)
    - `浪高說明` (`WaveHeight`，單位：公尺)
    - `浪況說明` (`WaveType`，如小浪至中浪)
  - 時間欄位：`StartTime`, `EndTime` (ISO 8601 時區字串)
- **展示元件**：海域下拉清單、預報時段切換、風浪天候資訊卡片、數據對照表。
- **儲存表格**：`marine_forecasts`（以 `location_name + start_time + end_time` 作唯一鍵去重）。

---

### 2. 氣象觀測站 (`O-A0001-001`)
- **官方名稱**：氣象觀測站-全測站逐時氣象資料
- **存取端點**：
  `https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={API_KEY}`
- **解析入口**：`records.Station[]`
- **關鍵欄位**：
  - `StationId`：測站代碼（如 `C0TB40`, `466920`）
  - `StationName`：測站名稱
  - `GeoInfo`：
    - `Coordinates`：篩選 `CoordinateName == 'WGS84'` 之 `StationLatitude`, `StationLongitude`
    - `StationAltitude`：測站海拔高度 (m)
    - `CountyName`、`TownName`：所屬縣市與鄉鎮
  - `ObsTime.DateTime`：觀測時間 (ISO 8601)
  - `WeatherElement`：
    - `AirTemperature`：氣溫 (°C)
    - `RelativeHumidity`：相對濕度 (%)
    - `WindSpeed`：風速 (m/s)
    - `WindDirection`：風向 (度 0-360)
    - `AirPressure`：測站氣壓 (hPa)
    - `Now.Precipitation`：即時累積雨量 (mm)
- **缺值代碼處理**：`-99`, `-999`, `-99.0`, `X` 一律轉換為 `None`（`NULL`），避免誤判為有效極端值。
- **展示元件**：Folium 互動測站地圖、縣市快選、即時氣溫色級標記、測站指標趨勢圖。
- **儲存表格**：`station_observations`（以 `station_id + obs_time` 複合唯一鍵去重）。

---

### 3. 海嘯資訊 (`E-A0014-001`)
- **官方名稱**：海嘯資訊-海嘯警示與解除報告
- **存取端點**：
  `https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0014-001?Authorization={API_KEY}`
- **解析入口**：`records.Tsunami[]`
- **關鍵欄位**：
  - `TsunamiNo`：海嘯事件編號
  - `ReportNo`：報告期別（如 第2報）
  - `IssueTime`：發布時間
  - `ValidTime.EndTime`：報告有效截止時間
  - `ReportColor`：警示燈號（綠、黃、橙、紅等）
  - `ReportType`：警示類型（如海嘯警報、海嘯消息、警報解除）
  - `ReportContent`：報告詳細說明內文
  - `EarthquakeInfo`：
    - `OriginTime`：地震發生時間
    - `FocalDepth`：震源深度 (km)
    - `Epicenter.Location`：震央位置描述
    - `Epicenter.EpicenterLatitude`, `Epicenter.EpicenterLongitude`：震央座標
    - `EarthquakeMagnitude.MagnitudeValue`：地震規模
  - `Web`：官方報告網頁 URL
- **展示元件**：海嘯警戒狀態橫幅（無事件顯示「目前無海嘯警戒」）、震央地理位置地圖標記、官方連結按鈕。
- **儲存表格**：`tsunami_events`（以 `tsunami_no + report_no + issue_time` 複合唯一鍵去重）。

---

### 4. 溫度分布狀態 (`O-A0038-001`)
- **官方名稱**：溫度分布圖-溫度分布圖
- **存取端點**：
  `https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/O-A0038-001?Authorization={API_KEY}&downloadType=WEB&format=JSON`
- **解析入口**：`cwaopendata.dataset`
- **關鍵欄位**：
  - `ObsTime.DateTime`：分析觀測時間
  - `GeoInfo.LatitudeRange`, `LongitudeRange`：涵蓋空間範圍
  - `Resource.ProductURL`：官方即時渲染之影像檔 CDN 連結 (`https://.../O-A0038-001.jpg`)
- **重要原則**：本資料集為影像產品，直接展示官方分析全景圖，不臆測還原為數值矩陣。
- **展示元件**：全台即時溫度熱圖渲染、發布時間標註、原始圖片外開檢視連結。
- **儲存表格**：`temperature_maps`（以 `dataset_id + obs_time` 唯一鍵去重）。

---

### 5. 颱風侵襲機率 (`W-C0034-003`)
- **官方名稱**：颱風侵襲機率-暴風圈侵襲機率圖層 (KMZ)
- **存取端點**：
  `https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-003?Authorization={API_KEY}&downloadType=WEB&format=KMZ`
- **解析入口**：解壓縮 KMZ 檔案內之 `fifows_wsp.kml`
- **關鍵結構**：
  - KML `Polygon` 與 Style 標籤：
    - `wsp20`：機率 20%
    - `wsp40`：機率 40%
    - `wsp60`：機率 60%
    - `wsp80`：機率 80%
- **重要原則**：機率資訊依官方範圍呈現，不可自行將機率過度解讀為「必然發生之警報」。無颱風時提示「目前無颱風活動資料」。
- **展示元件**：機率分級對照說明、圖層快照元數據卡片。
- **儲存表格**：`typhoon_probabilities`。

---

### 6. 熱帶氣旋路徑 (`W-C0034-005`)
- **官方名稱**：熱帶氣旋路徑-過去分析與預測路徑
- **存取端點**：
  `https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-005?Authorization={API_KEY}`
- **解析入口**：`records.TropicalCyclones.TropicalCyclone[]`
- **關鍵欄位**：
  - `TyphoonName`：國際英文名稱（如 GAEMI, KRATHON）
  - `CwaTyphoonName`：氣象署中文名稱（如 凱米, 山陀兒）
  - `Year`：年度
  - `AnalysisData.Fix[]`：過去至目前定位歷史
    - `FixTime`：定位時間
    - `Coordinate`：緯度,經度
    - `Pressure`：中心氣壓 (hPa)
    - `MaxWindSpeed`：中心最大風速 (m/s)
    - `Gust`：最大瞬間陣風 (m/s)
  - `ForecastData.Fix[]`：未來 24~72 小時預測路徑
- **展示元件**：Folium 互動路徑地圖（歷史路徑實線藍點 vs 預測路徑虛線紅點）、颱風強度風速時間軸卡片。
- **儲存表格**：`typhoon_tracks`（以 `typhoon_name + record_type + fix_time` 複合唯一鍵去重）。
