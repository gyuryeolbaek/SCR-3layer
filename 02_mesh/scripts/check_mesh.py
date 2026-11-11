#!/usr/bin/env python3
"""
격자 검증 + 단면 그림 + 체크포인트 보고서

판정 항목:
  FAIL  checkMesh 표준 검사 실패, 품질 기준 초과(non-ortho, skewness), 음수 체적,
        패치 누락/빈 패치/예상 밖 패치, 촉매 cellZone 누락, 체적 오차 과다
  WARN  레이어 커버리지 < 목표, 촉매층 경계-격자면 불일치, 체적 오차 경미
출력: report/mesh_report.md, report/mesh_*.png  (FAIL 있으면 종료 코드 1)
"""
import argparse
import json
import pathlib
import re
import sys

import numpy as np
import yaml
import vtk
from vtk.util.numpy_support import vtk_to_numpy as v2n
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection

vtk.vtkObject.GlobalWarningDisplayOff()  # 다면체 단면 경고 숨김 (그림에만 영향)

results = []


def rec(level, item, detail=""):
    results.append((level, item, detail))


# ---------------------------------------------------------------------------
# 로그 파싱
# ---------------------------------------------------------------------------
def parse_checkmesh(path):
    t = path.read_text(errors="ignore") if path.exists() else ""

    def g(pat, cast=float):
        m = re.search(pat, t)
        return cast(m.group(1)) if m else None

    d = {
        "max_non_ortho": g(r"non-orthogonality Max:\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"),
        "avg_non_ortho": g(r"non-orthogonality Max:\s*[\d.eE+-]+\s+average:\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"),
        "max_skew": g(r"Max skewness\s*=\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"),
        "max_aspect": g(r"Max aspect ratio\s*=\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"),
        "min_vol": g(r"Min volume\s*=\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"),
        "total_vol": g(r"Total volume\s*=\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"),
        "failed": g(r"Failed\s+(\d+)\s+mesh checks", int),
        "ok": "Mesh OK" in t,
        "stars": [l.strip() for l in t.splitlines() if l.strip().startswith("***")],
        "exists": bool(t),
    }
    m = re.search(r"Mesh stats.*?cells:\s+(\d+)", t, re.S)
    d["cells"] = int(m.group(1)) if m else None
    types = {}
    for k in ("hexahedra", "prisms", "wedges", "pyramids", "tet wedges", "tetrahedra", "polyhedra"):
        m = re.search(rf"^\s*{k}:\s+(\d+)", t, re.M)
        if m:
            types[k] = int(m.group(1))
    d["types"] = types
    return d


def parse_layers(path):
    if not path.exists():
        return {}
    t = path.read_text(errors="ignore")
    out = {}
    # 최종 결과 표 (target/mesh 열이 있는 표)
    m = re.search(r"patch\s+faces\s+layers\s+overall thickness[^\n]*\n[^\n]*target[^\n]*\n-----[^\n]*\n(.*?)\n\s*\n", t, re.S)
    if not m:
        return out
    for line in m.group(1).splitlines():
        p = line.split()
        if len(p) >= 6:
            out[p[0]] = {"faces": int(p[1]), "target": float(p[2]), "mesh": float(p[3]),
                         "thickness": float(p[4]), "thickness_pct": float(p[5])}
    return out


def parse_levels(path):
    t = path.read_text(errors="ignore") if path.exists() else ""
    blocks = re.findall(r"Cells per refinement level:\n((?:\s+\d+\s+\d+\n)+)", t)
    if not blocks:
        return {}
    return {int(a): int(b) for a, b in re.findall(r"(\d+)\s+(\d+)", blocks[-1])}


