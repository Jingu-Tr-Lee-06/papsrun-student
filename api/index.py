from datetime import datetime, timedelta
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import networkx as nx
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from api.core.route_engine import execute_papsrun_pipeline
from api.core.supabase_loader import fetch_class_network_from_supabase

DEFAULT_CLASS_CODE = os.environ.get("DEFAULT_CLASS_CODE", "CLASS_2026_A")
DEFAULT_TEMP_C = float(os.environ.get("DEFAULT_TEMP_C", "24.5"))
DEFAULT_SOLAR_WM2 = float(os.environ.get("DEFAULT_SOLAR_W_M2", "750.0"))
VWORLD_API_KEY = os.environ.get("VWORLD_API_KEY", "")
KMA_API_KEY = os.environ.get("KMA_API_KEY", "")

app = FastAPI(
    title="PAPSrun AI Campus Routing Engine",
    description="20인 1인 1모듈 통합 생체역학·미기상·위상수학·차분프라이버시 기반 캠퍼스 러닝 경로 최적화 API",
    version="1.4.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# [Pydantic 요청 스키마]
# ==============================================================================
class RoadNode(BaseModel):
    id: str
    lat: float
    lon: float
    elevation: float = 30.0
    label: Optional[str] = None


class RoadEdge(BaseModel):
    u: str
    v: str
    length: Optional[float] = None
    surface: str = "asphalt"
    is_shaded: bool = False


class RunnerProfile(BaseModel):
    paps_grade: int = Field(default=3, ge=1, le=5)
    weight: float = Field(default=62.0, ge=30.0, le=150.0)
    age: int = Field(default=17, ge=12, le=80)
    rest_hr: float = Field(default=70.0, ge=40.0, le=110.0)
    peak_hr: float = Field(default=145.0, ge=80.0, le=210.0)
    current_temp: Optional[float] = None
    solar_w: Optional[float] = None


class WeatherContext(BaseModel):
    temp_c: float = DEFAULT_TEMP_C
    solar_wm2: float = DEFAULT_SOLAR_WM2


class RouteRequest(BaseModel):
    class_code: Optional[str] = DEFAULT_CLASS_CODE
    runner_id: Optional[str] = None
    start_node: str
    end_node: Optional[str] = None
    route_mode: str = "A→B 구간 탐색"
    target_distance_m: float = 800.0
    profile: RunnerProfile = Field(default_factory=RunnerProfile)
    weather: WeatherContext = Field(default_factory=WeatherContext)
    nodes: Optional[List[RoadNode]] = None
    edges: Optional[List[RoadEdge]] = None


# ==============================================================================
# [실시간 기상 관측 조회 헬퍼 (Open-Meteo + 기상청 API허브 보조)]
# ==============================================================================
def _fetch_live_campus_weather(
    lat: float = 36.4675,
    lon: float = 127.1405,
) -> Tuple[float, float, str]:
    """공주 캠퍼스 좌표 기준 실시간 대기 기온(°C) 및 실제 일사량(W/m²)을 반환합니다."""
    live_temp = DEFAULT_TEMP_C
    live_solar = 0.0
    source_label = "기본 표준 기상값"

    # 1. Open-Meteo 실시간 초단기 관측·예보 API (실제 관측값 그대로 사용)
    try:
        om_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&current=temperature_2m,shortwave_radiation&timezone=Asia%2FSeoul"
        )
        req = urllib.request.Request(om_url, headers={"User-Agent": "PAPSrun-Engine/1.5"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                current = payload.get("current", {})
                if current.get("temperature_2m") is not None:
                    live_temp = round(float(current["temperature_2m"]), 1)

                raw_solar = current.get("shortwave_radiation")
                live_solar = round(float(raw_solar), 1) if raw_solar is not None else 0.0
                source_label = (
                    "실시간 기상 API (야간·새벽)"
                    if live_solar <= 10.0
                    else "실시간 기상 API (주간)"
                )
                return live_temp, live_solar, source_label
    except Exception:
        pass

    # 2. 기상청 API허브(ASOS 세종 239번 지점) 보조 조회
    if KMA_API_KEY:
        try:
            now_dt = datetime.now() - timedelta(minutes=20)
            tm_str = now_dt.strftime("%Y%m%d%H00")
            params = urllib.parse.urlencode(
                {
                    "tm": tm_str,
                    "stn": "239",
                    "help": "0",
                    "authKey": KMA_API_KEY.strip(),
                }
            )
            kma_url = f"https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php?{params}"
            req = urllib.request.Request(kma_url, headers={"User-Agent": "PAPSrun-Engine/1.5"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    text = resp.read().decode("utf-8", errors="ignore").strip()
                    for line in text.split("\n"):
                        line = line.strip()
                        if not line or line.startswith("#") or "7777END" in line:
                            continue
                        parts = line.split()
                        if len(parts) >= 12 and parts[11] not in ("-99.0", "-99", "-99.9"):
                            live_temp = round(float(parts[11]), 1)
                            current_hour = datetime.now().hour
                            live_solar = 0.0 if (current_hour < 6 or current_hour >= 19) else 650.0
                            source_label = "기상청 ASOS 실시간 연동"
                            return live_temp, live_solar, source_label
        except Exception:
            pass

    return live_temp, live_solar, source_label


# ==============================================================================
# [유틸리티: 하버사인 거리 및 샘플/사용자/Supabase 그래프 빌더]
# ==============================================================================
def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return round(2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a)), 1)


def _build_default_campus_graph() -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    sample_nodes = [
        ("N01_본관앞", 36.4675, 127.1400, 32.0),
        ("N02_운동장트랙", 36.4682, 127.1408, 31.0),
        ("N03_과학관입구", 36.4688, 127.1415, 44.5),
        ("N04_숲속산책로", 36.4679, 127.1422, 34.0),
        ("N05_도서관광장", 36.4686, 127.1428, 36.0),
        ("N06_체육관후문", 36.4694, 127.1420, 41.0),
    ]
    for nid, lat, lon, elev in sample_nodes:
        G.add_node(nid, lat=lat, lon=lon, elevation=elev, label=nid)

    sample_edges = [
        ("N01_본관앞", "N02_운동장트랙", "urethane", True),
        ("N02_운동장트랙", "N03_과학관입구", "concrete", False),
        ("N01_본관앞", "N04_숲속산책로", "dirt", True),
        ("N04_숲속산책로", "N05_도서관광장", "grass", True),
        ("N03_과학관입구", "N05_도서관광장", "asphalt", False),
        ("N03_과학관입구", "N06_체육관후문", "asphalt_old", False),
        ("N05_도서관광장", "N06_체육관후문", "urethane", True),
    ]
    for u, v, surf, shaded in sample_edges:
        dist = _haversine_m(
            G.nodes[u]["lat"], G.nodes[u]["lon"], G.nodes[v]["lat"], G.nodes[v]["lon"]
        )
        eu, ev = G.nodes[u]["elevation"], G.nodes[v]["elevation"]
        G.add_edge(u, v, length=dist, surface=surf, is_shaded=shaded, elev_u=eu, elev_v=ev)
        G.add_edge(v, u, length=dist, surface=surf, is_shaded=shaded, elev_u=ev, elev_v=eu)

    return G


def _build_graph_from_dicts(
    raw_nodes: List[Dict[str, Any]],
    raw_edges: List[Dict[str, Any]],
) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    for n in raw_nodes:
        nid = str(n["id"])
        G.add_node(
            nid,
            lat=float(n["lat"]),
            lon=float(n["lon"]),
            elevation=float(n.get("elevation", 30.0)),
            label=str(n.get("label") or nid),
        )

    for e in raw_edges:
        u, v = str(e["u"]), str(e["v"])
        if u not in G.nodes or v not in G.nodes:
            continue
        raw_len = e.get("length")
        dist = float(raw_len) if raw_len and float(raw_len) > 0 else _haversine_m(
            G.nodes[u]["lat"], G.nodes[u]["lon"], G.nodes[v]["lat"], G.nodes[v]["lon"]
        )
        eu = G.nodes[u]["elevation"]
        ev = G.nodes[v]["elevation"]
        surf = str(e.get("surface", "asphalt"))
        shaded = bool(e.get("is_shaded", False))
        G.add_edge(u, v, length=dist, surface=surf, is_shaded=shaded, elev_u=eu, elev_v=ev)
        G.add_edge(v, u, length=dist, surface=surf, is_shaded=shaded, elev_u=ev, elev_v=eu)

    return G


def _resolve_graph(
    class_code: Optional[str],
    nodes: Optional[List[RoadNode]],
    edges: Optional[List[RoadEdge]],
) -> Tuple[nx.MultiDiGraph, str]:
    if nodes and edges:
        return (
            _build_graph_from_dicts(
                [n.model_dump() for n in nodes],
                [e.model_dump() for e in edges],
            ),
            "요청 페이로드 직접 전달 도로망",
        )

    target_code = (class_code or DEFAULT_CLASS_CODE).strip()
    if target_code:
        sb_nodes, sb_edges, sb_msg = fetch_class_network_from_supabase(target_code)
        if sb_nodes and sb_edges:
            return _build_graph_from_dicts(sb_nodes, sb_edges), sb_msg
        return _build_default_campus_graph(), sb_msg

    return _build_default_campus_graph(), "기본 샘플 캠퍼스 도로망"


# ==============================================================================
# [API 엔드포인트]
# ==============================================================================
@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "service": "PAPSrun AI Campus Routing Engine",
        "status": "running",
        "default_class_code": DEFAULT_CLASS_CODE,
        "privacy_standard": "SHA-256 Identity Anonymization + GPS Gaussian Differential Privacy",
        "endpoints": {
            "docs": "/docs",
            "health": "/api/health",
            "config": "/api/config",
            "sample_network": "/api/sample-network",
            "class_network": "/api/network/{class_code}",
            "optimize_route": "/api/optimize-route",
        },
    }


@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "PAPSrun AI Integrated Backend",
        "modules_loaded": 20,
        "default_class_code": DEFAULT_CLASS_CODE,
    }


