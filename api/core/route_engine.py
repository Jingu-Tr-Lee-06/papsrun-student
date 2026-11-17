from typing import Any, Callable, Dict, List, Optional, Tuple
import networkx as nx

# Team 1: 지형·미기상 & 기상 안전 제어 (학생 01~05)
from api.team1.student_01_albedo import get_surface_albedo
from api.team1.student_02_solar import calculate_effective_solar
from api.team1.student_03_radiation import calculate_absorbed_radiation
from api.team1.student_04_slope_dem import compute_edge_slope
from api.team1.student_05_failsafe import check_weather_failsafe

# Team 2: 체육 PAPS 생체역학 & ML 심박 추론 (학생 06~10)
from api.team2.student_06_step_test import calculate_base_delta_hr
from api.team2.student_07_paps_weight import get_paps_strain_factor
from api.team2.student_08_ml_regression import predict_ml_bpm_gain
from api.team2.student_09_clamping import clamp_physiological_bpm
from api.team2.student_10_target_hr import evaluate_target_hr_zone

# Team 3: 수학 위상 & 다익스트라 코어 (학생 11~15)
from api.team3.student_11_topology import check_network_connectivity
from api.team3.student_12_normalizer import min_max_normalize
from api.team3.student_13_joint_guard import get_surface_joint_penalty
from api.team3.student_14_steep_cost import calculate_steep_slope_multiplier
from api.team3.student_15_dijkstra_core import (
    compute_final_dijkstra_edge_cost,
    run_custom_dijkstra,
)

# Team 4: 입력 검증, 차분 프라이버시, 지도 시각화, 비교 대시보드 & XAI (학생 16~20)
from api.team4.student_16_sidebar_ui import validate_route_selection
from api.team4.student_17_privacy import (
    build_privacy_audit_report,
    mask_coordinates_privacy,
)
from api.team4.student_18_map_render import build_map_render_payload
from api.team4.student_19_metrics_table import build_metrics_comparison_table
from api.team4.student_20_xai_explainer import generate_xai_explanation


def build_comfort_weight_fn(
    G: nx.MultiDiGraph,
    profile: Dict[str, Any],
    solar_wm2: float,
) -> Callable[[str, str, Dict[str, Any]], float]:
    """2번 경로: 일반 쾌적 경로 (주간: 수목 그늘·완충 노면 / 야간·새벽: 완충 노면·평탄도 우선)"""
    weight_kg = float(profile.get("weight", 62.0))
    rest_hr = float(profile.get("rest_hr", 70.0))
    peak_hr = float(profile.get("peak_hr", 145.0))
    base_delta = calculate_base_delta_hr(peak_hr, rest_hr)
    is_night_or_dawn = solar_wm2 <= 10.0

    def comfort_edge_weight(u: str, v: str, edge_attr: Dict[str, Any]) -> float:
        length = max(1.0, float(edge_attr.get("length", 50.0)))
        surface = str(edge_attr.get("surface", "asphalt"))
        is_shaded = bool(edge_attr.get("is_shaded", False))
        elev_u = float(edge_attr.get("elev_u", G.nodes[u].get("elevation", 30.0)))
        elev_v = float(edge_attr.get("elev_v", G.nodes[v].get("elevation", 30.0)))

        albedo = get_surface_albedo(surface)
        eff_solar = calculate_effective_solar(solar_wm2, is_shaded=is_shaded)
        r_abs = calculate_absorbed_radiation(eff_solar, albedo)
        slope_pct = compute_edge_slope(elev_u, elev_v, length)

        ml_gain = predict_ml_bpm_gain(slope_pct, r_abs, weight_kg, 1.0)
        est_bpm = clamp_physiological_bpm(
            rest_hr + (base_delta * 0.35) + ml_gain,
            rest_hr=rest_hr,
        )

        edge_attr["r_abs"] = r_abs
        edge_attr["bpm"] = est_bpm
        edge_attr["slope_pct"] = slope_pct

        surf_factor = 0.55 if surface in ("urethane", "grass", "dirt", "soil") else 1.45
        slope_factor = 1.0 + (max(0.0, slope_pct) * 0.15)

        if is_night_or_dawn:
            env_factor = 1.15 if is_shaded else 0.90
            cost = length * surf_factor * slope_factor * env_factor
        else:
            shade_factor = 0.45 if is_shaded else 2.2
            rad_factor = 1.0 + (r_abs / 850.0)
            cost = length * shade_factor * surf_factor * slope_factor * rad_factor

        return compute_final_dijkstra_edge_cost(cost)

    return comfort_edge_weight


