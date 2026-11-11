#!/usr/bin/env python3
"""
geometry_spec.yaml → snappyHexMesh용 패치별 STL 생성기 (촉매 반응기 템플릿)

출력 (out/):
  fluid.stl            패치별 solid 이름이 붙은 ASCII STL (snappyHexMesh 입력)
  patches/<name>.stl   패치별 개별 STL
  fluid.step           Windows FreeCAD 등에서 확인용 CAD
  geometry_out.json    격자 단계로 넘길 정보 (패치, bbox, 촉매 박스, locationInMesh 후보)

사용법:
  python3 scripts/make_geometry.py [geometry_spec.yaml] [--out out]
"""
import argparse
import json
import math
import pathlib
import sys

import yaml
import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

EPS = 1e-6


# ---------------------------------------------------------------------------
# spec 읽기
# ---------------------------------------------------------------------------
def v(node):
    """{value: x, status: ...} 형식과 단순 값 모두 허용"""
    return node["value"] if isinstance(node, dict) and "value" in node else node


def collect_flags(spec, prefix=""):
    """status가 assumed/estimated인 항목 목록 (보고서용)"""
    flags = []
    if isinstance(spec, dict):
        if "status" in spec and spec["status"] in ("assumed", "estimated"):
            flags.append({"item": prefix, "value": spec.get("value"),
                          "status": spec["status"], "note": spec.get("note", "")})
        for k, val in spec.items():
            if k in ("status", "value", "note"):
                continue
            flags += collect_flags(val, f"{prefix}.{k}" if prefix else k)
    elif isinstance(spec, list):
        for i, item in enumerate(spec):
            name = item.get("name", i) if isinstance(item, dict) else i
            flags += collect_flags(item, f"{prefix}[{name}]")
    return flags


def load_params(spec):
    p = {}
    h = spec["housing"]
    p["W"], p["D"], p["H"] = v(h["width_x"]), v(h["depth_y"]), v(h["height_z"])

    i = spec["inlet_duct"]
    p["Di"], p["Li"], p["Ei"] = v(i["diameter"]), v(i["length"]), v(i["extension"])

    d = spec["diffuser"]
    if d.get("type") != "loft_circle_to_square":
        sys.exit(f"[ERROR] 지원하지 않는 diffuser.type: {d.get('type')}")
    p["Hd"] = v(d["height"])

    o = spec["outlet_duct"]
    Do = v(o["diameter"])
    p["Do"] = p["Di"] if Do == "same_as_inlet" else float(Do)
    p["side"] = v(o["side"])
    # ±x/±y 측면 출구: center_z + 가로 위치(center_y 또는 center_x)
    # -z 바닥 출구: center_x, center_y
    p["zc"] = v(o.get("center_z", 0.0))
    p["xc"] = v(o.get("center_x", 0.0))
    p["yc"] = v(o.get("center_y", 0.0))
    p["Lo"], p["Eo"] = v(o["length"]), v(o["extension"])

    p["catalyst"] = [
        {"name": c["name"], "z_min": float(c["z_min"]), "z_max": float(c["z_max"])}
        for c in spec.get("catalyst_layers", [])
    ]
    t = spec.get("tessellation", {})
    p["lin"] = float(t.get("linear_deflection", 0.002))
    p["ang"] = float(t.get("angular_deflection", 0.1))
    p["patch_names"] = spec["patches"]
    return p


# ---------------------------------------------------------------------------
# 형상 생성
# ---------------------------------------------------------------------------
OUTLET_DIRS = {"-x": (-1, 0, 0), "+x": (1, 0, 0), "-y": (0, -1, 0), "+y": (0, 1, 0),
               "-z": (0, 0, -1)}


def outlet_axis(p):
    """출구 덕트가 붙는 하우징 벽 위의 중심점과 방향"""
    if p["side"] not in OUTLET_DIRS:
        sys.exit(f"[ERROR] outlet_duct.side는 {list(OUTLET_DIRS)} 중 하나여야 함")
    d = OUTLET_DIRS[p["side"]]
    if d[2] != 0:  # 바닥 출구: 벽 평면 z = 0
        return (p["xc"], p["yc"], 0.0), d, 0.0
    half = p["W"] / 2 if d[0] != 0 else p["D"] / 2
    # 덕트 축에 수직인 가로 좌표: x면이면 y, y면이면 x
    if d[0] != 0:
        wall_pt = (d[0] * half, p["yc"], p["zc"])
    else:
        wall_pt = (p["yc"], d[1] * half, p["zc"])
    return wall_pt, d, half


