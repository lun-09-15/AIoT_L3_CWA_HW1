"""Streamlit application for the six CWA marine, observation and hazard datasets."""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

import folium
import pandas as pd
import streamlit as st
from branca.element import Element
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
    weather_map = folium.Map(location=[23.7, 121.0], zoom_start=7, tiles="OpenStreetMap", control_scale=True)
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
    st_folium(weather_map, width=1000, height=520, key="station_map", returned_objects=[])


def _latest_station_frame() -> pd.DataFrame:
    return _read("""SELECT s.* FROM station_observations s
                    JOIN (SELECT station_id,MAX(obs_time) AS obs_time FROM station_observations GROUP BY station_id) latest
                    ON s.station_id=latest.station_id AND s.obs_time=latest.obs_time
                    ORDER BY s.county_name,s.station_name""")


def _weather_overview_map(
    frame: pd.DataFrame,
    dark_basemap: bool,
    show_temperature: bool,
    show_rain: bool,
    show_wind: bool,
) -> None:
    mapped = frame.dropna(subset=["latitude", "longitude"]).copy()
    if mapped.empty:
        st.info("目前尚無含有效 WGS84 座標的測站資料。請從側邊欄更新氣象觀測站資料。")
        return

    weather_map = folium.Map(
        location=[23.7, 121.0], zoom_start=6, tiles=None, control_scale=True,
        prefer_canvas=True, min_zoom=5,
    )
    folium.TileLayer(
        tiles="OpenStreetMap", name="OpenStreetMap", show=True,
    ).add_to(weather_map)
    if dark_basemap:
        # Keep the OSM tile service/API key-free while giving the overview a dark look.
        weather_map.get_root().header.add_child(Element(
            f'<style>#{weather_map.get_name()} .leaflet-tile-pane '
            '{filter:invert(.88) hue-rotate(180deg) brightness(.72) contrast(.92) saturate(.78);}</style>'
        ))

    station_layer = folium.FeatureGroup(name="測站位置", show=True)
    temperature_layer = folium.FeatureGroup(name="氣溫標籤", show=True) if show_temperature else None
    rain_layer = folium.FeatureGroup(name="降雨觀測", show=True) if show_rain else None
    wind_layer = folium.FeatureGroup(name="風速觀測", show=True) if show_wind else None

    for row in mapped.itertuples(index=False):
        lat, lon = float(row.latitude), float(row.longitude)
        station_name = escape(str(row.station_name or "測站"))
        station_id = escape(str(row.station_id or ""))
        county = escape(str(row.county_name or ""))
        town = escape(str(row.town_name or ""))
        temp = row.temperature
        rain = row.precipitation
        wind = row.wind_speed
        details = (
            f"<b>{station_name}</b> ({station_id})<br>{county} {town}<br>"
            f"觀測時間：{escape(_timestamp(row.obs_time))}<br>"
            f"氣溫：{temp if pd.notna(temp) else '—'} °C　"
            f"雨量：{rain if pd.notna(rain) else '—'} mm<br>"
            f"風速：{wind if pd.notna(wind) else '—'} m/s"
        )
        folium.CircleMarker(
            [lat, lon], radius=3, color="#d8e4f0", weight=1,
            fill=True, fill_color="#24364a", fill_opacity=.9,
            tooltip=f"{station_name} · {temp if pd.notna(temp) else '—'} °C",
            popup=folium.Popup(details, max_width=280),
        ).add_to(station_layer)

        if temperature_layer is not None and pd.notna(temp):
            color = "#50c9a7" if temp < 24 else "#f2d16b" if temp < 29 else "#f58c69"
            label = folium.DivIcon(
                icon_size=(48, 25), icon_anchor=(24, 12),
                html=(f'<div style="background:{color};color:#17202a;border:1px solid #fff;'
                      f'border-radius:16px;padding:2px 7px;font:bold 12px Arial;text-align:center;'
                      f'box-shadow:0 2px 8px #0008;white-space:nowrap">{temp:.0f}°</div>'),
            )
            folium.Marker(
                [lat, lon], icon=label, tooltip=f"{station_name} · {temp:.1f} °C",
            ).add_to(temperature_layer)

        if rain_layer is not None and pd.notna(rain) and rain > 0:
            folium.CircleMarker(
                [lat, lon], radius=min(5 + float(rain), 18), color="#49a8ff",
                fill=True, fill_color="#49a8ff", fill_opacity=.48,
                tooltip=f"{station_name} · 雨量 {rain:g} mm",
            ).add_to(rain_layer)

        if wind_layer is not None and pd.notna(wind):
            folium.CircleMarker(
                [lat, lon], radius=min(4 + float(wind) / 2, 12), color="#bd91ff",
                fill=True, fill_color="#bd91ff", fill_opacity=.42,
                tooltip=f"{station_name} · 風速 {wind:g} m/s",
            ).add_to(wind_layer)

    layers = [station_layer]
    layers.extend(layer for layer in (temperature_layer, rain_layer, wind_layer) if layer is not None)
    for layer in layers:
        layer.add_to(weather_map)
    weather_map.fit_bounds(
        [[mapped["latitude"].min(), mapped["longitude"].min()],
         [mapped["latitude"].max(), mapped["longitude"].max()]],
        padding=(28, 28),
    )
    map_key = f"overview_map_{int(dark_basemap)}_{int(show_temperature)}_{int(show_rain)}_{int(show_wind)}"
    st_folium(weather_map, width=960, height=610, key=map_key, returned_objects=[])


