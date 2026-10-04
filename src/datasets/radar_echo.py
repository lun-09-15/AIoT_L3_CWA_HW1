"""Fetch and prepare numerical CWA radar echo grids for animated map rendering."""
from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import requests

from src.cwa_client import CWAClient
from src.config import DEFAULT_TIMEOUT_SEC

DATASET_ID = "O-A0059-001"
HISTORY_API = "https://opendata.cwa.gov.tw/historyapi/v1"
FILE_API = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TIMESTAMP = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?")


def _key_name(key: Any) -> str:
    return re.sub(r"[^a-z]", "", str(key).lower())


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _find_scalar(payload: Any, names: set[str]) -> Optional[str]:
    for node in _walk(payload):
        if isinstance(node, dict):
            for key, value in node.items():
                if _key_name(key) in names and isinstance(value, (str, int, float)):
                    text = str(value).strip()
                    if text:
                        return text
    return None


def _parse_time(value: str) -> Optional[datetime]:
    text = value.strip().replace("Z", "+00:00").replace("/", "-")
    if len(text) == 16:
        text += ":00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _history_times(payload: Any) -> List[str]:
    """Return radar product times only; UpdateTime is not a downloadable frame time."""
    values: set[str] = set()
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if _key_name(key) not in {"datetime", "datatime", "obstime", "producttime"}:
                continue
            if not isinstance(value, str):
                continue
            match = _TIMESTAMP.search(value)
            if match and _parse_time(match.group()) is not None:
                values.add(match.group())
    return sorted(values, key=lambda item: _parse_time(item) or datetime.min)

def _load_payload(response, stage: str = "歷史資料") -> Any:
    if response.status_code >= 400:
        raise RuntimeError(f"CWA {stage} API HTTP {response.status_code}。")
    content_type = response.headers.get("Content-Type", "").lower()
    if "json" in content_type or response.text.lstrip().startswith(("{", "[")):
        payload = response.json()
        if isinstance(payload, dict) and str(payload.get("success", "true")).lower() == "false":
            raise ValueError("CWA 歷史資料 API 回報失敗。")
        return payload
    root = ET.fromstring(response.content)
    return _xml_to_dict(root)


def _xml_to_dict(root: ET.Element) -> Dict[str, Any]:
    """Convert XML to nested dictionaries while preserving repeated content text."""
    def convert(element: ET.Element) -> Any:
        children = list(element)
        if not children:
            return (element.text or "").strip()
        result: Dict[str, Any] = {}
        for child in children:
            key = child.tag.rsplit("}", 1)[-1]
            item = convert(child)
            if key in result:
                if not isinstance(result[key], list):
                    result[key] = [result[key]]
                result[key].append(item)
            else:
                result[key] = item
        return result
    return {root.tag.rsplit("}", 1)[-1]: convert(root)}


def parse_radar_grid(payload: Any) -> Dict[str, Any]:
    """Parse CWA JSON/XML radar grid fields and comma-separated reflectivity values."""
    lon = _find_scalar(payload, {"startpointlongitude"})
    lat = _find_scalar(payload, {"startpointlatitude"})
    resolution = _find_scalar(payload, {"gridresolution"})
    width = _find_scalar(payload, {"griddimensionx"})
    height = _find_scalar(payload, {"griddimensiony"})
    timestamp = _find_scalar(payload, {"datetime", "datatime"})
    content = None
    for node in _walk(payload):
        if isinstance(node, dict):
            for key, value in node.items():
                if _key_name(key) == "content" and isinstance(value, str) and _NUMBER.search(value):
                    content = value
                    break
        if content:
            break
    if not all((lon, lat, resolution, width, height, timestamp, content)):
        raise ValueError("雷達格點回應缺少座標、維度、時間或格點內容欄位。")
    nx, ny = int(float(width)), int(float(height))
    values = [float(match.group()) for match in _NUMBER.finditer(content)]
    if nx < 1 or ny < 1 or len(values) != nx * ny:
        raise ValueError(f"雷達格點數量不符（預期 {nx * ny:,}，收到 {len(values):,}）。")
    return {
        "timestamp": timestamp,
        "longitude": float(lon), "latitude": float(lat), "resolution": float(resolution),
        "width": nx, "height": ny, "values": values,
    }


def _crop_and_downsample(grid: Dict[str, Any]) -> Dict[str, Any]:
    """Keep Taiwan and nearby seas, and max-pool each 2x2 block for browser rendering."""
    lon0, lat0, step = grid["longitude"], grid["latitude"], grid["resolution"]
    x0 = max(0, int(math.floor((118.8 - lon0) / step)))
    x1 = min(grid["width"], int(math.ceil((123.8 - lon0) / step)))
    y0 = max(0, int(math.floor((21.2 - lat0) / step)))
    y1 = min(grid["height"], int(math.ceil((26.2 - lat0) / step)))
    stride = 2
    out_w = math.ceil((x1 - x0) / stride)
    out_h = math.ceil((y1 - y0) / stride)
    source = grid["values"]
    output: List[Optional[float]] = []
    for oy in range(out_h):
        sy = y0 + oy * stride
        for ox in range(out_w):
            sx = x0 + ox * stride
            block = [
                source[y * grid["width"] + x]
                for y in range(sy, min(sy + stride, y1))
                for x in range(sx, min(sx + stride, x1))
            ]
            valid = [value for value in block if value > -90]
            output.append(max(valid) if valid else None)
    return {
        "timestamp": grid["timestamp"],
        "longitude": lon0 + x0 * step,
        "latitude": lat0 + y0 * step,
        "resolution": step * stride,
        "width": out_w,
        "height": out_h,
        "values": output,
    }