def parse_boundary(path):
    t = path.read_text(errors="ignore")
    t = t[t.find("FoamFile"):]
    t = t[t.find("}") + 1:]
    out = {}
    for name, body in re.findall(r"\n\s*([A-Za-z_][\w.:-]*)\s*\n\s*\{(.*?)\}", t, re.S):
        tp = re.search(r"type\s+(\w+);", body)
        nf = re.search(r"nFaces\s+(\d+);", body)
        if tp and nf:
            out[name] = {"type": tp.group(1), "nFaces": int(nf.group(1))}
    return out


# ---------------------------------------------------------------------------
# VTK: cellZone 체적, 단면
# ---------------------------------------------------------------------------
def read_case(case):
    r = vtk.vtkOpenFOAMReader()
    r.SetFileName(str(case / "case.foam"))
    r.ReadZonesOn()
    r.Update()
    mb = r.GetOutput()
    internal, zones = None, {}
    for i in range(mb.GetNumberOfBlocks()):
        name = mb.GetMetaData(i).Get(vtk.vtkCompositeDataSet.NAME())
        if name == "internalMesh":
            internal = mb.GetBlock(i)
        elif name == "zones":
            zb = mb.GetBlock(i)
            for j in range(zb.GetNumberOfBlocks()):
                if zb.GetMetaData(j).Get(vtk.vtkCompositeDataSet.NAME()) == "cellZones":
                    cz = zb.GetBlock(j)
                    for k in range(cz.GetNumberOfBlocks()):
                        zones[cz.GetMetaData(k).Get(vtk.vtkCompositeDataSet.NAME())] = cz.GetBlock(k)
    return internal, zones


def cell_volumes(grid):
    f = vtk.vtkCellSizeFilter()
    f.SetInputData(grid)
    f.ComputeVolumeOn(); f.ComputeAreaOff(); f.ComputeLengthOff(); f.ComputeVertexCountOff()
    f.Update()
    return v2n(f.GetOutput().GetCellData().GetArray("Volume"))


def zone_z_range(grid):
    """셀 꼭짓점 평균으로 셀 중심 z 계산 (다면체에서도 안정적)"""
    cells = grid.GetCells()
    off = v2n(cells.GetOffsetsArray()).astype(np.int64)
    conn = v2n(cells.GetConnectivityArray()).astype(np.int64)
    z = v2n(grid.GetPoints().GetData())[:, 2][conn]
    zc = np.add.reduceat(z, off[:-1]) / np.diff(off)
    return float(zc.min()), float(zc.max())


def cut(grid, origin, normal):
    pl = vtk.vtkPlane()
    pl.SetOrigin(*origin); pl.SetNormal(*normal)
    c = vtk.vtkCutter()
    c.SetCutFunction(pl); c.SetInputData(grid); c.GenerateTrianglesOff(); c.Update()
    pd = c.GetOutput()
    if not pd.GetNumberOfPoints():
        return np.zeros((0, 3)), []
    pts = v2n(pd.GetPoints().GetData())
    polys, ids = [], vtk.vtkIdList()
    for i in range(pd.GetNumberOfCells()):
        pd.GetCellPoints(i, ids)
        polys.append([ids.GetId(k) for k in range(ids.GetNumberOfIds())])
    return pts, polys


def draw(ax, internal, zones, origin, normal, axes, xlim=None, ylim=None, lw=0.15):
    a, b = axes
    for g in zones.values():
        zp, zpolys = cut(g, origin, normal)
        if len(zp):
            ax.add_collection(PolyCollection([zp[p][:, [a, b]] for p in zpolys],
                                             facecolor="#ef9a9a", edgecolor="none", alpha=0.5))
    pts, polys = cut(internal, origin, normal)
    if len(pts):
        ax.add_collection(PolyCollection([pts[p][:, [a, b]] for p in polys],
                                         facecolor="none", edgecolor="#263238", linewidth=lw))
    ax.set_aspect("equal")
    ax.autoscale_view()
    if xlim:
        ax.set_xlim(*xlim)
    if ylim:
        ax.set_ylim(*ylim)