def check_params(p):
    errs = []
    for k in ("W", "D", "H", "Di", "Hd", "Do", "Li", "Lo"):
        if not (isinstance(p[k], (int, float)) and p[k] > 0):
            errs.append(f"{k} 값이 양수가 아님: {p[k]}")
    if p["Di"] >= min(p["W"], p["D"]):
        errs.append("입구 덕트 직경이 하우징 단면보다 큼")
    r = p["Do"] / 2
    if p["side"] == "-z":
        if abs(p["xc"]) + r > p["W"] / 2 or abs(p["yc"]) + r > p["D"] / 2:
            errs.append("바닥 출구 덕트가 하우징 바닥면을 벗어남")
    else:
        if p["zc"] - r < 0 or p["zc"] + r > p["H"]:
            errs.append(f"출구 덕트가 하우징 높이 범위를 벗어남 (center_z={p['zc']}, D={p['Do']})")
        lateral = p["D"] if p["side"] in ("-x", "+x") else p["W"]
        if abs(p["yc"]) + r > lateral / 2:
            errs.append("출구 덕트가 하우징 측면 폭을 벗어남")
    for c in p["catalyst"]:
        if not (0 <= c["z_min"] < c["z_max"] <= p["H"]):
            errs.append(f"{c['name']} z 범위가 하우징 밖이거나 잘못됨")
    if errs:
        for e in errs:
            print("[ERROR]", e)
        sys.exit(1)


def build_fluid(p):
    W, D, H, Hd = p["W"], p["D"], p["H"], p["Hd"]
    housing = cq.Workplane("XY").box(W, D, H, centered=(True, True, False))

    diffuser = (cq.Workplane("XY").workplane(offset=H).rect(W, D)
                .workplane(offset=Hd).circle(p["Di"] / 2)
                .loft(ruled=False))

    inlet = (cq.Workplane("XY").workplane(offset=H + Hd)
             .circle(p["Di"] / 2).extrude(p["Li"] + p["Ei"]))

    wall_pt, d, _ = outlet_axis(p)
    overlap = 0.05  # 하우징 안쪽으로 겹쳐서 boolean 안정화
    start = cq.Vector(*[wall_pt[k] - d[k] * overlap for k in range(3)])
    outlet = cq.Solid.makeCylinder(p["Do"] / 2, p["Lo"] + p["Eo"] + overlap,
                                   start, cq.Vector(*d))

    fluid = housing.union(diffuser).union(inlet).union(cq.Workplane().add(outlet)).clean()
    solids = fluid.solids().vals()
    if len(solids) != 1:
        sys.exit(f"[ERROR] boolean 결과 solid가 {len(solids)}개 (1개여야 함)")
    return solids[0]


# ---------------------------------------------------------------------------
# 패치 분류
# ---------------------------------------------------------------------------
def classify(face, p, names):
    bb = face.BoundingBox()
    c = bb.center
    H, Hd = p["H"], p["Hd"]
    z_top = H + Hd + p["Li"] + p["Ei"]
    wall_pt, d, half = outlet_axis(p)
    L_out = p["Lo"] + p["Eo"]
    end = [wall_pt[k] + d[k] * L_out for k in range(3)]
    planar = face.geomType() == "PLANE"

    if planar and abs(bb.zmin - z_top) < EPS and abs(bb.zmax - z_top) < EPS:
        return names["inlet"]
    if planar and math.dist((c.x, c.y, c.z), end) < 1e-4:
        return names["outlet"]

    w_in, w_diff, w_house, w_out = names["walls"]
    if bb.zmin >= H + Hd - EPS:
        return w_in
    if bb.zmin >= H - EPS:
        return w_diff
    # 출구 덕트: 하우징 벽 바깥쪽에 있는 면
    if d[2] != 0:
        return w_out if (bb.zmax <= EPS and bb.zmin < -EPS) else w_house
    axis = 0 if d[0] != 0 else 1
    lo = (bb.xmin, bb.ymin)[axis]
    hi = (bb.xmax, bb.ymax)[axis]
    # 덕트 면은 벽 평면(±half)에서 바깥쪽으로 뻗어 나가야 함 (벽 자체는 제외)
    if (d[axis] < 0 and hi <= -half + EPS and lo < -half - EPS) or \
       (d[axis] > 0 and lo >= half - EPS and hi > half + EPS):
        return w_out
    return w_house


# ---------------------------------------------------------------------------
# 삼각화 & STL 출력
# ---------------------------------------------------------------------------
def tessellate(solid, p):
    # 분류는 삼각화 전에 수행 (삼각화 후 BoundingBox는 메쉬 기준으로 약간 커질 수 있음)
    names = [(face, classify(face, p, p["patch_names"])) for face in solid.Faces()]
    BRepMesh_IncrementalMesh(solid.wrapped, p["lin"], False, p["ang"], True)
    patches = {}
    for face, name in names:
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face.wrapped, loc)
        if tri is None:
            sys.exit("[ERROR] 삼각화 실패한 면이 있음")
        trsf = loc.Transformation()
        nodes = []
        for i in range(1, tri.NbNodes() + 1):
            pt = tri.Node(i).Transformed(trsf)
            nodes.append((pt.X(), pt.Y(), pt.Z()))
        rev = face.wrapped.Orientation() == TopAbs_REVERSED
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            if rev:
                b, c = c, b
            patches.setdefault(name, []).append((nodes[a - 1], nodes[b - 1], nodes[c - 1]))
    return patches