def load_recent_radar_frames(client: Optional[CWAClient] = None, frame_count: int = 7) -> List[Dict[str, Any]]:
    """Fetch recent radar grids using the timestamp path advertised by CWA history metadata."""
    client = client or CWAClient()
    try:
        metadata_response = client.session.get(
            f"{HISTORY_API}/getMetadata/{DATASET_ID}",
            params={"Authorization": client.api_key},
            timeout=DEFAULT_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        raise RuntimeError(client._safe_error(exc)) from exc
    metadata = _load_payload(metadata_response, "雷達時次清單")
    timestamps = _history_times(metadata)[-frame_count:]
    if len(timestamps) < 2:
        raise ValueError("CWA 雷達歷史索引目前不足兩個時次，無法播放動畫。")

    frames: List[Dict[str, Any]] = []
    for timestamp in timestamps:
        parsed_time = _parse_time(timestamp)
        if parsed_time is None:
            raise ValueError("CWA 雷達歷史索引包含無法辨識的時次。")
        time_path = parsed_time.strftime("%Y/%m/%d/%H/%M/%S")
        data_url = f"{HISTORY_API}/getData/{DATASET_ID}/{time_path}"
        try:
            response = client.session.get(
                data_url,
                params={"Authorization": client.api_key},
                timeout=DEFAULT_TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            raise RuntimeError(client._safe_error(exc)) from exc
        frames.append(_crop_and_downsample(
            parse_radar_grid(_load_payload(response, f"雷達格點 {timestamp}"))
        ))
    frames.sort(key=lambda frame: _parse_time(frame["timestamp"]) or datetime.min)
    return frames

def radar_animation_html(frames: List[Dict[str, Any]]) -> str:
    """Build a Leaflet map that paints numeric dBZ cells on Canvas and animates frames."""
    encoded = json.dumps(frames, ensure_ascii=False, separators=(",", ":"))
    # Prevent a timestamp/string from closing the script element in generated HTML.
    encoded = encoded.replace("</", "<\\/")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body{{margin:0;padding:0;background:#0b1220;color:#eaf1fb;font:14px sans-serif}}
#controls{{height:56px;display:flex;align-items:center;gap:12px;padding:0 12px;box-sizing:border-box;background:#111c2c}}
#play{{border:1px solid #3b526d;border-radius:6px;background:#1b2d44;color:white;padding:7px 13px;cursor:pointer}}
#timeline{{flex:1;min-width:80px;accent-color:#54d6b2}}#time{{min-width:190px;text-align:right;color:#c5d3e4}}
#map{{height:calc(100vh - 56px);width:100%;background:#152538}}
.radar-canvas{{position:absolute;inset:0;z-index:400;pointer-events:none}}
.leaflet-control-attribution{{font-size:10px}}
</style></head><body>
<div id="controls"><button id="play" type="button">▶ 播放</button><input id="timeline" type="range" min="0" max="{len(frames)-1}" value="{len(frames)-1}"><span id="time"></span></div>
<div id="map"></div><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const frames={encoded};
const map=L.map('map',{{zoomControl:true,preferCanvas:true}}).setView([23.7,121.0],7);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:18,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}}).addTo(map);
const canvas=L.DomUtil.create('canvas','radar-canvas',map.getContainer());const ctx=canvas.getContext('2d');
const slider=document.getElementById('timeline'),button=document.getElementById('play'),timeLabel=document.getElementById('time');
let frameIndex=frames.length-1,timer=null;
function color(v){{if(v<5)return null;if(v<10)return '#79e7f5';if(v<20)return '#00a9e8';if(v<30)return '#20c55e';if(v<40)return '#facc15';if(v<50)return '#fb923c';if(v<60)return '#ef4444';return '#d946ef';}}
function draw(){{
 const size=map.getSize(),dpr=window.devicePixelRatio||1;canvas.width=size.x*dpr;canvas.height=size.y*dpr;canvas.style.width=size.x+'px';canvas.style.height=size.y+'px';ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,size.x,size.y);
 const f=frames[frameIndex],step=f.resolution;
 for(let y=0;y<f.height;y++)for(let x=0;x<f.width;x++){{const value=f.values[y*f.width+x];if(value===null||value<=-90)continue;const fill=color(value);if(!fill)continue;
   const west=f.longitude+x*step,south=f.latitude+y*step;
   const nw=map.latLngToContainerPoint([south+step,west]),se=map.latLngToContainerPoint([south,west+step]);
   if(se.x<0||nw.x>size.x||se.y<0||nw.y>size.y)continue;
   ctx.globalAlpha=.72;ctx.fillStyle=fill;ctx.fillRect(nw.x,nw.y,Math.max(1,se.x-nw.x+1),Math.max(1,se.y-nw.y+1));
 }}
 ctx.globalAlpha=1;timeLabel.textContent=f.timestamp.replace('T',' ').replace('+08:00',' 台灣時間');slider.value=frameIndex;
}}
function setFrame(i){{frameIndex=Math.max(0,Math.min(frames.length-1,i));draw();}}
slider.addEventListener('input',()=>setFrame(Number(slider.value)));
button.addEventListener('click',()=>{{if(timer){{clearInterval(timer);timer=null;button.textContent='▶ 播放';return;}}button.textContent='❚❚ 暫停';timer=setInterval(()=>setFrame((frameIndex+1)%frames.length),700);}});
map.on('moveend zoomend',draw);window.addEventListener('resize',draw);draw();
</script></body></html>"""
