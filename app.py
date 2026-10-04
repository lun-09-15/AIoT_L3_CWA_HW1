"""Streamlit application for CWA marine, observation, earthquake and hazard data."""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

import folium
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from branca.element import Element
from folium.plugins import LocateControl
from streamlit_folium import st_folium

from src.config import BASE_DIR, CWA_API_KEY, DB_PATH, MOENV_API_KEY
from src.database import get_latest_ingestion_summary, init_db, query_rows
from src.datasets.radar_echo import load_recent_radar_frames, radar_animation_html
from src.datasets.specs import DATASET_SPECS
from src.datasets.typhoon import parse_typhoon_probability_kmz

st.set_page_config(page_title="CWA 海氣象與災害資訊", page_icon="🌦️", layout="wide")
init_db()

PAGES = [
    "總覽", "海面天氣預報", "氣象觀測站", "地震資訊", "海嘯資訊", "溫度分布狀態", "雷達回波", "颱風侵襲機率", "熱帶氣旋路徑",
]
DATASET_FOR_PAGE = {
    "海面天氣預報": "F-A0012-001",
    "氣象觀測站": "O-A0001-001",
    "地震資訊": ("E-A0015-001", "E-A0016-001"),
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


def _freshness_overview(latest: Dict[str, Dict[str, Any]]) -> None:
    # Allow twice the published interval for normal delivery/network variance.
    freshness_limits = {
        "F-A0012-001": 12,
        "O-A0001-001": 2,
        "O-A0038-001": 2,
        "W-C0034-003": 12,
        "W-C0034-005": 12,
    }
    rows = []
    for dataset_id, spec in DATASET_SPECS.items():
        run = latest.get(dataset_id)
        if not run:
            status = "⚪ 尚未匯入"
            fetched_at = "—"
            source_time = "—"
        else:
            run_status = str(run.get("status", "UNKNOWN")).upper()
            fetched_at = _timestamp(run.get("run_time"))
            source_time = _timestamp(run.get("data_timestamp"))
            if run_status == "FAILED":
                status = "🔴 更新失敗"
            elif run_status == "EMPTY":
                status = "⚪ 來源無可呈現資料"
            elif dataset_id in freshness_limits:
                checked_time = run.get("data_timestamp") or run.get("run_time")
                freshness = _freshness(checked_time, freshness_limits[dataset_id])
                status = "🟢 新鮮" if freshness == "資料新鮮" else "🟡 可能過期" if "可能已過期" in freshness else "⚪ 時間無法判讀"
            else:
                # Tsunami and typhoon feeds are event-driven; an old event timestamp
                # does not mean the feed is stale when there is no active event.
                status = "🟢 擷取成功（事件型）"
        rows.append({
            "資料集": dataset_id,
            "資料名稱": spec.official_name,
            "狀態": status,
            "來源資料時間": source_time,
            "最近擷取時間": fetched_at,
        })
    air_run = latest.get("AQX_P_432")
    if not MOENV_API_KEY:
        air_status = "⚪ 未設定環境部 API Key"
    elif not air_run:
        air_status = "⚪ 尚未匯入"
    elif air_run.get("status") == "FAILED":
        air_status = "🔴 更新失敗"
    elif air_run.get("status") == "EMPTY":
        air_status = "⚪ 來源無可呈現資料"
    else:
        freshness = _freshness(air_run.get("data_timestamp") or air_run.get("run_time"), 2)
        air_status = "🟢 新鮮" if freshness == "資料新鮮" else "🟡 可能過期" if "可能已過期" in freshness else "⚪ 時間無法判讀"
    rows.append({
        "資料集": "AQX_P_432", "資料名稱": "空氣品質指標（環境部）", "狀態": air_status,
        "來源資料時間": _timestamp(air_run.get("data_timestamp")) if air_run else "—",
        "最近擷取時間": _timestamp(air_run.get("run_time")) if air_run else "—",
    })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("固定週期資料依更新頻率判斷新鮮度；海嘯與颱風屬事件型資料，舊事件時間不代表資料過期。狀態依最近一次擷取紀錄，按左側「更新全部資料」可重新檢查。")


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
    frame = _read("""SELECT s.* FROM station_observations s
                    JOIN (SELECT station_id,MAX(obs_time) AS obs_time FROM station_observations GROUP BY station_id) latest
                    ON s.station_id=latest.station_id AND s.obs_time=latest.obs_time
                    ORDER BY s.county_name,s.station_name""")
    if frame.empty:
        # Keep the expected schema so an empty/new Cloud database still renders
        # the dashboard and can show its normal no-data state.
        return pd.DataFrame(columns=[
            "id", "station_id", "station_name", "county_name", "town_name",
            "latitude", "longitude", "altitude", "obs_time", "temperature",
            "relative_humidity", "wind_speed", "wind_direction", "precipitation",
            "air_pressure", "updated_at",
        ])
    return frame


@st.cache_data(ttl=60, show_spinner=False)
def _latest_air_quality_frame() -> pd.DataFrame:
    frame = _read("""SELECT a.* FROM air_quality_observations a
                    JOIN (SELECT site_id,MAX(publish_time) AS publish_time FROM air_quality_observations GROUP BY site_id) latest
                    ON a.site_id=latest.site_id AND a.publish_time=latest.publish_time
                    ORDER BY a.county_name,a.site_name""")
    if frame.empty:
        return pd.DataFrame(columns=[
            "id", "site_id", "site_name", "county_name", "publish_time", "aqi",
            "status", "pollutant", "pm25", "pm25_avg", "pm10", "ozone",
            "longitude", "latitude", "updated_at",
        ])
    return frame


def _aqi_style(value: Any) -> tuple[str, str]:
    if pd.isna(value):
        return "#738091", "無 AQI"
    aqi = float(value)
    if aqi <= 50:
        return "#35b779", "良好"
    if aqi <= 100:
        return "#f2d34f", "普通"
    if aqi <= 150:
        return "#f39c45", "對敏感族群不健康"
    if aqi <= 200:
        return "#e34a4a", "對所有族群不健康"
    if aqi <= 300:
        return "#9b59b6", "非常不健康"
    return "#7e2635", "危害"


def _weather_overview_map(
    frame: pd.DataFrame,
    dark_basemap: bool,
    show_temperature: bool,
    show_rain: bool,
    show_wind: bool,
    county_key: str,
    selected_county: str,
    air_quality_frame: pd.DataFrame,
    show_air_quality: bool,
    air_metric: str,
) -> None:
    mapped = frame.dropna(subset=["latitude", "longitude"]).copy()
    air_mapped = air_quality_frame.dropna(subset=["latitude", "longitude"]).copy()
    if mapped.empty and (not show_air_quality or air_mapped.empty):
        st.info("目前尚無含有效 WGS84 座標的測站資料。請從側邊欄更新氣象觀測站資料。")
        return

    is_county_filtered = selected_county != "全部縣市"
    center_frame = mapped if not mapped.empty else air_mapped
    map_center = (
        [float(center_frame["latitude"].median()), float(center_frame["longitude"].median())]
        if is_county_filtered else [23.7, 121.0]
    )
    weather_map = folium.Map(
        location=map_center, zoom_start=9 if is_county_filtered else 7, tiles=None, control_scale=True,
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
    air_quality_layer = folium.FeatureGroup(name="空氣品質測站", show=True) if show_air_quality else None

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

    if air_quality_layer is not None and not air_mapped.empty:
        for row in air_mapped.itertuples(index=False):
            aqi = row.aqi
            value = aqi if air_metric == "AQI" else row.pm25
            if pd.isna(value):
                continue
            color, level = _aqi_style(aqi)
            value_text = f"{float(value):.0f}" if air_metric == "AQI" else f"{float(value):.1f}"
            site_name = escape(str(row.site_name or "空品測站"))
            county = escape(str(row.county_name or ""))
            pollutant = escape(str(row.pollutant or "未提供"))
            monitor_status = escape(str(row.status or "未提供"))
            marker = folium.DivIcon(
                icon_size=(48, 25), icon_anchor=(24, 12),
                html=(f'<div style="background:{color};color:#17202a;border:1px solid #fff;'
                      f'border-radius:16px;padding:2px 7px;font:bold 12px Arial;text-align:center;'
                      f'box-shadow:0 2px 8px #0008;white-space:nowrap">{value_text}</div>'),
            )
            popup = (
                f"<b>{site_name}</b> · {county}<br>AQI：{aqi if pd.notna(aqi) else '—'}（{level}）<br>"
                f"PM2.5：{row.pm25 if pd.notna(row.pm25) else '—'} μg/m³<br>主要污染物：{pollutant}<br>"
                f"狀態：{monitor_status}<br>發布時間：{escape(_timestamp(row.publish_time))}"
            )
            folium.Marker(
                [row.latitude, row.longitude], icon=marker,
                tooltip=f"{site_name} · {air_metric} {value_text}",
                popup=folium.Popup(popup, max_width=300),
            ).add_to(air_quality_layer)

    layers = [station_layer]
    layers.extend(layer for layer in (temperature_layer, rain_layer, wind_layer, air_quality_layer) if layer is not None)
    for layer in layers:
        layer.add_to(weather_map)
    LocateControl(
        position="topleft",
        strings={"title": "定位我的裝置", "popup": "您的裝置位置"},
        locateOptions={"enableHighAccuracy": True, "timeout": 12000, "maximumAge": 60000},
    ).add_to(weather_map)
    map_key = f"overview_map_{county_key}_{int(dark_basemap)}_{int(show_temperature)}_{int(show_rain)}_{int(show_wind)}_{int(show_air_quality)}_{air_metric}"
    st_folium(weather_map, width=960, height=610, key=map_key, returned_objects=[])


def _apply_dashboard_theme() -> None:
    st.markdown("""
    <style>
      :root { color-scheme:dark; --dash-bg:#0b1220; --dash-surface:#111c2c; --dash-card:#162438; --dash-border:#293950; --dash-text:#edf3fb; --dash-muted:#9aacc2; --dash-accent:#55d6b2; }
      .stApp, [data-testid="stAppViewContainer"] { background:radial-gradient(ellipse at 58% -12%,#18334a 0,transparent 44%),var(--dash-bg); color:var(--dash-text); }
      [data-testid="stHeader"] { background:rgba(11,18,32,.88); backdrop-filter:blur(14px); }
      [data-testid="stMainBlockContainer"] { max-width:100%; padding:3.8rem clamp(1rem,2.2vw,2.2rem) 2rem !important; }
      [data-testid="stSidebar"] { background:linear-gradient(180deg,#111e30 0%,#0e1827 100%); border-right:1px solid #25354b; }
      [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color:#aebdd0; }
      [data-testid="stSidebar"] [data-testid="stRadio"] label { border-radius:9px; transition:background .16s ease,color .16s ease; }
      [data-testid="stSidebar"] [data-testid="stRadio"] label:hover { background:#1b2b40; color:#fff; }
      h1 { color:#f5f8fc !important; font-size:clamp(1.75rem,2.5vw,2.35rem) !important; letter-spacing:-.035em; font-weight:760 !important; margin-bottom:.2rem !important; }
      h2, h3 { color:#eaf1fa !important; letter-spacing:-.02em; }
      [data-testid="stCaptionContainer"] p { color:var(--dash-muted) !important; line-height:1.55; }
      [data-testid="stMetric"] { background:linear-gradient(145deg,#192a40,#142236); border:1px solid var(--dash-border); border-radius:15px; padding:14px 16px; box-shadow:0 8px 24px #03091430; transition:transform .16s ease,border-color .16s ease; }
      [data-testid="stMetric"]:hover { transform:translateY(-2px); border-color:#42617e; }
      [data-testid="stMetricLabel"] { color:#aebdd0; }
      [data-testid="stMetricValue"] { color:#f4f8ff; font-weight:700; }
      [data-testid="stMarkdownContainer"] p { color:#c7d2e1; }
      [data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--dash-border); border-radius:14px; }
      [data-testid="stExpander"] { background:rgba(17,28,44,.64); border:1px solid #283a51; border-radius:13px; overflow:hidden; }
      [data-testid="stAlert"] { border-radius:12px; }
      [data-testid="stDataFrame"] { border:1px solid #293950; border-radius:12px; overflow:hidden; }
      [data-testid="stPlotlyChart"], [data-testid="stLineChart"] { background:rgba(17,28,44,.5); border:1px solid #293950; border-radius:14px; padding:8px; }
      [data-testid="stImage"] img { border:1px solid #293950; border-radius:14px; box-shadow:0 12px 32px #02071155; }
      [data-testid="stSelectbox"] [data-baseweb="select"] > div, [data-testid="stMultiSelect"] [data-baseweb="select"] > div { border-color:#334963; border-radius:10px; }
      [data-testid="stTextInput"] input { border-color:#334963; border-radius:10px; }
      .stButton > button, [data-testid="stDownloadButton"] > button, [data-testid="stLinkButton"] > a { border-radius:10px; transition:transform .16s ease,border-color .16s ease,filter .16s ease; }
      .stButton > button:hover, [data-testid="stDownloadButton"] > button:hover, [data-testid="stLinkButton"] > a:hover { transform:translateY(-1px); border-color:#55d6b2; filter:brightness(1.06); }
      .stButton > button[kind="primary"] { background:linear-gradient(105deg,#23bda1,#42d2ae); border:0; color:#06251f; font-weight:750; box-shadow:0 7px 18px #18b89a30; }
      a { color:#71d9c0 !important; }
      .overview-kicker { color:#56d4b0; font-size:.78rem; letter-spacing:.12em; font-weight:700; }
      .overview-title { color:#f4f7fb; font-size:1.55rem; font-weight:750; margin:.1rem 0 .25rem; }
      .overview-panel { background:linear-gradient(145deg,#142438,#111c2b); border:1px solid #2b4058; border-radius:15px; padding:14px 16px; margin:0 0 12px; box-shadow:0 8px 24px #03091424; }
      .overview-panel-title { color:#edf3fa; font-weight:700; margin-bottom:5px; }
      .overview-muted { color:#9eafc3; font-size:.82rem; line-height:1.5; }
      .overview-ok { color:#59d7b4; font-weight:700; }
    </style>
    """, unsafe_allow_html=True)


def _page_overview() -> None:
    all_stations = _latest_station_frame()
    air_quality = _latest_air_quality_frame()
    latest = {row["dataset_id"]: row for row in get_latest_ingestion_summary()}
    dark_basemap = st.session_state.get("overview_basemap", "深色") == "深色"
    show_air_quality = st.session_state.get("overview_show_air_quality", False)
    air_metric = st.session_state.get("overview_air_metric", "AQI")

    with st.expander("資料新鮮度與更新狀態", expanded=False):
        _freshness_overview(latest)

    left, center, right = st.columns([2.25, 8.1, 2.25], gap="small")
    with left:
        counties = ["全部縣市"] + sorted(all_stations["county_name"].dropna().unique().tolist())
        selected_county = st.selectbox("總覽縣市篩選", counties, key="overview_county")
        stations = all_stations if selected_county == "全部縣市" else all_stations[all_stations["county_name"] == selected_county]
        filtered_air_quality = air_quality if selected_county == "全部縣市" else air_quality[air_quality["county_name"] == selected_county]
        station_run = latest.get("O-A0001-001")
        observed_at = stations["obs_time"].max() if not stations.empty else None
        temp_values = stations["temperature"].dropna() if not stations.empty else pd.Series(dtype=float)
        rain_values = stations["precipitation"].dropna() if not stations.empty else pd.Series(dtype=float)
        wind_values = stations["wind_speed"].dropna() if not stations.empty else pd.Series(dtype=float)
        st.markdown('<div class="overview-kicker">CWA · TAIWAN</div><div class="overview-title">台灣即時氣象</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="overview-panel"><div class="overview-panel-title">● 觀測資料狀態</div>'
            f'<div class="overview-muted">來源：全測站逐時觀測<br>觀測時間：{escape(_timestamp(observed_at))}<br>'
            f'有效測站：{len(stations)} 站<br>同步狀態：<span class="overview-ok">{escape(station_run["status"] if station_run else "尚未匯入")}</span></div></div>',
            unsafe_allow_html=True,
        )
        if stations.empty:
            st.info("目前沒有可呈現的測站觀測資料。請檢查資料新鮮度與更新狀態、確認 CWA_API_KEY 已設定，再按「更新全部資料」重試。")
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
                county_key=str(counties.index(selected_county)),
                selected_county=selected_county,
                air_quality_frame=filtered_air_quality,
                show_air_quality=show_air_quality,
                air_metric=air_metric,
            )
            st.caption(f"地圖底圖：OpenStreetMap {'深色樣式' if dark_basemap else '標準街道'} · 不需要底圖 API key · 測站資料：{_timestamp(observed_at)}")

    with right:
        st.subheader("圖層與底圖")
        st.radio("底圖樣式", ["深色", "街道"], horizontal=True, key="overview_basemap")
        st.checkbox("顯示氣溫標籤", value=False, key="overview_show_temperature")
        st.checkbox("顯示降雨標記", value=False, key="overview_show_rain")
        st.checkbox("顯示風速標記", value=False, key="overview_show_wind")
        st.checkbox("顯示空氣品質測站", value=False, key="overview_show_air_quality")
        if st.session_state.get("overview_show_air_quality", False):
            st.radio("空品標籤數值", ["AQI", "PM2.5"], horizontal=True, key="overview_air_metric")
            if air_quality.empty:
                st.info("尚無空品資料；請設定 MOENV_API_KEY 後更新資料。")
            else:
                st.caption("標籤顏色依 AQI 等級，數字依上方選項顯示。")
        st.markdown("**圖例**")
        st.markdown("🟢 **低於 24°C**　🟡 **24–28.9°C**　🟠 **29°C 以上**")
        st.caption("空品標記依 AQI 分級著色，點擊可看 AQI、PM2.5、污染物和發布時間。")
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
    st.dataframe(display, hide_index=True, width="stretch")
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
    station_labels = {
        row.station_id: f"{row.station_name}（{row.station_id}）"
        for row in station_options.itertuples(index=False)
    }
    with st.expander("多測站趨勢比較", expanded=False):
        compare_ids = st.multiselect(
            "選擇測站（最多比較 5 站）",
            options=station_options["station_id"].tolist(),
            default=station_options["station_id"].head(3).tolist(),
            format_func=lambda value: station_labels.get(value, value),
            key="station_compare_ids",
        )
        compare_metric_label = st.selectbox(
            "比較觀測項目", list(metric_options), key="station_compare_metric"
        )
        if len(compare_ids) > 5:
            st.warning("為保持圖表易讀，一次最多顯示 5 個測站；目前先呈現前 5 站。")
            compare_ids = compare_ids[:5]
        if compare_ids:
            compare_column = metric_options[compare_metric_label]
            compare_frames = []
            for compare_id in compare_ids:
                history_part = _read(
                    f"SELECT obs_time,{compare_column} FROM station_observations "
                    "WHERE station_id=? ORDER BY obs_time DESC LIMIT 48",
                    (compare_id,),
                )
                if not history_part.empty:
                    history_part["測站"] = station_labels[compare_id]
                    compare_frames.append(history_part)
            if compare_frames:
                comparison = pd.concat(compare_frames, ignore_index=True)
                comparison = comparison.pivot(index="obs_time", columns="測站", values=compare_column).sort_index()
                comparison.columns.name = None
                st.line_chart(comparison, height=360)
                st.caption(f"各站最近 48 筆資料 · {compare_metric_label} · 時間未對齊時保留空值。")
            else:
                st.info("所選測站目前沒有可比較的歷史資料。")
        else:
            st.info("請至少選擇一個測站。")
    table = frame[["station_id", "station_name", "county_name", "town_name", "obs_time", "temperature", "relative_humidity", "wind_speed", "wind_direction", "precipitation", "air_pressure"]].copy()
    table.columns = ["站碼", "測站", "縣市", "鄉鎮", "觀測時間", "氣溫 °C", "濕度 %", "風速 m/s", "風向 °", "雨量 mm", "氣壓 hPa"]
    st.dataframe(table, hide_index=True, width="stretch")
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
    st.dataframe(history, hide_index=True, width="stretch", column_config={"官方報告": st.column_config.LinkColumn()})
    _download_csv(history, "tsunami_reports.csv")


def _page_earthquake() -> None:
    st.title("🌏 地震資訊")
    st.caption("CWA E-A0015-001 顯著有感地震 + E-A0016-001 小區域有感地震；地震報告與海嘯警報分開呈現。")
    for dataset_id, name in (("E-A0015-001", "顯著有感地震"), ("E-A0016-001", "小區域有感地震")):
        with st.expander(f"{name}資料擷取狀態", expanded=False):
            _show_ingestion_status(dataset_id)

    events = _read("SELECT * FROM earthquake_events ORDER BY origin_time DESC, id DESC LIMIT 1000")
    if events.empty:
        return _empty("目前資料庫沒有地震報告。請確認 CWA_API_KEY 並按左側「更新全部資料」。")

    dataset_labels = {"全部": None, "顯著有感地震": "E-A0015-001", "小區域有感地震": "E-A0016-001"}
    selected = st.selectbox("報告類型", list(dataset_labels), key="earthquake_dataset_filter")
    if dataset_labels[selected]:
        events = events[events["dataset_id"] == dataset_labels[selected]]
    if events.empty:
        return _empty("此類型目前沒有已匯入的地震報告。")

    latest = events.iloc[0]
    cols = st.columns(4)
    cols[0].metric("顯示報告數", f"{len(events)}")
    cols[1].metric("最新規模", f"{latest['magnitude']:.1f}" if pd.notna(latest.get("magnitude")) else "—")
    max_intensity = latest.get("max_intensity")
    cols[2].metric("最大震度", max_intensity if pd.notna(max_intensity) and max_intensity else "—")
    cols[3].metric("最近發生時間", _timestamp(latest.get("origin_time")))

    mapped = events.dropna(subset=["epicenter_lat", "epicenter_lon"])
    if not mapped.empty:
        center = [float(mapped.iloc[0]["epicenter_lat"]), float(mapped.iloc[0]["epicenter_lon"])]
        m = folium.Map(location=center, zoom_start=5, tiles="OpenStreetMap", control_scale=True)
        for _, row in mapped.head(300).iterrows():
            dataset_id = str(row["dataset_id"])
            source_name = "顯著有感" if dataset_id == "E-A0015-001" else "小區域有感"
            magnitude = row.get("magnitude")
            radius = max(4, min(13, 3 + (float(magnitude) * 1.2 if pd.notna(magnitude) else 2)))
            color = "#ff5b5b" if dataset_id == "E-A0015-001" else "#f0b44d"
            popup = (
                f"<b>{escape(source_name)}地震 · 規模 {escape(str(magnitude if pd.notna(magnitude) else '—'))}</b><br>"
                f"{escape(str(row.get('epicenter_location') or '震央位置未提供'))}<br>"
                f"發生：{escape(_timestamp(row.get('origin_time')))}<br>"
                f"深度：{escape(str(row.get('focal_depth') if pd.notna(row.get('focal_depth')) else '—'))} km<br>"
                f"最大震度：{escape(str(row.get('max_intensity') or '—'))}<br>"
                f"{escape(str(row.get('report_content') or ''))}"
            )
            folium.CircleMarker(
                location=[float(row["epicenter_lat"]), float(row["epicenter_lon"])],
                radius=radius, color=color, weight=2, fill=True, fill_color=color,
                fill_opacity=0.65, tooltip=f"{source_name} · 規模 {magnitude if pd.notna(magnitude) else '—'}",
                popup=folium.Popup(popup, max_width=360),
            ).add_to(m)
        st_folium(m, width="stretch", height=520, key="earthquake_map", returned_objects=[])
        st.caption("紅色為顯著有感地震，黃色為小區域有感地震；圓點大小僅用規模作視覺提示。僅繪製官方提供有效震央座標的報告。")
    else:
        st.info("報告目前沒有可用震央座標，以下仍顯示文字與表格資料。")

    table = events.head(200)[[
        "dataset_id", "origin_time", "epicenter_location", "magnitude", "focal_depth",
        "max_intensity", "report_content", "report_image_uri", "web_url",
    ]].copy()
    table["dataset_id"] = table["dataset_id"].map({"E-A0015-001": "顯著有感", "E-A0016-001": "小區域有感"})
    table.columns = ["報告類型", "發生時間", "震央", "規模", "深度 km", "最大震度", "報告內容", "震度圖", "官方報告"]
    st.subheader("地震報告")
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "震度圖": st.column_config.LinkColumn(), "官方報告": st.column_config.LinkColumn(),
    })
    _download_csv(table, "cwa_earthquake_reports.csv")