def make_figures(internal, zones, info, rep):
    W, H, Hd = info["housing"]["W"], info["housing"]["H"], info["diffuser_height"]
    y0 = 0.0071 * W  # 격자면과 겹치지 않도록 살짝 어긋난 단면

    fig, ax = plt.subplots(figsize=(6, 12))
    draw(ax, internal, zones, (0, y0, 0), (0, 1, 0), (0, 2), lw=0.08)
    ax.set_title("Mesh section y~0  (red: catalyst cellZones)")
    ax.set_xlabel("x [m]"); ax.set_ylabel("z [m]")
    fig.tight_layout(); fig.savefig(rep / "mesh_section_full.png", dpi=170); plt.close(fig)

    gap_z = info["location_in_mesh"][2]
    o = info["outlet"]
    fig, axs = plt.subplots(1, 3, figsize=(16, 6))
    draw(axs[0], internal, zones, (0, y0, 0), (0, 1, 0), (0, 2),
         xlim=(W / 2 - 0.35, W / 2 + 0.02), ylim=(H - 0.25, H + Hd * 0.6))
    axs[0].set_title("Diffuser-housing junction (x-z)")
    if o["side"] == "-z":
        r = o["diameter"] / 2
        draw(axs[1], internal, zones, (0, y0, 0), (0, 1, 0), (0, 2),
             xlim=(o["center"][0] - r - 0.15, o["center"][0] + r + 0.05), ylim=(-0.3, 0.3))
        axs[1].set_title("Outlet junction (x-z)")
    else:
        draw(axs[1], internal, zones, (0, y0, 0), (0, 1, 0), (0, 2))
        axs[1].set_title("Section (x-z)")
    draw(axs[2], internal, zones, (0, 0, gap_z), (0, 0, 1), (0, 1),
         xlim=(W / 2 - 0.2, W / 2 + 0.02), ylim=(W / 2 - 0.2, W / 2 + 0.02), lw=0.3)
    axs[2].set_title(f"Housing corner z={gap_z:.2f} m (x-y, wall layers)")
    for a in axs:
        a.set_xlabel("[m]")
    fig.tight_layout(); fig.savefig(rep / "mesh_section_details.png", dpi=150); plt.close(fig)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="case")
    ap.add_argument("--spec", default="mesh_spec.yaml")
    args = ap.parse_args()
    case = pathlib.Path(args.case)
    spec = yaml.safe_load(pathlib.Path(args.spec).read_text(encoding="utf-8"))
    gdir = pathlib.Path(spec["input"]["geometry_dir"])
    info = json.loads((gdir / spec["input"]["info"]).read_text(encoding="utf-8"))
    meta = json.loads((case / "mesh_case.json").read_text(encoding="utf-8"))
    q, lay = spec["quality"], spec["layers"]
    rep = pathlib.Path("report")
    rep.mkdir(exist_ok=True)

    # --- checkMesh ---------------------------------------------------------
    cm = parse_checkmesh(case / "log.checkMesh")
    cm0 = parse_checkmesh(case / "log.checkMesh.noLayers")
    cma = parse_checkmesh(case / "log.checkMesh.all")
    if not cm["exists"] or cm["max_non_ortho"] is None:
        rec("FAIL", "checkMesh 결과 없음", "case/log.checkMesh 확인")
    else:
        rec("PASS" if cm["ok"] else "FAIL", "checkMesh 표준 검사",
            "Mesh OK" if cm["ok"] else f"Failed {cm['failed']} checks: " + "; ".join(cm["stars"]))
        rec("PASS" if cm["max_non_ortho"] <= q["max_non_ortho"] else "FAIL", "최대 non-orthogonality",
            f"{cm['max_non_ortho']:.1f} (기준 ≤ {q['max_non_ortho']}, 평균 {cm['avg_non_ortho']:.1f})")
        rec("PASS" if cm["max_skew"] <= q["max_skewness"] else "FAIL", "최대 skewness",
            f"{cm['max_skew']:.2f} (기준 ≤ {q['max_skewness']})")
        rec("PASS" if cm["min_vol"] > 0 else "FAIL", "최소 셀 체적 > 0", f"{cm['min_vol']:.3e} m³")
        rec("INFO", "최대 aspect ratio", f"{cm['max_aspect']:.1f}")
        cad = info["cad_volume"]
        err = abs(cm["total_vol"] - cad) / cad * 100
        rec("PASS" if err < 1 else "FAIL", "유체 영역 체적 vs 형상 체적",
            f"격자 {cm['total_vol']:.4f} / 형상 {cad:.4f} m³ (오차 {err:.2f}%)")
    if cma["stars"]:
        rec("INFO", "추가 검사(-allGeometry) 참고",
            "; ".join(cma["stars"]) + " → snappy 격자에서 흔한 항목, 판정 제외")

    # --- 패치 --------------------------------------------------------------
    bnd = parse_boundary(case / "constant" / "polyMesh" / "boundary")
    roles = info["patch_roles"]
    expected = {roles["inlet"]: "patch", roles["outlet"]: "patch", **{w: "wall" for w in roles["walls"]}}
    ok = True
    for p, tp in expected.items():
        if p not in bnd:
            rec("FAIL", f"패치 {p}", "없음"); ok = False
        elif bnd[p]["nFaces"] == 0:
            rec("FAIL", f"패치 {p}", "면 0개"); ok = False
        elif bnd[p]["type"] != tp:
            rec("FAIL", f"패치 {p} 타입", f"{bnd[p]['type']} (기대 {tp})"); ok = False
    extra = {p: b for p, b in bnd.items() if p not in expected and b["nFaces"] > 0}
    if extra:
        ok = False
        rec("FAIL", "예상 밖 패치에 면이 있음",
            ", ".join(f"{p}({b['nFaces']})" for p, b in extra.items()) + " → locationInMesh/누설 확인")
    if ok:
        rec("PASS", "패치 이름·타입 일치", ", ".join(f"{p}:{bnd[p]['type']}" for p in expected))

    # --- 레이어 ------------------------------------------------------------
    if lay["enabled"]:
        layers = parse_layers(case / "log.snappyHexMesh.layers")
        if not layers:
            rec("FAIL", "레이어 결과 없음", "case/log.snappyHexMesh.layers 확인")
        for p, d in layers.items():
            cov = d["mesh"] / d["target"] * 100 if d["target"] else 0
            rec("PASS" if cov >= lay["target_coverage"] else "WARN", f"레이어 커버리지 {p}",
                f"{cov:.0f}% (평균 {d['mesh']:.2f}/{d['target']:.0f}층, 두께 {d['thickness']*1000:.1f} mm, "
                f"기준 ≥ {lay['target_coverage']}%)")

    # --- 촉매 cellZone -----------------------------------------------------
    internal, zones = read_case(case)
    for c in info["catalyst_zones"]:
        name = c["name"]
        if name not in zones or zones[name].GetNumberOfCells() == 0:
            rec("FAIL", f"cellZone {name}", "없음 또는 비어 있음")
            continue
        g = zones[name]
        vol = float(cell_volumes(g).sum())
        exp = info["housing"]["W"] * info["housing"]["D"] * (c["z_max"] - c["z_min"])
        err = abs(vol - exp) / exp * 100
        zlo, zhi = zone_z_range(g)
        lvl = "PASS" if err < 0.5 else ("WARN" if err < 2 else "FAIL")
        rec(lvl, f"cellZone {name}",
            f"{g.GetNumberOfCells():,} cells, 체적 {vol:.4f} / 기대 {exp:.4f} m³ (오차 {err:.2f}%), "
            f"셀 중심 z {zlo:.3f}~{zhi:.3f} (지정 {c['z_min']}~{c['z_max']})")
    for w in meta.get("alignment_warnings", []):
        rec("WARN", "촉매층 경계-격자면 불일치", w)

    make_figures(internal, zones, info, rep)

    # --- 보고서 ------------------------------------------------------------
    levels = parse_levels(case / "log.snappyHexMesh.layers") or parse_levels(case / "log.snappyHexMesh")
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️"}
    nf = sum(r[0] == "FAIL" for r in results)
    nw = sum(r[0] == "WARN" for r in results)
    verdict = "FAIL" if nf else ("WARN - 확인 필요" if nw else "PASS")
    h = meta["background"]["cell_size"]
    bg = meta["background"]["n"]
    L = [f"# 격자 체크포인트 보고서 (mesh rev {spec['meta']['revision']}, geometry rev {info.get('revision')})", "",
         f"**판정: {verdict}**  (FAIL {nf}, WARN {nw})", "",
         "## 1. 요약", "",
         f"- 총 셀 수: **{cm['cells']:,}**" if cm["cells"] else "- 총 셀 수: ?",
         "- 셀 종류: " + ", ".join(f"{k} {n:,}" for k, n in cm["types"].items() if n),
         f"- 배경 격자: {bg[0]}×{bg[1]}×{bg[2]}, h = {h} m",
         "- 레벨별 셀 수 (셀 크기): " + ", ".join(
             f"L{l} {n:,} ({h / 2 ** l * 1000:.1f} mm)" for l, n in sorted(levels.items())),
         ""]
    if cm0["exists"] and cm0["max_non_ortho"] is not None and cm["max_non_ortho"] is not None:
        L += ["| 지표 | 레이어 전 | 최종 |", "|---|---|---|",
              f"| 셀 수 | {cm0['cells']:,} | {cm['cells']:,} |",
              f"| max non-ortho | {cm0['max_non_ortho']:.1f} | {cm['max_non_ortho']:.1f} |",
              f"| max skewness | {cm0['max_skew']:.2f} | {cm['max_skew']:.2f} |",
              f"| max aspect ratio | {cm0['max_aspect']:.1f} | {cm['max_aspect']:.1f} |", ""]
    L += ["## 2. 자동 검사", "", "| 결과 | 항목 | 내용 |", "|---|---|---|"]
    L += [f"| {icon[l]} {l} | {i} | {d} |" for l, i, d in results]
    L += ["", "## 3. 패치", "", "| 패치 | 타입 | 면 수 |", "|---|---|---|"]
    L += [f"| {p} | {b['type']} | {b['nFaces']:,} |" for p, b in bnd.items()]
    L += ["", "## 4. 단면 그림", "",
          "![full](mesh_section_full.png)", "", "![details](mesh_section_details.png)", "",
          "## 5. 사용자 확인 사항", "",
          "- [ ] 단면 그림에서 형상이 제대로 표현되었는가 (덕트, 확장부, 출구 접합부)",
          "- [ ] 촉매층(빨간 영역) 위치와 두께가 의도와 같은가",
          "- [ ] 셀 수와 해상도가 이번 검증 단계에 적절한가 (계산 시간과의 균형)",
          "- [ ] WARN 항목을 이대로 진행해도 되는가",
          "",
          "ParaView로 자세히 보려면 Windows ParaView에서 `02_mesh/case/case.foam`을 여세요.",
          "승인 시 `02_mesh/MESH_APPROVED` 파일을 직접 생성하세요. `./Allmesh`를 다시 실행하면 승인은 자동 해제됩니다."]
    (rep / "mesh_report.md").write_text("\n".join(L), encoding="utf-8")

    for l, i, d in results:
        print(f"[{l}] {i} {('- ' + d) if d else ''}")
    print(f"\n판정: {verdict}\n보고서: {rep / 'mesh_report.md'}")
    sys.exit(1 if nf else 0)


if __name__ == "__main__":
    main()
