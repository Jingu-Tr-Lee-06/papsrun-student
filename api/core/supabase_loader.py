import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from supabase import create_client
except ImportError as _imp_err:
    create_client = None
    _SUPABASE_IMPORT_ERR = str(_imp_err)
else:
    _SUPABASE_IMPORT_ERR = ""


def _ensure_env_loaded() -> None:
    """python-dotenv 유무와 무관하게 프로젝트 루트의 .env 파일을 직접 읽어 환경변수에 등록합니다."""
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"):
        return

    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


def get_supabase_client() -> Tuple[Optional[Any], str]:
    _ensure_env_loaded()

    if create_client is None:
        return None, f"supabase 패키지 미설치 ({_SUPABASE_IMPORT_ERR})"

    url = (os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    key = (
        os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
        or ""
    ).strip()

    if not url or not key:
        return None, ".env 파일에 SUPABASE_URL 또는 SUPABASE_KEY가 비어 있습니다."

    try:
        return create_client(url, key), "ok"
    except Exception as exc:
        return None, f"create_client 오류: {exc}"


def fetch_class_network_from_supabase(
    class_code: Optional[str] = None,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]], str]:
    """Supabase에서 학급 코드(class_code)에 해당하는 실제 도로망 노드·간선 데이터를 조회합니다."""
    _ensure_env_loaded()
    code = (class_code or os.environ.get("DEFAULT_CLASS_CODE", "")).strip()
    if not code:
        return None, None, "학급 코드 미입력 (기본 샘플 도로망 사용)"

    client, err_msg = get_supabase_client()
    if client is None:
        return None, None, f"Supabase 연결 실패: {err_msg} (기본 샘플 도로망 사용)"

    try:
        # 1. 학급 노드(핀) 조회 (실제 테이블 컬럼: id, lat, lng, elevation, team_id)
        nodes_res = (
            client.table("campus_nodes")
            .select("*")
            .eq("class_code", code)
            .execute()
        )
        raw_nodes = nodes_res.data or []

        # 2. 학급 간선(도로) 조회 (실제 테이블 컬럼: source_id, target_id, distance, surface_type, is_shaded)
        edges_res = (
            client.table("campus_edges")
            .select("*")
            .eq("class_code", code)
            .execute()
        )
        raw_edges = edges_res.data or []

        if not raw_edges:
            return None, None, f"Supabase 연결 성공했으나 학급 '{code}'의 간선 데이터가 0건입니다 (기본 샘플 도로망 사용)"

        node_map: Dict[str, Dict[str, Any]] = {}
        for n in raw_nodes:
            nid = str(n.get("id") or n.get("node_id"))
            lon_val = n.get("lng") if n.get("lng") is not None else n.get("lon", 127.1400)
            node_map[nid] = {
                "id": nid,
                "lat": float(n.get("lat", 36.4675)),
                "lon": float(lon_val),
                "elevation": float(n.get("elevation", 30.0)),
                "label": str(n.get("name") or nid),
            }

        norm_edges: List[Dict[str, Any]] = []
        for e in raw_edges:
            u = str(e.get("source_id") or e.get("from_node") or e.get("u"))
            v = str(e.get("target_id") or e.get("to_node") or e.get("v"))

            # campus_nodes에 없는 노드가 있으면 간선 메타 좌표로 자동 보완
            if u not in node_map and e.get("source_lat") is not None:
                node_map[u] = {
                    "id": u,
                    "lat": float(e["source_lat"]),
                    "lon": float(e.get("source_lng") or e.get("source_lon", 127.1400)),
                    "elevation": float(e.get("source_elevation", 30.0)),
                    "label": u,
                }
            if v not in node_map and e.get("target_lat") is not None:
                node_map[v] = {
                    "id": v,
                    "lat": float(e["target_lat"]),
                    "lon": float(e.get("target_lng") or e.get("target_lon", 127.1400)),
                    "elevation": float(e.get("target_elevation", 30.0)),
                    "label": v,
                }

            raw_len = e.get("distance") or e.get("length_m") or e.get("length")
            norm_edges.append(
                {
                    "u": u,
                    "v": v,
                    "length": float(raw_len) if raw_len else None,
                    "surface": str(e.get("surface_type") or e.get("surface") or "asphalt_new"),
                    "is_shaded": bool(e.get("is_shaded", False)),
                }
            )

        norm_nodes = list(node_map.values())
        return (
            norm_nodes,
            norm_edges,
            f"Supabase 실시간 학급 도로망 연동 성공 [{code}] (노드 {len(norm_nodes)}개, 간선 {len(norm_edges)}개)",
        )

    except Exception as exc:
        return None, None, f"Supabase 쿼리 오류: {exc} (기본 샘플 도로망 사용)"