def normal(t):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    m = math.sqrt(sum(k * k for k in n)) or 1.0
    return tuple(k / m for k in n)


def stl_block(name, tris):
    out = [f"solid {name}"]
    for t in tris:
        n = normal(t)
        out.append(f"  facet normal {n[0]:.6e} {n[1]:.6e} {n[2]:.6e}")
        out.append("    outer loop")
        for vtx in t:
            out.append(f"      vertex {vtx[0]:.9e} {vtx[1]:.9e} {vtx[2]:.9e}")
        out.append("    endloop")
        out.append("  endfacet")
    out.append(f"endsolid {name}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 격자 단계로 넘길 정보
# ---------------------------------------------------------------------------
def location_in_mesh(p):
    """유체 내부 & 촉매층 밖 & 격자 면과 겹치지 않도록 약간 어긋난 점"""
    edges = sorted({0.0, p["H"]} | {c[k] for c in p["catalyst"] for k in ("z_min", "z_max")})
    gaps = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)
            if not any(c["z_min"] <= edges[i] and edges[i + 1] <= c["z_max"] for c in p["catalyst"])]
    lo, hi = max(gaps, key=lambda g: g[1] - g[0]) if gaps else (0.0, p["H"])
    return [0.0123 * p["W"], 0.0137 * p["D"], round(0.5 * (lo + hi) + 0.0071, 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?", default="geometry_spec.yaml")
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    spec_path = pathlib.Path(args.spec)
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    p = load_params(spec)
    check_params(p)

    out = pathlib.Path(args.out)
    (out / "patches").mkdir(parents=True, exist_ok=True)

    solid = build_fluid(p)
    patches = tessellate(solid, p)

    expected = [p["patch_names"]["inlet"], p["patch_names"]["outlet"], *p["patch_names"]["walls"]]
    missing = [n for n in expected if n not in patches]
    if missing:
        sys.exit(f"[ERROR] 생성되지 않은 패치: {missing}")

    combined = ""
    for name in expected:
        block = stl_block(name, patches[name])
        (out / "patches" / f"{name}.stl").write_text(block)
        combined += block
    (out / "fluid.stl").write_text(combined)
    cq.exporters.export(cq.Workplane().add(solid), str(out / "fluid.step"))

    # 기대 bbox는 사양값으로 해석적으로 계산 (OCC bbox는 B-spline 때문에 느슨함)
    wall_pt, d, half = outlet_axis(p)
    end = [wall_pt[k] + d[k] * (p["Lo"] + p["Eo"]) for k in range(3)]
    bmin = [min(-p["W"] / 2, end[0]), min(-p["D"] / 2, end[1]), min(0.0, end[2])]
    bmax = [max(p["W"] / 2, end[0]), max(p["D"] / 2, end[1]), p["H"] + p["Hd"] + p["Li"] + p["Ei"]]
    info = {
        "units": "m",
        "spec": str(spec_path),
        "revision": spec.get("meta", {}).get("revision"),
        "stl": "fluid.stl",
        "patches": {n: len(patches[n]) for n in expected},
        "patch_roles": {"inlet": expected[0], "outlet": expected[1], "walls": expected[2:]},
        "bbox": {"min": bmin, "max": bmax},
        "cad_volume": solid.Volume(),
        "housing": {"W": p["W"], "D": p["D"], "H": p["H"]},
        "diffuser_height": p["Hd"],
        "inlet": {"diameter": p["Di"], "length_total": p["Li"] + p["Ei"]},
        "outlet": {"diameter": p["Do"], "side": p["side"], "center": list(wall_pt),
                   "center_z": p["zc"], "length_total": p["Lo"] + p["Eo"]},
        "catalyst_zones": [
            {**c, "box_min": [-p["W"] / 2, -p["D"] / 2, c["z_min"]],
             "box_max": [p["W"] / 2, p["D"] / 2, c["z_max"]]} for c in p["catalyst"]
        ],
        "location_in_mesh": location_in_mesh(p),
        "flags": collect_flags(spec),
    }
    (out / "geometry_out.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))

    print(f"[OK] {out/'fluid.stl'} 생성")
    for n in expected:
        print(f"     {n:<18} {len(patches[n]):>7} triangles")
    print(f"[OK] CAD volume = {solid.Volume():.4f} m^3")


if __name__ == "__main__":
    main()