def _page_temperature() -> None:
    st.title("🌡️ 溫度分布狀態")
    st.caption("資料集 O-A0038-001 · 官方影像產品；圖面色彩不反推為精確逐點數值")
    _show_ingestion_status("O-A0038-001")
    maps = _read("SELECT * FROM temperature_maps ORDER BY obs_time DESC LIMIT 1")
    if maps.empty:
        return _empty()
    row = maps.iloc[0]
    st.caption(f"觀測時間：{_timestamp(row['obs_time'])} · {_freshness(row['obs_time'], 2)} · 範圍 {row.get('lat_range') or '—'} N, {row.get('lon_range') or '—'} E")
    st.image(row["image_url"], caption=f"中央氣象署溫度分布圖 · {_timestamp(row['obs_time'])}", width="stretch")
    st.link_button("開啟原始影像", row["image_url"])


@st.cache_data(ttl=300, show_spinner=False)
def _cached_radar_frames() -> List[Dict[str, Any]]:
    return load_recent_radar_frames(frame_count=7)


def _page_radar_echo() -> None:
    st.title("🌧️ 雷達回波")
    st.caption("資料集 O-A0059-001 · 使用雷達整合回波數值格點（dBZ），以地圖色階逐格繪製；不是官方 PNG 圖片。")
    st.caption("CWA 約每 10 分鐘更新。本頁按需載入最近約 1 小時、最多 7 個時次；資料取自 CWA 歷史 API，需設定 CWA_API_KEY。")
    reload_requested = st.button("⟳ 載入／更新最近雷達資料", type="primary", width="stretch")
    if not CWA_API_KEY:
        st.warning("尚未設定 CWA_API_KEY，無法取得中央氣象署雷達格點資料。")
        return

    if reload_requested:
        _cached_radar_frames.clear()
    if reload_requested or "radar_echo_frames" not in st.session_state:
        try:
            with st.spinner("正在取得最近雷達格點並建立動畫…"):
                st.session_state["radar_echo_frames"] = _cached_radar_frames()
        except Exception as exc:
            st.error(f"雷達資料載入失敗：{str(exc)[:240]}")
            st.caption("請確認 CWA_API_KEY 有效、網路可連線，或稍後重試。")
            return

    frames = st.session_state.get("radar_echo_frames", [])
    if len(frames) < 2:
        st.info("目前可播放的雷達時次不足。請稍後重新載入。")
        return
    st.caption(f"可播放 {len(frames)} 個時次 · 最新資料時間：{_timestamp(frames[-1]['timestamp'])} · dBZ 顏色為回波強度分級示意")
    components.html(radar_animation_html(frames), height=660, scrolling=False)
    st.caption("色階：5–10、10–20、20–30、30–40、40–50、50–60、60 dBZ 以上。格點經裁切及降採樣以提升瀏覽效能；定位為雷達回波概覽。資料來源：中央氣象署 O-A0059-001；底圖：OpenStreetMap。")


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
    st.dataframe(detail, hide_index=True, width="stretch")
    _download_csv(detail, "typhoon_track.csv")


