"use client";

import React, { useEffect, useState } from "react";
import {
  MapContainer,
  TileLayer,
  Polyline,
  CircleMarker,
  Marker,
  Popup,
  Tooltip,
  LayersControl,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

export type PathViewMode = "all" | "optimal" | "comfort" | "shortest";

export interface NetworkNode {
  id: string;
  lat: number;
  lon: number;
  elevation: number;
  label?: string;
}

export interface NetworkEdge {
  u: string;
  v: string;
  length: number;
  surface: string;
  is_shaded: boolean;
}

export interface MapRenderPayload {
  center: [number, number];
  zoom: number;
  network_edges: Array<{
    u: string;
    v: string;
    coords: [[number, number], [number, number]];
    surface: string;
    slope_pct: number;
    is_shaded: boolean;
  }>;
  shortest_path: {
    coords: [number, number][];
    color: string;
    weight: number;
    dash_array?: string;
    label: string;
  } | null;
  comfort_path?: {
    coords: [number, number][];
    color: string;
    weight: number;
    label: string;
  } | null;
  optimal_path: {
    coords: [number, number][];
    color: string;
    weight: number;
    label: string;
  } | null;
  markers?: {
    start?: { coords: [number, number]; label: string; color: string };
    end?: { coords: [number, number]; label: string; color: string };
  };
}

interface RouteMapProps {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
  mapPayload: MapRenderPayload | null;
  activePathView: PathViewMode;
  selectedStart: string;
  selectedEnd: string;
  vworldApiKey?: string;
  onSelectStart: (nodeId: string) => void;
  onSelectEnd: (nodeId: string) => void;
}

function MapViewportUpdater({ center, zoom }: { center: [number, number]; zoom: number }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, zoom, { animate: true });
  }, [center, zoom, map]);
  return null;
}

function shiftCoords(
  coords: [number, number][],
  latOffset: number,
  lonOffset: number
): [number, number][] {
  return coords.map(([lat, lon]) => [lat + latOffset, lon + lonOffset]);
}

const createPinIcon = (bgColor: string, text: string) =>
  L.divIcon({
    className: "custom-papsrun-pin",
    html: `<div style="display:inline-flex;align-items:center;justify-content:center;width:max-content;background:${bgColor};color:#ffffff;font-weight:800;font-size:11px;padding:4px 10px;border-radius:9999px;border:2px solid #0f172a;box-shadow:0 3px 8px rgba(0,0,0,0.55);white-space:nowrap;">${text}</div>`,
    iconSize: [120, 28],
    iconAnchor: [60, 14],
  });

