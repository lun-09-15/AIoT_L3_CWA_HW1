# 台灣海氣象與災害資訊儀表板

以中央氣象署開放資料建置的 Streamlit + SQLite 儀表板，整合海面天氣、逐時測站觀測、海嘯報告、溫度分布圖、颱風侵襲機率與熱帶氣旋路徑。

## 六項資料功能

| 頁面 | 資料集 | 功能 |
| --- | --- | --- |
| 海面天氣預報 | `F-A0012-001` | 依海域與時段查詢天氣、風向風速、浪高與浪況。 |
| 氣象觀測站 | `O-A0001-001` | 測站地圖、縣市篩選、氣溫/濕度/風速/雨量趨勢與 CSV。 |
| 海嘯資訊 | `E-A0014-001` | 最新報告狀態、震央資訊、歷史報告與 CWA 原始連結。 |
| 溫度分布狀態 | `O-A0038-001` | 顯示 CWA 官方溫度分布影像和資料時間。 |
| 颱風侵襲機率 | `W-C0034-003` | 解析 KMZ/KML 的官方機率多邊形並疊加於地圖。 |
| 熱帶氣旋路徑 | `W-C0034-005` | 在地圖分別呈現過去分析定位與未來預測路徑。 |

各資料集使用個別解析器與資料表。原始回應保存在 `data/snapshots/`，結構化資料與匯入狀態寫入 SQLite；時間戳記、單位、缺值和空事件狀態會在頁面中保留或明確標示。

總覽頁的「六項資料新鮮度與更新狀態」可查看最近擷取結果、來源資料時間及固定週期資料的逾時提示。海嘯與颱風等事件型資料會依事件特性呈現，不會僅因事件時間較早就判為過期。按左側「更新全部資料」重新擷取後可查看最新狀態。

總覽測站地圖預設以台灣本島為中心並採較近的縮放層級，不會為了涵蓋所有站點而自動縮小；可在地圖上自行縮放查看外島及其他測站。

## 安裝與設定

需要 Python 3.9 以上版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

編輯 `.env` 並設定 CWA 會員中心取得的授權碼：

```ini
CWA_API_KEY=你的CWA授權碼
```

不要提交 `.env` 或分享 API Key。

## 匯入資料並啟動

在專案根目錄執行：

```powershell
python -m src.ingest
streamlit run app.py
```

可只更新指定資料集：

```powershell
python -m src.ingest --only O-A0001-001 W-C0034-005
```

開啟 Streamlit 顯示的本機網址。也可以使用頁面左側「更新全部資料」按鈕重新擷取；資料更新需要有效的 CWA API Key 和網路連線。尚未匯入資料時，儀表板會顯示空資料提示，不會將舊資料假裝成即時狀態。

## 專案結構

```text
app.py                         # Streamlit 六頁儀表板
src/config.py                  # 環境變數與本機路徑
src/cwa_client.py              # CWA REST/File API、重試與原始快照
src/ingest.py                  # 全部或指定資料集匯入命令
src/database.py                # SQLite schema、交易與查詢
src/storage.py                 # 資料集解析/儲存分派
src/datasets/                   # 六項資料集規格與解析器
docs/datasets_spec.md          # 資料集欄位規格
workflow.md                    # 開發與展示工作流程
data/snapshots/                # 原始 API/檔案快照（本機資料）
data/weather_dashboard.db      # SQLite 資料庫（自動建立）
```

## 常見問題

- **缺少 Key**：確認 `.env` 位於專案根目錄，變數名稱為 `CWA_API_KEY`，之後重新啟動 Streamlit。
- **某項匯入失敗**：看終端機的資料集代碼與錯誤訊息；其他資料集會繼續匯入。
- **目前無颱風/海嘯資料**：事件資料無內容可能代表目前無有效事件。請查看資料時間與最近匯入狀態，不要把過期報告解讀成即時警報。
- **溫度分布圖**：`O-A0038-001` 是官方影像產品，不是可逐點查詢的溫度矩陣。

## 技術

Python、Requests、Pandas、SQLite、Streamlit、Folium、streamlit-folium、python-dotenv。

資料來源：中央氣象署氣象資料開放平台。實際資料欄位、更新頻率和警示效力以 CWA 公告及各資料產品說明為準。