def _apply_dashboard_theme() -> None:
    st.markdown("""
    <style>
      :root { color-scheme: dark; }
      .stApp, [data-testid="stAppViewContainer"] { background:#0c1420; color:#e7edf5; }
      [data-testid="stHeader"] { background:rgba(12,20,32,.94); }
      [data-testid="stMainBlockContainer"] { max-width:100%; padding:1.1rem 1.35rem 2rem; }
      [data-testid="stSidebar"] { background:#101b2a; border-right:1px solid #233247; }
      [data-testid="stMetric"] { background:#172334; border:1px solid #26374d; border-radius:12px; padding:12px 14px; }
      [data-testid="stMetricLabel"] { color:#aab9cc; }
      [data-testid="stMetricValue"] { color:#f3f7fc; }
      [data-testid="stMarkdownContainer"] p { color:#c2cede; }
      [data-testid="stVerticalBlockBorderWrapper"] { border-color:#26374d; }
      .overview-kicker { color:#56d4b0; font-size:.78rem; letter-spacing:.12em; font-weight:700; }
      .overview-title { color:#f4f7fb; font-size:1.55rem; font-weight:750; margin:.1rem 0 .25rem; }
      .overview-panel { background:#121e2d; border:1px solid #26374d; border-radius:14px; padding:14px 16px; margin:0 0 12px; }
      .overview-panel-title { color:#edf3fa; font-weight:700; margin-bottom:5px; }
      .overview-muted { color:#9eafc3; font-size:.82rem; line-height:1.5; }
      .overview-ok { color:#59d7b4; font-weight:700; }
    </style>
    """, unsafe_allow_html=True)