def build_ensemble_weight_fn(
    G: nx.MultiDiGraph,
    profile: Dict[str, Any],
    solar_wm2: float,
    current_temp: float = 24.5,
) -> Callable[[str, str, Dict[str, Any]], float]:
    """3번 경로: PAPS 등급(1~5등급)별 명확한 코스 차이를 만드는 개인 맞춤 비용 함수"""
    paps_grade = int(profile.get("paps_grade", 3))
    weight_kg = float(profile.get("weight", 62.0))
    rest_hr = float(profile.get("rest_hr", 70.0))
    peak_hr = float(profile.get("peak_hr", 145.0))
    is_night_or_dawn = solar_wm2 <= 10.0

    base_delta = calculate_base_delta_hr(peak_hr, rest_hr)
    paps_factor = get_paps_strain_factor(paps_grade, weight_kg)

    # 등급별 극명한 가중치 프로파일 (1등급: 속도·효율 중심 ↔ 5등급: 경사·노면충격 극도 회피)
    grade_slope_sensitivity = {1: 0.05, 2: 0.18, 3: 0.45, 4: 0.95, 5: 1.65}.get(paps_grade, 0.45)
    grade_surface_sensitivity = {1: 0.25, 2: 0.60, 3: 1.10, 4: 1.75, 5: 2.50}.get(paps_grade, 1.10)
    hr_recovery_ratio = 1.0 + max(0.0, base_delta - 50.0) / 85.0

    cold_stiffness = max(1.0, 1.0 + max(0.0, 16.0 - current_temp) * 0.04)
    thermal_stress_index = max(0.65, (current_temp / 24.0) * (0.75 + solar_wm2 / 1500.0))

    def ensemble_edge_weight(u: str, v: str, edge_attr: Dict[str, Any]) -> float:
        length = max(1.0, float(edge_attr.get("length", 50.0)))
        surface = str(edge_attr.get("surface", "asphalt"))
        is_shaded = bool(edge_attr.get("is_shaded", False))
        elev_u = float(edge_attr.get("elev_u", G.nodes[u].get("elevation", 30.0)))
        elev_v = float(edge_attr.get("elev_v", G.nodes[v].get("elevation", 30.0)))

        albedo = get_surface_albedo(surface)
        eff_solar = calculate_effective_solar(solar_wm2, is_shaded=is_shaded)
        r_abs = calculate_absorbed_radiation(eff_solar, albedo)
        slope_pct = compute_edge_slope(elev_u, elev_v, length)

        ml_gain = predict_ml_bpm_gain(slope_pct, r_abs, weight_kg, paps_factor)
        est_bpm = clamp_physiological_bpm(
            rest_hr + (base_delta * 0.35) + ml_gain,
            rest_hr=rest_hr,
        )

        edge_attr["r_abs"] = r_abs
        edge_attr["bpm"] = est_bpm
        edge_attr["slope_pct"] = slope_pct
        edge_attr["paps_grade"] = paps_grade

        _ = min_max_normalize(length, 5.0, 350.0)
        joint_mult = get_surface_joint_penalty(surface, paps_grade=paps_grade)
        steep_pen = calculate_steep_slope_multiplier(slope_pct, paps_grade=paps_grade)

        # 오르막 및 고강도 구간: 1등급은 거의 페널티 없이 돌파, 4~5등급은 강력 우회
        uphill_factor = (1.0 + max(0.0, slope_pct) * grade_slope_sensitivity * hr_recovery_ratio)
        if paps_grade <= 2:
            # 우수 체력군(1~2등급)은 완충 노면 우회 보너스를 줄이고 거리 효율을 우선시
            joint_term = 1.0 + (joint_mult - 1.0) * grade_surface_sensitivity
            steep_term = 1.0 + (steep_pen - 1.0) * 0.25
        else:
            joint_term = (joint_mult * cold_stiffness) ** grade_surface_sensitivity
            steep_term = steep_pen ** (0.8 + (paps_grade - 2) * 0.45)

        if is_night_or_dawn:
            weather_term = 1.05 if is_shaded else 0.95
        elif is_shaded:
            weather_term = max(0.40, 0.85 / thermal_stress_index)
        else:
            weather_term = (1.0 + (r_abs / 850.0)) * thermal_stress_index

        raw_cost = length * uphill_factor * steep_term * joint_term * weather_term
        return compute_final_dijkstra_edge_cost(raw_cost)

    return ensemble_edge_weight