@app.get("/api/config")
def get_frontend_config(lat: float = 36.4675, lon: float = 127.1405) -> Dict[str, Any]:
    """실시간 기상 API(기온·일사량) 및 프론트엔드 지도/학급 기본 설정을 전달합니다."""
    live_temp, live_solar, weather_source = _fetch_live_campus_weather(lat=lat, lon=lon)
    return {
        "default_class_code": DEFAULT_CLASS_CODE,
        "default_temp_c": live_temp,
        "default_solar_wm2": live_solar,
        "weather_source": weather_source,
        "vworld_api_key": VWORLD_API_KEY,
    }


@app.get("/api/sample-network")
def get_sample_network() -> Dict[str, Any]:
    G = _build_default_campus_graph()
    return {
        "source": "기본 샘플 캠퍼스 도로망",
        "nodes": [{"id": nid, **attrs} for nid, attrs in G.nodes(data=True)],
        "edges": [{"u": u, "v": v, **attrs} for u, v, attrs in G.edges(data=True)],
    }


@app.get("/api/network/{class_code}")
def get_class_network(class_code: str) -> Dict[str, Any]:
    G, source_msg = _resolve_graph(class_code=class_code, nodes=None, edges=None)
    return {
        "class_code": class_code,
        "source": source_msg,
        "nodes": [{"id": nid, **attrs} for nid, attrs in G.nodes(data=True)],
        "edges": [{"u": u, "v": v, **attrs} for u, v, attrs in G.edges(data=True)],
    }


@app.post("/api/optimize-route")
def optimize_route(req: RouteRequest) -> Dict[str, Any]:
    try:
        G, source_msg = _resolve_graph(req.class_code, req.nodes, req.edges)
        end_node = req.end_node or req.start_node

        # 평문 runner_id는 파이프라인 내부에서 즉시 SHA-256 단방향 해시 처리 후 폐기
        raw_runner_id = req.runner_id
        req.runner_id = None

        result = execute_papsrun_pipeline(
            G=G,
            start_node=req.start_node,
            end_node=end_node,
            profile=req.profile.model_dump(exclude_none=True),
            weather=req.weather.model_dump(),
            route_mode=req.route_mode,
            target_distance_m=req.target_distance_m,
            runner_id=raw_runner_id,
        )
        del raw_runner_id

        result["data_source"] = source_msg
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except nx.NetworkXNoPath:
        raise HTTPException(
            status_code=404,
            detail="선택한 출발지와 도착지 사이에 연결된 캠퍼스 경로가 존재하지 않습니다.",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"경로 최적화 연산 중 오류 발생: {exc}")