def _page_overview() -> None:
    _apply_dashboard_theme()
    stations = _latest_station_frame()
    latest = {row["dataset_id"]: row for row in get_latest_ingestion_summary()}
    station_run = latest.get("O-A0001-001")
    observed_at = stations["obs_time"].max() if not stations.empty else None
    temp_values = stations["temperature"].dropna() if not stations.empty else pd.Series(dtype=float)
    rain_values = stations["precipitation"].dropna() if not stations.empty else pd.Series(dtype=float)
    wind_values = stations["wind_speed"].dropna() if not stations.empty else pd.Series(dtype=float)
    dark_basemap = st.session_state.get("overview_basemap", "深色") == "深色"

    left, center, right = st.columns([2.25, 8.1, 2.25], gap="small")
    with left:
        st.markdown('<div class="overview-kicker">CWA · TAIWAN</div><div class="overview-title">台灣即時氣象</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="overview-panel"><div class="overview-panel-title">● 觀測資料狀態</div>'
            f'<div class="overview-muted">來源：全測站逐時觀測<br>觀測時間：{escape(_timestamp(observed_at))}<br>'
            f'有效測站：{len(stations)} 站<br>同步狀態：<span class="overview-ok">{escape(station_run["status"] if station_run else "尚未匯入")}</span></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown("**即時觀測摘要**")
        c1, c2 = st.columns(2, gap="small")
        c1.metric("最高氣溫", f"{temp_values.max():.1f} °C" if not temp_values.empty else "—")
        c2.metric("最低氣溫", f"{temp_values.min():.1f} °C" if not temp_values.empty else "—")
        c1.metric("最大雨量", f"{rain_values.max():.1f} mm" if not rain_values.empty else "—")
        c2.metric("最大風速", f"{wind_values.max():.1f} m/s" if not wind_values.empty else "—")
        if not stations.empty and not temp_values.empty:
            hottest = stations.loc[stations["temperature"].idxmax()]
            coolest = stations.loc[stations["temperature"].idxmin()]
            st.markdown(
                f'<div class="overview-panel"><div class="overview-panel-title">溫度極值測站</div>'
                f'<div class="overview-muted">最高　{escape(str(hottest["station_name"]))} · {hottest["temperature"]:.1f} °C<br>'
                f'最低　{escape(str(coolest["station_name"]))} · {coolest["temperature"]:.1f} °C</div></div>',
                unsafe_allow_html=True,
            )
        st.caption("氣溫標籤使用各站最近一筆觀測。點選地圖標記可查看站名、時間、雨量與風速。")

    with center:
        st.subheader("全台測站觀測分布")
        tsunami_run = latest.get("E-A0014-001")
        tsunami = _read("SELECT issue_time,report_type,report_color,report_content,valid_end_time FROM tsunami_events ORDER BY issue_time DESC LIMIT 1")
        if tsunami_run and tsunami_run["status"] == "FAILED":
            st.warning("海嘯資料最近更新失敗；請以中央氣象署官方公告為準。")
        elif not tsunami.empty:
            notice = tsunami.iloc[0]
            expired = False
            try:
                end = datetime.fromisoformat(str(notice["valid_end_time"]).replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                expired = end.astimezone(timezone.utc) < datetime.now(timezone.utc)
            except (ValueError, TypeError):
                pass
            if not expired and ("解除" in str(notice["report_type"] or "") or notice["report_color"] == "綠色"):
                st.success(f"最新海嘯資料：{notice['report_type'] or '報告'}（{notice['report_color'] or '未標示'}）")
            elif not expired:
                st.warning(f"海嘯資訊提醒：{notice['report_type'] or '最新報告'}（{notice['report_color'] or '未標示'}） · {_timestamp(notice['issue_time'])}")
            else:
                st.info(f"最近海嘯報告已超過有效時間（{_timestamp(notice['issue_time'])}），不代表目前警報。")
        else:
            st.info("資料庫目前沒有海嘯報告；此狀態不等同官方即時安全告警。")

        with st.container(border=True):
            _weather_overview_map(
                stations,
                dark_basemap=dark_basemap,
                show_temperature=st.session_state.get("overview_show_temperature", False),
                show_rain=st.session_state.get("overview_show_rain", False),
                show_wind=st.session_state.get("overview_show_wind", False),
            )
            st.caption(f"地圖底圖：OpenStreetMap {'深色樣式' if dark_basemap else '標準街道'} · 不需要底圖 API key · 測站資料：{_timestamp(observed_at)}")

    with right:
        st.subheader("圖層與底圖")
        st.radio("底圖樣式", ["深色", "街道"], horizontal=True, key="overview_basemap")
        st.checkbox("顯示氣溫標籤", value=False, key="overview_show_temperature")
        st.checkbox("顯示降雨標記", value=False, key="overview_show_rain")
        st.checkbox("顯示風速標記", value=False, key="overview_show_wind")
        st.markdown("**圖例**")
        st.markdown("🟢 **低於 24°C**　🟡 **24–28.9°C**　🟠 **29°C 以上**")
        st.caption("藍色圓圈為有雨測站，紫色圓圈為風速觀測；圓圈大小依數值調整。")
        st.markdown("**資料來源**")
        st.caption("中央氣象署 O-A0001-001 全測站逐時氣象資料。底圖使用 OpenStreetMap，保留地圖授權標示。")
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
        m = folium.Map(location=[latest["epicenter_lat"], latest["epicenter_lon"]], zoom_start=5, tiles="OpenStreetMap")
        folium.Marker([latest["epicenter_lat"], latest["epicenter_lon"]], tooltip="最新報告震央", popup=latest.get("epicenter_location") or "震央").add_to(m)
        st_folium(m, width=1000, height=360, key="tsunami_map", returned_objects=[])
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
    m = folium.Map(location=[22.5, 130.0], zoom_start=4, tiles="OpenStreetMap", control_scale=True)
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
    st_folium(m, width=1000, height=560, key="typhoon_probability_map", returned_objects=[])
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
    m = folium.Map(location=[20.0, 135.0], zoom_start=4, tiles="OpenStreetMap", control_scale=True)
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
    st_folium(m, width=1000, height=560, key="typhoon_track_map", returned_objects=[])
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