def find_target_distance_loop(
    G: nx.MultiDiGraph,
    start_node: str,
    target_distance_m: float,
    weight_func: Callable[[str, str, Dict[str, Any]], float],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    best_opt = None
    best_eval = None
    best_score = float("inf")
    half_target = max(100.0, float(target_distance_m) / 2.0)

    for candidate in G.nodes():
        if candidate == start_node:
            continue
        try:
            out_opt, out_eval = run_custom_dijkstra(G, start_node, candidate, weight_func)
            back_opt, back_eval = run_custom_dijkstra(G, candidate, start_node, weight_func)

            opt_dist = out_eval["optimal"]["distance"] + back_eval["optimal"]["distance"]
            dist_gap = abs(opt_dist - float(target_distance_m))
            out_gap = abs(out_eval["optimal"]["distance"] - half_target)
            score = dist_gap + (out_gap * 0.4)

            if score < best_score:
                best_score = score
                merged_opt_nodes = out_opt["nodes"] + back_opt["nodes"][1:]
                merged_opt_coords = out_opt["coords"] + back_opt["coords"][1:]
                merged_opt_segs = out_opt["segments"] + back_opt["segments"]

                merged_sht_nodes = out_eval["shortest"]["nodes"] + back_eval["shortest"]["nodes"][1:]
                merged_sht_coords = out_eval["shortest"]["coords"] + back_eval["shortest"]["coords"][1:]
                merged_sht_segs = out_eval["shortest"]["segments"] + back_eval["shortest"]["segments"]

                opt_rads = [s["r_abs"] for s in merged_opt_segs] or [0.0]
                sht_rads = [s["r_abs"] for s in merged_sht_segs] or [0.0]

                best_opt = {
                    "nodes": merged_opt_nodes,
                    "coords": merged_opt_coords,
                    "segments": merged_opt_segs,
                }
                best_eval = {
                    "shortest": {
                        "nodes": merged_sht_nodes,
                        "coords": merged_sht_coords,
                        "segments": merged_sht_segs,
                        "distance": round(
                            out_eval["shortest"]["distance"] + back_eval["shortest"]["distance"], 1
                        ),
                        "elev_gain": round(
                            out_eval["shortest"]["elev_gain"] + back_eval["shortest"]["elev_gain"], 1
                        ),
                        "max_bpm": max(
                            out_eval["shortest"]["max_bpm"], back_eval["shortest"]["max_bpm"]
                        ),
                        "avg_rad": round(sum(sht_rads) / len(sht_rads), 1),
                    },
                    "optimal": {
                        "nodes": merged_opt_nodes,
                        "coords": merged_opt_coords,
                        "segments": merged_opt_segs,
                        "distance": round(opt_dist, 1),
                        "elev_gain": round(
                            out_eval["optimal"]["elev_gain"] + back_eval["optimal"]["elev_gain"], 1
                        ),
                        "max_bpm": max(
                            out_eval["optimal"]["max_bpm"], back_eval["optimal"]["max_bpm"]
                        ),
                        "avg_rad": round(sum(opt_rads) / len(opt_rads), 1),
                    },
                }
        except Exception:
            continue

    if best_opt is None or best_eval is None:
        raise ValueError("해당 출발지에서 왕복 순환 가능한 연결 경로를 찾지 못했습니다.")

    return best_opt, best_eval


def execute_papsrun_pipeline(
    G: nx.MultiDiGraph,
    start_node: str,
    end_node: str,
    profile: Dict[str, Any],
    weather: Dict[str, Any],
    route_mode: str = "A→B 구간 탐색",
    target_distance_m: float = 800.0,
    runner_id: Optional[str] = None,
) -> Dict[str, Any]:
    current_temp = float(profile.get("current_temp", weather.get("temp_c", 24.5)))
    solar_wm2 = float(profile.get("solar_w", weather.get("solar_wm2", 750.0)))

    # 1. 기상 안전 Fail-Safe 인터락 (학생 05)
    is_weather_safe, weather_msg = check_weather_failsafe(current_temp)
    if not is_weather_safe:
        return {
            "ok": False,
            "blocked_by_weather": True,
            "message": weather_msg,
        }

    # 2. 그래프 위상 무결성 검증 (학생 11)
    topo_info = check_network_connectivity(G, start_node)
    available_nodes = sorted(list(G.nodes()))

    # 3. 입력 검증 (학생 16)
    if route_mode == "A→B 구간 탐색":
        is_valid, val_msg, clean_profile = validate_route_selection(
            start_node=start_node,
            end_node=end_node,
            available_nodes=available_nodes,
            profile=profile,
        )
        if not is_valid:
            return {"ok": False, "blocked_by_weather": False, "message": val_msg}
    else:
        if start_node not in available_nodes:
            return {
                "ok": False,
                "blocked_by_weather": False,
                "message": "출발 교차점이 도로망에 존재하지 않습니다.",
            }
        _, _, clean_profile = validate_route_selection(
            start_node=available_nodes[0],
            end_node=available_nodes[-1],
            available_nodes=available_nodes,
            profile=profile,
        )

    # 4. 3방향 경로 산출: ① 최단 경로 vs ② 일반 쾌적 경로 vs ③ PAPSrun 개인 맞춤 경로
    comfort_fn = build_comfort_weight_fn(G, clean_profile, solar_wm2=solar_wm2)
    ensemble_fn = build_ensemble_weight_fn(
        G, clean_profile, solar_wm2=solar_wm2, current_temp=current_temp
    )

    if route_mode == "A→B 구간 탐색":
        _, comfort_eval = run_custom_dijkstra(G, start_node, end_node, comfort_fn)
        opt_path, eval_data = run_custom_dijkstra(G, start_node, end_node, ensemble_fn)
    else:
        _, comfort_eval = find_target_distance_loop(
            G, start_node, target_distance_m=target_distance_m, weight_func=comfort_fn
        )
        opt_path, eval_data = find_target_distance_loop(
            G, start_node, target_distance_m=target_distance_m, weight_func=ensemble_fn
        )

    eval_data["comfort"] = comfort_eval["optimal"]

    # 5. 카보넨 목표 심박수 판정 (학생 10)
    thr_info = evaluate_target_hr_zone(
        predicted_bpm=eval_data["optimal"]["max_bpm"],
        rest_hr=clean_profile["rest_hr"],
        age=clean_profile["age"],
        paps_grade=clean_profile["paps_grade"],
    )

    # 6. 식별자 SHA-256 익명화 + GPS 차분 프라이버시 좌표 마스킹 (학생 17)
    noise_m = 18.0
    privacy_report = build_privacy_audit_report(runner_id, noise_meters=noise_m)
    masked_opt_coords = mask_coordinates_privacy(opt_path["coords"], noise_meters=noise_m)
    masked_start = (masked_opt_coords[0][0], masked_opt_coords[0][1]) if masked_opt_coords else None
    masked_end = (masked_opt_coords[-1][0], masked_opt_coords[-1][1]) if masked_opt_coords else None

    # 7. 3종 경로 지도 직렬화, 3방향 비교 테이블 및 XAI 근거 생성 (학생 18, 19, 20)
    map_payload = build_map_render_payload(
        G=G,
        opt_coords=opt_path["coords"],
        short_coords=eval_data["shortest"]["coords"],
        comfort_coords=eval_data["comfort"]["coords"],
        masked_start=masked_start,
        masked_end=masked_end,
    )
    metrics_table = build_metrics_comparison_table(
        shortest_stats=eval_data["shortest"],
        optimal_stats=eval_data["optimal"],
        comfort_stats=eval_data["comfort"],
    )
    xai_explanation = generate_xai_explanation(
        eval_data=eval_data,
        profile=clean_profile,
        thr_info=thr_info,
    )

    return {
        "ok": True,
        "blocked_by_weather": False,
        "weather_message": weather_msg,
        "privacy_report": privacy_report,
        "topology": {
            "is_connected": topo_info["is_connected"],
            "reachable_count": topo_info["reachable_count"],
            "total_nodes": topo_info["total_nodes"],
        },
        "route_mode": route_mode,
        "optimal_path": opt_path,
        "eval_data": eval_data,
        "thr_info": thr_info,
        "map_payload": map_payload,
        "metrics_table": metrics_table,
        "xai": xai_explanation,
    }