def _sync_all() -> None:
    if CWA_API_KEY:
        with st.sidebar.status("正在更新八項中央氣象署資料…", expanded=True) as status:
            try:
                from src.ingest import ingest_all
                from src.cwa_client import CWAClient
                results = ingest_all(CWAClient())
                failed = [dataset_id for dataset_id, result in results.items() if result["status"] == "FAILED"]
                status.update(label="中央氣象署資料更新完成" if not failed else f"中央氣象署資料更新完成，{len(failed)} 項失敗", state="complete" if not failed else "error")
                st.session_state["last_sync_results"] = results
            except Exception as exc:
                status.update(label="中央氣象署資料更新失敗", state="error")
                st.sidebar.error(str(exc))
    else:
        st.sidebar.warning("未設定 CWA_API_KEY，略過中央氣象署資料更新。")

    if MOENV_API_KEY:
        with st.sidebar.status("正在更新環境部空氣品質資料…", expanded=True) as status:
            from src.air_quality_ingest import sync_air_quality
            result = sync_air_quality()
            failed = result["status"] == "FAILED"
            status.update(
                label="空氣品質資料更新完成" if not failed else "空氣品質資料更新失敗",
                state="error" if failed else "complete",
            )
            if failed:
                st.sidebar.error(result["message"])
    else:
        st.sidebar.warning("未設定 MOENV_API_KEY，略過環境部空氣品質資料更新。")
    st.cache_data.clear()