export default function RouteMap({
  nodes,
  edges,
  mapPayload,
  activePathView,
  selectedStart,
  selectedEnd,
  vworldApiKey,
  onSelectStart,
  onSelectEnd,
}: RouteMapProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return (
      <div className="flex h-[480px] w-full items-center justify-center rounded-xl border border-slate-800 bg-slate-900 text-sm text-slate-400">
        지도 엔진 초기화 중...
      </div>
    );
  }

  const nodeMap = new Map<string, NetworkNode>();
  nodes.forEach((n) => nodeMap.set(n.id, n));

  const defaultCenter: [number, number] =
    nodes.length > 0
      ? [
          nodes.reduce((acc, cur) => acc + cur.lat, 0) / nodes.length,
          nodes.reduce((acc, cur) => acc + cur.lon, 0) / nodes.length,
        ]
      : [36.4675, 127.1405];

  const center = mapPayload?.center ?? defaultCenter;
  const zoom = mapPayload?.zoom ?? 17;

  const showOptimal = activePathView === "all" || activePathView === "optimal";
  const showComfort = activePathView === "all" || activePathView === "comfort";
  const showShortest = activePathView === "all" || activePathView === "shortest";
  const isAllMode = activePathView === "all";

  return (
    <div className="relative h-[480px] w-full overflow-hidden rounded-xl border border-slate-800 shadow-lg">
      <MapContainer
        center={center}
        zoom={zoom}
        scrollWheelZoom={true}
        className="z-0 w-full h-full"
      >
        <MapViewportUpdater center={center} zoom={zoom} />

        <LayersControl position="topright">
          <LayersControl.BaseLayer checked name="일반 지도 (OpenStreetMap)">
            <TileLayer
              attribution='&copy; OpenStreetMap contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
          </LayersControl.BaseLayer>

          {vworldApiKey && (
            <LayersControl.BaseLayer name="국토부 VWorld 위성영상">
              <TileLayer
                attribution="&copy; 국토교통부 VWorld"
                url={`https://api.vworld.kr/req/wmts/1.0.0/${vworldApiKey}/Satellite/{z}/{y}/{x}.jpeg`}
              />
            </LayersControl.BaseLayer>
          )}
        </LayersControl>

        {/* 1. 학급 캠퍼스 기본 도로망 간선 */}
        {edges.map((edge, idx) => {
          const uNode = nodeMap.get(edge.u);
          const vNode = nodeMap.get(edge.v);
          if (!uNode || !vNode) return null;
          return (
            <Polyline
              key={`base-edge-${idx}`}
              positions={[
                [uNode.lat, uNode.lon],
                [vNode.lat, vNode.lon],
              ]}
              pathOptions={{
                color: "#64748b",
                weight: 3,
                opacity: 0.4,
              }}
            >
              <Tooltip sticky>
                {edge.u} → {edge.v} ({edge.surface} · {edge.length}m ·{" "}
                {edge.is_shaded ? "그늘" : "양지"})
              </Tooltip>
            </Polyline>
          );
        })}

        {/* 2. PAPSrun AI 개인 맞춤 추천 경로 */}
        {showOptimal && mapPayload?.optimal_path && mapPayload.optimal_path.coords.length >= 2 && (
          <>
            <Polyline
              positions={
                isAllMode
                  ? shiftCoords(mapPayload.optimal_path.coords, -0.00005, -0.00005)
                  : mapPayload.optimal_path.coords
              }
              pathOptions={{
                color: "#052e16",
                weight: 9,
                opacity: 0.85,
              }}
            />
            <Polyline
              positions={
                isAllMode
                  ? shiftCoords(mapPayload.optimal_path.coords, -0.00005, -0.00005)
                  : mapPayload.optimal_path.coords
              }
              pathOptions={{
                color: "#16a34a",
                weight: 6,
                opacity: 0.98,
              }}
            >
              <Tooltip sticky>{mapPayload.optimal_path.label}</Tooltip>
            </Polyline>
          </>
        )}

        {/* 3. 일반 쾌적 경로 */}
        {showComfort && mapPayload?.comfort_path && mapPayload.comfort_path.coords.length >= 2 && (
          <>
            <Polyline
              positions={mapPayload.comfort_path.coords}
              pathOptions={{
                color: "#0c4a6e",
                weight: 8,
                opacity: 0.8,
              }}
            />
            <Polyline
              positions={mapPayload.comfort_path.coords}
              pathOptions={{
                color: "#0ea5e9",
                weight: 5,
                opacity: 0.98,
              }}
            >
              <Tooltip sticky>{mapPayload.comfort_path.label}</Tooltip>
            </Polyline>
          </>
        )}

        {/* 4. 물리적 최단 거리 경로 */}
        {showShortest && mapPayload?.shortest_path && mapPayload.shortest_path.coords.length >= 2 && (
          <>
            <Polyline
              positions={
                isAllMode
                  ? shiftCoords(mapPayload.shortest_path.coords, 0.00005, 0.00005)
                  : mapPayload.shortest_path.coords
              }
              pathOptions={{
                color: "#ffffff",
                weight: 7,
                opacity: 0.9,
              }}
            />
            <Polyline
              positions={
                isAllMode
                  ? shiftCoords(mapPayload.shortest_path.coords, 0.00005, 0.00005)
                  : mapPayload.shortest_path.coords
              }
              pathOptions={{
                color: "#dc2626",
                weight: 4.5,
                opacity: 1,
                dashArray: "8, 8",
              }}
            >
              <Tooltip sticky>{mapPayload.shortest_path.label}</Tooltip>
            </Polyline>
          </>
        )}

        {/* 5. 교차점 노드 마커 */}
        {nodes.map((node) => {
          const isStart = node.id === selectedStart;
          const isEnd = node.id === selectedEnd;
          const fillColor = isStart ? "#16a34a" : isEnd ? "#dc2626" : "#3b82f6";

          return (
            <CircleMarker
              key={`node-${node.id}`}
              center={[node.lat, node.lon]}
              radius={isStart || isEnd ? 7 : 3.5}
              pathOptions={{
                color: "#ffffff",
                weight: isStart || isEnd ? 2 : 1,
                fillColor,
                fillOpacity: isStart || isEnd ? 1 : 0.75,
              }}
            >
              <Tooltip direction="top" offset={[0, -5]}>
                [{node.id}] 표고 {node.elevation}m
              </Tooltip>
              <Popup>
                <div className="min-w-[160px] space-y-2 text-xs text-slate-800">
                  <div className="font-bold text-slate-900">{node.label || node.id}</div>
                  <div className="text-slate-600">해발 표고: {node.elevation} m</div>
                  <div className="flex gap-1.5 pt-1">
                    <button
                      type="button"
                      onClick={() => onSelectStart(node.id)}
                      className="flex-1 px-2 py-1 font-semibold text-white rounded bg-emerald-600 hover:bg-emerald-700"
                    >
                      🚩 출발지
                    </button>
                    <button
                      type="button"
                      onClick={() => onSelectEnd(node.id)}
                      className="flex-1 px-2 py-1 font-semibold text-white rounded bg-rose-600 hover:bg-rose-700"
                    >
                      🎯 도착지
                    </button>
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          );
        })}

        {/* 6. 차분 프라이버시 보호 출발·도착 거점 오버레이 핀 */}
        {mapPayload?.markers?.start && (
          <Marker
            position={mapPayload.markers.start.coords}
            icon={createPinIcon("#15803d", "🚩 START (비식별)")}
          >
            <Tooltip>{mapPayload.markers.start.label}</Tooltip>
          </Marker>
        )}
        {mapPayload?.markers?.end && (
          <Marker
            position={mapPayload.markers.end.coords}
            icon={createPinIcon("#b91c1c", "🎯 GOAL (비식별)")}
          >
            <Tooltip>{mapPayload.markers.end.label}</Tooltip>
          </Marker>
        )}
      </MapContainer>
    </div>
  );
}