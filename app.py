"""Streamlit application for the six CWA marine, observation and hazard datasets."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from src.config import BASE_DIR, CWA_API_KEY, DB_PATH
from src.database import get_latest_ingestion_summary, init_db, query_rows
from src.datasets.specs import DATASET_SPECS
from src.datasets.typhoon import parse_typhoon_probability_kmz

st.set_page_config(page_title="CWA 海氣象與災害資訊", page_icon="🌦️", layout="wide")
init_db()

PAGES = [
    "總覽", "海面天氣預報", "氣象觀測站", "海嘯資訊", "溫度分布狀態", "颱風侵襲機率", "熱帶氣旋路徑",
]
DATASET_FOR_PAGE = {
    "海面天氣預報": "F-A0012-001",
    "氣象觀測站": "O-A0001-001",
    "海嘯資訊": "E-A0014-001",
    "溫度分布狀態": "O-A0038-001",
    "颱風侵襲機率": "W-C0034-003",
    "熱帶氣旋路徑": "W-C0034-005",
}


@st.cache_data(ttl=60, show_spinner=False)
def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.DataFrame(query_rows(sql, params))


def _timestamp(value: Any) -> str:
    if not value:
        return "時間未提供"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M %Z").strip()
    except (ValueError, TypeError):
        return str(value)


def _freshness(value: Any, hours: int) -> str:
    if not value:
        return "尚無資料時間"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        return "資料新鮮" if age.total_seconds() <= hours * 3600 else f"資料已超過 {hours} 小時，可能已過期"
    except (ValueError, TypeError):
        return "無法判讀資料時間"


def _download_csv(frame: pd.DataFrame, filename: str, label: str = "下載 CSV") -> None:
    if not frame.empty:
        st.download_button(label, frame.to_csv(index=False).encode("utf-8-sig"), filename, "text/csv")


def _empty(message: str = "尚無資料。可使用左側「更新全部資料」匯入最新資料。") -> None:
    st.info(message)


def _show_ingestion_status(dataset_id: str) -> None:
    run = next((item for item in get_latest_ingestion_summary() if item["dataset_id"] == dataset_id), None)
    if not run:
        st.caption("尚無匯入紀錄；此頁資料可能尚未載入。")
    elif run["status"] == "FAILED":
        st.warning(
            f"最近一次更新失敗（{_timestamp(run['run_time'])}）。目前顯示的已保存資料可能過期。"
            + (f"原因：{run['error_message']}" if run.get("error_message") else "")
        )
    elif run["status"] == "EMPTY":
        st.info(f"最近一次更新成功，但來源沒有可呈現資料（{_timestamp(run['run_time'])}）。")
    else:
        st.caption(
            f"最近成功更新：{_timestamp(run['run_time'])} · "
            f"來源資料時間：{_timestamp(run.get('data_timestamp'))} · "
            f"回應 {run.get('response_time_ms'):.0f} ms"
            if run.get("response_time_ms") is not None
            else f"最近成功更新：{_timestamp(run['run_time'])} · 來源資料時間：{_timestamp(run.get('data_timestamp'))}"
        )


def _station_map(frame: pd.DataFrame) -> None:
    mapped = frame.dropna(subset=["latitude", "longitude"])
    if mapped.empty:
        st.info("目前資料沒有可用的 WGS84 座標。")
        return
    weather_map = folium.Map(location=[23.7, 121.0], zoom_start=7, tiles="CartoDB positron", control_scale=True)
    for row in mapped.itertuples(index=False):
        temp = getattr(row, "temperature", None)
        color = "blue" if pd.notna(temp) and temp < 20 else "orange" if pd.notna(temp) and temp >= 30 else "green"
        popup = (
            f"<b>{row.station_name}</b> ({row.station_id})<br>"
            f"{row.county_name or ''} {row.town_name or ''}<br>"
            f"觀測：{_timestamp(row.obs_time)}<br>"
            f"氣溫：{temp if pd.notna(temp) else '—'} °C<br>"
            f"雨量：{row.precipitation if pd.notna(row.precipitation) else '—'} mm"
        )
        folium.CircleMarker(
            [row.latitude, row.longitude], radius=5, color=color, fill=True,
            fill_color=color, fill_opacity=.75, tooltip=row.station_name, popup=popup,
        ).add_to(weather_map)
    st_folium(weather_map, width=1000, height=520, key="station_map")


def _page_overview() -> None:
    st.title("🌦️ CWA 海氣象與災害資訊儀表板")
    st.write("整合六項中央氣象署資料。資料時間與來源會隨各產品呈現；事件型資料在無事件時會明確顯示狀態。")
    latest = {row["dataset_id"]: row for row in get_latest_ingestion_summary()}
    cols = st.columns(3)
    for index, (page, dataset_id) in enumerate(DATASET_FOR_PAGE.items()):
        spec = DATASET_SPECS[dataset_id]
        run = latest.get(dataset_id)
        status = run["status"] if run else "尚未匯入"
        date = _timestamp(run.get("data_timestamp") or run.get("run_time")) if run else "—"
        with cols[index % 3]:
            st.metric(page, status, f"{run.get('records_count', 0)} 筆" if run else None)
            st.caption(f"{dataset_id} · {date}")
            st.caption(spec.update_frequency)
    st.subheader("最近匯入狀態")
    if latest:
        rows = []
        for dataset_id, run in latest.items():
            rows.append({
                "資料集": dataset_id,
                "狀態": run["status"],
                "筆數/圖層數": run["records_count"],
                "執行時間": _timestamp(run["run_time"]),
                "錯誤": run.get("error_message") or "",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        _empty("資料庫尚無匯入紀錄。請先在左側按「更新全部資料」，或執行 `python -m src.ingest`。")


def _page_marine() -> None:
    st.title("🌊 海面天氣預報")
    st.caption("資料集 F-A0012-001 · 依預報海域與有效時段呈現")
    _show_ingestion_status("F-A0012-001")
    frame = _read("SELECT * FROM marine_forecasts ORDER BY start_time, location_name")
    if frame.empty:
        return _empty()
    now = datetime.now().astimezone().isoformat(timespec="minutes")
    frame = frame[frame["end_time"].astype(str) >= now].copy()
    if frame.empty:
        return _empty("資料庫中的海面預報時段都已結束。請更新資料取得最新預報。")
    locations = ["全部海域"] + sorted(frame["location_name"].dropna().unique().tolist())
    selected = st.selectbox("預報海域", locations)
    if selected != "全部海域":
        frame = frame[frame["location_name"] == selected]
    slot_values = sorted(frame["start_time"].dropna().unique().tolist())
    if slot_values:
        selected_time = st.selectbox("預報開始時間", slot_values, format_func=_timestamp)
        frame = frame[frame["start_time"] == selected_time]
    if frame.empty:
        return _empty("該篩選條件沒有預報時段。")
    row = frame.iloc[0]
    cols = st.columns(4)
    cols[0].metric("海域", row["location_name"])
    cols[1].metric("天氣", row["weather"] or "—")
    cols[2].metric("風向", row["wind_direction"] or "—")
    cols[3].metric("風速", row["wind_speed"] or "—")
    st.info(f"有效時間：{_timestamp(row['start_time'])} 至 {_timestamp(row['end_time'])} · 浪高 {row['wave_height'] or '—'} · 浪況 {row['wave_type'] or '—'}")
    display = frame[["location_name", "start_time", "end_time", "weather", "wind_direction", "wind_speed", "wave_height", "wave_type"]].copy()
    display.columns = ["海域", "開始時間", "結束時間", "天氣", "風向", "風速", "浪高", "浪況"]
    st.dataframe(display, hide_index=True, use_container_width=True)
    _download_csv(display, "marine_forecasts.csv")


def _page_stations() -> None:
    st.title("📍 氣象觀測站")
    st.caption("資料集 O-A0001-001 · 測站位置來自 WGS84 座標；特殊缺值已排除")
    _show_ingestion_status("O-A0001-001")
    frame = _read("""SELECT s.* FROM station_observations s
                    JOIN (SELECT station_id,MAX(obs_time) AS obs_time FROM station_observations GROUP BY station_id) latest
                    ON s.station_id=latest.station_id AND s.obs_time=latest.obs_time
                    ORDER BY s.county_name,s.station_name""")
    if frame.empty:
        return _empty()
    counties = ["全部縣市"] + sorted(frame["county_name"].dropna().unique().tolist())
    county = st.selectbox("縣市", counties)
    if county != "全部縣市":
        frame = frame[frame["county_name"] == county]
    metrics = st.columns(4)
    metrics[0].metric("測站數", len(frame))
    metrics[1].metric("平均氣溫", f"{frame['temperature'].mean():.1f} °C" if frame["temperature"].notna().any() else "—")
    metrics[2].metric("平均相對濕度", f"{frame['relative_humidity'].mean():.0f}%" if frame["relative_humidity"].notna().any() else "—")
    metrics[3].metric("最新觀測", _timestamp(frame["obs_time"].max()))
    _station_map(frame)
    station_options = frame[["station_id", "station_name"]].drop_duplicates()
    station_id = st.selectbox("測站趨勢", station_options["station_id"].tolist(),
                              format_func=lambda value: station_options.loc[station_options["station_id"] == value, "station_name"].iloc[0])
    station_label = station_options.loc[station_options["station_id"] == station_id, "station_name"].iloc[0]
    history = _read(
        "SELECT obs_time,temperature,relative_humidity,wind_speed,precipitation,air_pressure FROM station_observations WHERE station_id=? ORDER BY obs_time DESC LIMIT 48",
        (station_id,),
    ).sort_values("obs_time")
    metric_options = {"氣溫 °C": "temperature", "相對濕度 %": "relative_humidity", "風速 m/s": "wind_speed", "雨量 mm": "precipitation", "氣壓 hPa": "air_pressure"}
    metric_label = st.selectbox("觀測項目", list(metric_options))
    chart = history.set_index("obs_time")[[metric_options[metric_label]]].rename(columns={metric_options[metric_label]: metric_label})
    st.subheader(f"{station_label} · 最近 48 筆觀測")
    st.line_chart(chart, height=300)
    table = frame[["station_id", "station_name", "county_name", "town_name", "obs_time", "temperature", "relative_humidity", "wind_speed", "wind_direction", "precipitation", "air_pressure"]].copy()
    table.columns = ["站碼", "測站", "縣市", "鄉鎮", "觀測時間", "氣溫 °C", "濕度 %", "風速 m/s", "風向 °", "雨量 mm", "氣壓 hPa"]
    st.dataframe(table, hide_index=True, use_container_width=True)
    _download_csv(table, "station_observations.csv")


def _page_tsunami() -> None:
    st.title("🚨 海嘯資訊")
    st.caption("資料集 E-A0014-001 · 以最新報告狀態為準，並提供歷史報告查閱")
    _show_ingestion_status("E-A0014-001")
    events = _read("SELECT * FROM tsunami_events ORDER BY issue_time DESC LIMIT 100")
    if events.empty:
        return _empty("目前沒有海嘯報告資料；這表示資料庫沒有已匯入的報告，不等同即時官方安全告警。")
    latest = events.iloc[0]
    report_type = str(latest.get("report_type") or "")
    color = str(latest.get("report_color") or "")
    released = "解除" in report_type or color == "綠色"
    valid_end = latest.get("valid_end_time")
    expired = False
    if valid_end:
        try:
            end_dt = datetime.fromisoformat(str(valid_end).replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            expired = end_dt.astimezone(timezone.utc) < datetime.now(timezone.utc)
        except ValueError:
            expired = False
    if expired:
        st.info(f"資料庫最新報告：{report_type or '海嘯資訊'}（{color or '未標示'}）。此報告已過有效時間，不能視為目前警報。")
    elif released:
        st.success(f"最新報告：{report_type or '海嘯資訊'}（{color or '未標示'}）")
    else:
        st.warning(f"最新報告：{report_type or '海嘯資訊'}（{color or '未標示'}）")
    st.caption(f"報告時間 {_timestamp(latest['issue_time'])} · 編號 {latest.get('tsunami_no') or '—'} {latest.get('report_no') or ''} · 有效至 {_timestamp(latest.get('valid_end_time'))}")
    st.write(latest.get("report_content") or "報告未提供文字說明。")
    cols = st.columns(4)
    cols[0].metric("地震規模", latest.get("magnitude") if pd.notna(latest.get("magnitude")) else "—")
    cols[1].metric("震源深度", f"{latest['focal_depth']} km" if pd.notna(latest.get("focal_depth")) else "—")
    cols[2].metric("震央", latest.get("epicenter_location") or "—")
    cols[3].metric("地震時間", _timestamp(latest.get("origin_time")))
    if pd.notna(latest.get("epicenter_lat")) and pd.notna(latest.get("epicenter_lon")):
        m = folium.Map(location=[latest["epicenter_lat"], latest["epicenter_lon"]], zoom_start=5, tiles="CartoDB positron")
        folium.Marker([latest["epicenter_lat"], latest["epicenter_lon"]], tooltip="最新報告震央", popup=latest.get("epicenter_location") or "震央").add_to(m)
        st_folium(m, width=1000, height=360, key="tsunami_map")
    history = events[["issue_time", "tsunami_no", "report_no", "report_type", "report_color", "epicenter_location", "magnitude", "web_url"]].copy()
    history.columns = ["發布時間", "事件編號", "報別", "報告類型", "顏色", "震央", "規模", "官方報告"]
    st.subheader("最近報告")
    st.dataframe(history, hide_index=True, use_container_width=True, column_config={"官方報告": st.column_config.LinkColumn()})
    _download_csv(history, "tsunami_reports.csv")


def _page_temperature() -> None:
    st.title("🌡️ 溫度分布狀態")
    st.caption("資料集 O-A0038-001 · 官方影像產品；圖面色彩不反推為精確逐點數值")
    _show_ingestion_status("O-A0038-001")
    maps = _read("SELECT * FROM temperature_maps ORDER BY obs_time DESC LIMIT 1")
    if maps.empty:
        return _empty()
    row = maps.iloc[0]
    st.caption(f"觀測時間：{_timestamp(row['obs_time'])} · {_freshness(row['obs_time'], 2)} · 範圍 {row.get('lat_range') or '—'} N, {row.get('lon_range') or '—'} E")
    st.image(row["image_url"], caption=f"中央氣象署溫度分布圖 · {_timestamp(row['obs_time'])}", use_container_width=True)
    st.link_button("開啟原始影像", row["image_url"])


def _probability_map() -> None:
    snapshots = _read("SELECT * FROM typhoon_probabilities ORDER BY id DESC LIMIT 20")
    if snapshots.empty:
        return _empty("尚無颱風侵襲機率圖層；若目前沒有颱風活動，官方產品也可能沒有有效圖層。")
    row = snapshots.iloc[0]
    path = Path(str(row["kmz_path"]))
    if not path.is_absolute():
        path = BASE_DIR / path
    st.caption(f"圖層發布時間：{_timestamp(row.get('product_time'))} · 擷取時間：{_timestamp(row.get('captured_at'))} · {_freshness(row.get('product_time') or row.get('captured_at'), 24)}")
    if not path.exists():
        st.error(f"找不到已保存的圖層檔案：{path.name}。請重新匯入此資料集。")
        return
    try:
        parsed = parse_typhoon_probability_kmz(path.read_bytes())
    except Exception as exc:
        st.error(f"無法讀取颱風機率 KMZ：{exc}")
        return
    features = parsed["features"]
    if not features:
        st.info("目前圖層沒有機率範圍；目前可能沒有有效颱風活動資料。")
        return
    colors = {"20%": "#2ecc71", "40%": "#3498db", "60%": "#f1c40f", "80%": "#e67e22", "100%": "#e74c3c"}
    geojson = {"type": "FeatureCollection", "features": features}
    m = folium.Map(location=[22.5, 130.0], zoom_start=4, tiles="CartoDB positron", control_scale=True)
    folium.GeoJson(
        geojson,
        name="暴風圈侵襲機率",
        style_function=lambda feature: {
            "color": colors.get(feature["properties"].get("probability"), "#7f8c8d"),
            "fillColor": colors.get(feature["properties"].get("probability"), "#7f8c8d"),
            "weight": 1,
            "fillOpacity": .28,
        },
        tooltip=folium.GeoJsonTooltip(fields=["probability"], aliases=["官方機率範圍"]),
    ).add_to(m)
    folium.LayerControl().add_to(m)
    bounds = []
    for feature in features:
        for ring in feature["geometry"]["coordinates"]:
            bounds.extend([[lat, lon] for lon, lat in ring])
    if bounds:
        m.fit_bounds(bounds, padding=(15, 15))
    st_folium(m, width=1000, height=560, key="typhoon_probability_map")
    st.caption("顏色代表 KMZ 內官方標示的機率級距，請依 CWA 原始產品說明判讀。")
    with st.expander("圖層資料"):
        st.write(f"多邊形數：{parsed['polygon_count']}")
        st.write(f"快照：{path.name}")


def _page_typhoon_probability() -> None:
    st.title("🌀 颱風侵襲機率")
    st.caption("資料集 W-C0034-003 · 官方 KMZ GIS 圖層")
    _show_ingestion_status("W-C0034-003")
    _probability_map()


def _page_typhoon_track() -> None:
    st.title("🧭 熱帶氣旋路徑")
    st.caption("資料集 W-C0034-005 · 實線為分析定位，虛線為預測定位")
    _show_ingestion_status("W-C0034-005")
    frame = _read("SELECT * FROM typhoon_tracks ORDER BY fix_time")
    if frame.empty:
        return _empty("目前沒有可呈現的熱帶氣旋路徑資料。")
    names = sorted(frame["typhoon_name"].dropna().unique().tolist())
    cwa_names = frame.groupby("typhoon_name")["cwa_name"].first().fillna("").to_dict()
    selected_name = st.selectbox("熱帶氣旋", names, format_func=lambda name: f"{name}（{cwa_names.get(name, '')}）")
    cyclone = frame[frame["typhoon_name"] == selected_name].copy()
    cyclone = cyclone.sort_values("fix_time")
    m = folium.Map(location=[20.0, 135.0], zoom_start=4, tiles="CartoDB positron", control_scale=True)
    palette = {"ANALYSIS": "#1565c0", "FORECAST": "#d32f2f"}
    for kind, group in cyclone.groupby("record_type"):
        group = group.sort_values("fix_time")
        color = palette.get(kind, "#455a64")
        points = [[row.latitude, row.longitude] for row in group.itertuples()]
        if points:
            folium.PolyLine(points, color=color, weight=3, dash_array="8 8" if kind == "FORECAST" else None, tooltip="預測路徑" if kind == "FORECAST" else "分析路徑").add_to(m)
        for row in group.itertuples():
            label = "預測" if kind == "FORECAST" else "分析定位"
            popup = (
                f"<b>{selected_name} · {label}</b><br>時間：{_timestamp(row.fix_time)}<br>"
                f"位置：{row.latitude:.2f}, {row.longitude:.2f}<br>"
                f"最大風速：{row.max_wind_speed if pd.notna(row.max_wind_speed) else '—'} m/s<br>"
                f"中心氣壓：{row.pressure if pd.notna(row.pressure) else '—'} hPa"
            )
            folium.CircleMarker([row.latitude, row.longitude], radius=5 if kind == "ANALYSIS" else 4,
                                color=color, fill=True, fill_opacity=.8, popup=popup,
                                tooltip=f"{label} · {_timestamp(row.fix_time)}").add_to(m)
    points = cyclone[["latitude", "longitude"]].dropna()
    if not points.empty:
        m.fit_bounds([[points["latitude"].min(), points["longitude"].min()], [points["latitude"].max(), points["longitude"].max()]], padding=(20, 20))
    st_folium(m, width=1000, height=560, key="typhoon_track_map")
    detail = cyclone[["record_type", "fix_time", "latitude", "longitude", "max_wind_speed", "gust", "pressure"]].copy()
    detail["record_type"] = detail["record_type"].map({"ANALYSIS": "分析定位", "FORECAST": "預測定位"}).fillna(detail["record_type"])
    detail.columns = ["資料類型", "時間", "緯度", "經度", "最大風速 m/s", "陣風 m/s", "氣壓 hPa"]
    st.dataframe(detail, hide_index=True, use_container_width=True)
    _download_csv(detail, "typhoon_track.csv")


def _sync_all() -> None:
    if not CWA_API_KEY:
        st.sidebar.error("尚未設定 CWA_API_KEY。請建立 .env 並填入授權碼。")
        return
    with st.sidebar.status("正在更新六項資料…", expanded=True) as status:
        try:
            from src.ingest import ingest_all
            from src.cwa_client import CWAClient
            results = ingest_all(CWAClient())
            failed = [dataset_id for dataset_id, result in results.items() if result["status"] == "FAILED"]
            status.update(label="更新完成" if not failed else f"更新完成，{len(failed)} 項失敗", state="complete" if not failed else "error")
            st.session_state["last_sync_results"] = results
            st.cache_data.clear()
        except Exception as exc:
            status.update(label="資料更新失敗", state="error")
            st.sidebar.error(str(exc))


st.sidebar.title("資料導覽")
page = st.sidebar.radio("選擇資料區塊", PAGES, label_visibility="collapsed")
st.sidebar.button("⟳ 更新全部資料", on_click=_sync_all, use_container_width=True, type="primary")
st.sidebar.caption("同步需設定有效 CWA API Key，並保持網路連線。")
st.sidebar.caption(f"資料庫：{DB_PATH.name}")

if page == "總覽":
    _page_overview()
elif page == "海面天氣預報":
    _page_marine()
elif page == "氣象觀測站":
    _page_stations()
elif page == "海嘯資訊":
    _page_tsunami()
elif page == "溫度分布狀態":
    _page_temperature()
elif page == "颱風侵襲機率":
    _page_typhoon_probability()
elif page == "熱帶氣旋路徑":
    _page_typhoon_track()

st.sidebar.markdown("---")
st.sidebar.caption("資料來源：中央氣象署開放資料平台。產品時間和更新頻率依官方資料為準。")