_apply_dashboard_theme()
st.sidebar.title("資料導覽")
page = st.sidebar.radio("選擇資料區塊", PAGES, label_visibility="collapsed")
st.sidebar.button("⟳ 更新全部資料", on_click=_sync_all, width="stretch", type="primary")
st.sidebar.caption("新工作階段會自動更新；也可手動同步。CWA 與環境部空品各需自己的 API Key。")
st.sidebar.caption(f"資料庫：{DB_PATH.name}")

if not st.session_state.get("startup_sync_attempted", False):
    st.session_state["startup_sync_attempted"] = True
    if CWA_API_KEY or MOENV_API_KEY:
        _sync_all()
        st.rerun()
    else:
        st.sidebar.warning("自動更新需要設定 CWA_API_KEY 或 MOENV_API_KEY；目前顯示已儲存的資料。")

if page == "總覽":
    _page_overview()
elif page == "海面天氣預報":
    _page_marine()
elif page == "氣象觀測站":
    _page_stations()
elif page == "地震資訊":
    _page_earthquake()
elif page == "海嘯資訊":
    _page_tsunami()
elif page == "溫度分布狀態":
    _page_temperature()
elif page == "雷達回波":
    _page_radar_echo()
elif page == "颱風侵襲機率":
    _page_typhoon_probability()
elif page == "熱帶氣旋路徑":
    _page_typhoon_track()

st.sidebar.markdown("---")
st.sidebar.caption("資料來源：中央氣象署與環境部開放資料平台。產品時間和更新頻率依官方資料為準。")
