#!/usr/bin/env python3
"""
STL 검증 + 미리보기 + 체크포인트 보고서 생성

검사 항목:
  1) 닫힌 형상(watertight), 법선 일관성, 바깥 방향 법선(부피 > 0), 단일 body
  2) STL 부피 vs CAD 부피 (삼각화 오차)
  3) 패치 이름이 spec과 일치, 빈 패치 없음
  4) 설계 규칙: 출구 덕트 vs 촉매층 위치 관계 등
  5) OpenFOAM surfaceCheck (환경이 로드되어 있을 때만)

출력: out/geometry_report.md, out/preview_*.png
종료 코드: FAIL이 하나라도 있으면 1
"""
import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

COLORS = {
    "inlet": "#2e7d32", "outlet": "#c62828", "wall_inlet_duct": "#90a4ae",
    "wall_diffuser": "#5c6bc0", "wall_housing": "#b0bec5", "wall_outlet_duct": "#8d6e63",
}

results = []  # (level, item, detail)


def rec(level, item, detail=""):
    results.append((level, item, detail))


def load_patches(out, names):
    meshes = {}
    for n in names:
        f = out / "patches" / f"{n}.stl"
        if not f.exists():
            rec("FAIL", f"패치 파일 {n}", "없음")
            continue
        m = trimesh.load(f, force="mesh", process=False)
        meshes[n] = m
    return meshes


def check_mesh(meshes, info):
    full = trimesh.util.concatenate(list(meshes.values()))
    full.merge_vertices(digits_vertex=8)
    full.remove_unreferenced_vertices()

    rec("PASS" if full.is_watertight else "FAIL", "닫힌 형상 (watertight)",
        f"open edge 존재" if not full.is_watertight else "")
    rec("PASS" if full.is_winding_consistent else "FAIL", "법선 방향 일관성")
    vol = full.volume
    rec("PASS" if vol > 0 else "FAIL", "법선이 바깥 방향 (부피 > 0)", f"{vol:.4f} m³")
    bodies = full.body_count
    rec("PASS" if bodies == 1 else "FAIL", "단일 body", f"{bodies}개")

    cad = info["cad_volume"]
    err = abs(vol - cad) / cad * 100
    rec("PASS" if err < 0.5 else "WARN", "STL 부피 vs CAD 부피",
        f"STL {vol:.4f} / CAD {cad:.4f} m³ (오차 {err:.3f}%)")

    bmin, bmax = full.bounds
    emin, emax = np.array(info["bbox"]["min"]), np.array(info["bbox"]["max"])
    db = max(np.abs(bmin - emin).max(), np.abs(bmax - emax).max())
    rec("PASS" if db < 1e-3 else "WARN", "bounding box 일치",
        f"min {np.round(bmin, 4).tolist()}, max {np.round(bmax, 4).tolist()} (m)")

    # 단위 sanity: 수 m 규모 장치인데 1000배 크기면 mm 단위 의심
    span = (bmax - bmin).max()
    rec("PASS" if 0.05 < span < 100 else "FAIL", "단위 sanity (m)", f"최대 길이 {span:.3f} m")

    edges = full.edges_unique_length
    rec("INFO", "삼각형 변 길이", f"min {edges.min()*1000:.2f} mm, max {edges.max()*1000:.1f} mm")
    return full


def check_design(info):
    cat = sorted(info["catalyst_zones"], key=lambda c: -c["z_max"])
    H = info["housing"]["H"]
    for a, b in zip(cat, cat[1:]):
        if b["z_max"] > a["z_min"]:
            rec("FAIL", "촉매층 겹침", f"{a['name']} / {b['name']}")
    o = info["outlet"]
    if o["side"] == "-z":
        if cat:
            rec("INFO", "출구 위치", f"하우징 바닥 {o['center'][:2]}, 마지막 촉매층 아래 공간 {cat[-1]['z_min']:.2f} m")
        lim = info["location_in_mesh"]
        rec("INFO", "locationInMesh 후보", str(lim))
        return
    r = o["diameter"] / 2
    o_lo, o_hi = o["center_z"] - r, o["center_z"] + r
    for c in cat:
        if o_lo < c["z_max"] and o_hi > c["z_min"]:
            rec("WARN", "출구 덕트가 촉매층 높이와 겹침",
                f"출구 z {o_lo:.2f}~{o_hi:.2f} m vs {c['name']} z {c['z_min']:.2f}~{c['z_max']:.2f} m "
                "→ 유동이 촉매층을 옆으로 빠져나감. 실제 배치 확인 필요")
    if cat:
        last = cat[-1]
        if o_hi > last["z_min"] + 1e-9 and not any(o_lo < c["z_max"] and o_hi > c["z_min"] for c in cat):
            rec("WARN", "출구가 마지막 촉매층보다 위에 있음", "유동 일부가 마지막 촉매층을 거치지 않음")
        plenum = last["z_min"]
        rec("INFO", "하부 공간 높이 (마지막 촉매층 아래)", f"{plenum:.2f} m (출구 직경 {o['diameter']:.2f} m)")
        if plenum < o["diameter"]:
            rec("WARN", "하부 공간이 출구 직경보다 작음",
                "출구를 촉매층 아래에 두려면 하부 공간 높이나 덕트 직경 재확인 필요")
    lim = info["location_in_mesh"]
    rec("INFO", "locationInMesh 후보", str(lim))


def run_surface_check(out):
    exe = shutil.which("surfaceCheck")
    if not exe:
        rec("SKIP", "OpenFOAM surfaceCheck", "OpenFOAM 환경이 로드되지 않음 (WSL에서 source 후 재실행)")
        return
    log = out / "surfaceCheck.log"
    r = subprocess.run([exe, "fluid.stl"], cwd=out, capture_output=True, text=True)
    log.write_text(r.stdout + r.stderr)
    txt = r.stdout
    closed = re.search(r"Surface is closed", txt) is not None
    rec("PASS" if closed and r.returncode == 0 else "FAIL", "OpenFOAM surfaceCheck",
        "closed" if closed else "surfaceCheck.log 확인")
    m = re.search(r"Surface has (\d+) illegal triangles", txt)
    if m and int(m.group(1)) > 0:
        rec("FAIL", "surfaceCheck illegal triangles", m.group(1))


# ---------------------------------------------------------------------------
# 미리보기
# ---------------------------------------------------------------------------
def preview_3d(meshes, out, elev, azim, fname, title):
    fig = plt.figure(figsize=(7, 8))
    ax = fig.add_subplot(111, projection="3d")
    for n, m in meshes.items():
        ax.add_collection3d(Poly3DCollection(m.triangles, facecolor=COLORS.get(n, "#999"),
                                             edgecolor="none", alpha=0.85 if "wall" in n else 1.0))
    allv = np.vstack([m.vertices for m in meshes.values()])
    lo, hi = allv.min(0), allv.max(0)
    c, s = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(c[0] - s, c[0] + s); ax.set_ylim(c[1] - s, c[1] + s); ax.set_zlim(c[2] - s, c[2] + s)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=elev, azim=azim)
    ax.set_xlabel("x [m]"); ax.set_ylabel("y [m]"); ax.set_zlabel("z [m]")
    handles = [plt.Line2D([0], [0], marker="s", ls="", color=COLORS.get(n, "#999"), label=n) for n in meshes]
    ax.legend(handles=handles, loc="upper left", fontsize=8)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out / fname, dpi=130)
    plt.close(fig)


def preview_section(info, out):
    """덕트 축을 지나는 수직 단면 개략도 (PPT 스케치와 비교용)"""
    W, H = info["housing"]["W"], info["housing"]["H"]
    Hd = info["diffuser_height"]
    Di, Li = info["inlet"]["diameter"], info["inlet"]["length_total"]
    o = info["outlet"]
    Do, Lo = o["diameter"], o["length_total"]
    sgn = -1 if o["side"].startswith("-") else 1

    fig, ax = plt.subplots(figsize=(7, 9))
    ax.add_patch(Rectangle((-W / 2, 0), W, H, fill=False, lw=2, ec="#1e88e5"))
    ax.add_patch(Polygon([(-W / 2, H), (-Di / 2, H + Hd), (Di / 2, H + Hd), (W / 2, H)],
                         closed=True, fill=False, lw=1.5, ec="#333"))
    ax.add_patch(Rectangle((-Di / 2, H + Hd), Di, Li, fill=False, lw=1.5, ec="#333"))
    bottom = o["side"] == "-z"
    if bottom:
        xc = o["center"][0]
        ax.add_patch(Rectangle((xc - Do / 2, -Lo), Do, Lo, fill=False, lw=1.5, ec="#333"))
    else:
        x0 = sgn * W / 2
        ax.add_patch(Rectangle((min(x0, x0 + sgn * Lo), o["center_z"] - Do / 2), Lo, Do,
                               fill=False, lw=1.5, ec="#333"))
    for c in info["catalyst_zones"]:
        ax.add_patch(Rectangle((-W / 2, c["z_min"]), W, c["z_max"] - c["z_min"],
                               fc="#ef9a9a", ec="#c62828", lw=1.5, alpha=0.6))
        ax.text(0, (c["z_min"] + c["z_max"]) / 2, f"{c['name']}\nz {c['z_min']:.2f}~{c['z_max']:.2f}",
                ha="center", va="center", fontsize=8)
    ax.annotate("", xy=(0, H + Hd + Li * 0.6), xytext=(0, H + Hd + Li), arrowprops=dict(color="#2e7d32", lw=3))
    if bottom:
        ax.annotate("", xy=(xc, -Lo), xytext=(xc, -Lo * 0.6), arrowprops=dict(color="#c62828", lw=3))
    else:
        ax.annotate("", xy=(x0 + sgn * Lo, o["center_z"]), xytext=(x0 + sgn * Lo * 0.6, o["center_z"]),
                    arrowprops=dict(color="#c62828", lw=3))
    lim = info["location_in_mesh"]
    ax.plot(lim[0], lim[2], "k*", ms=12, label="locationInMesh")
    ax.set_aspect("equal"); ax.grid(alpha=0.3)
    ax.set_xlabel("y [m]" if o["side"][1] == "y" else "x [m]"); ax.set_ylabel("z [m]")
    ax.set_title("Vertical section (schematic)")
    ax.legend(loc="lower right", fontsize=8)
    ax.autoscale_view()
    fig.tight_layout()
    fig.savefig(out / "preview_section.png", dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------
def write_report(info, meshes, out):
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️", "SKIP": "⏭️"}
    n_fail = sum(1 for r in results if r[0] == "FAIL")
    n_warn = sum(1 for r in results if r[0] == "WARN")
    verdict = "FAIL" if n_fail else ("WARN - 확인 필요" if n_warn else "PASS")

    L = [f"# 형상 체크포인트 보고서 (rev {info.get('revision')})", "",
         f"**판정: {verdict}**  (FAIL {n_fail}, WARN {n_warn})", "",
         "## 1. 자동 검사", "", "| 결과 | 항목 | 내용 |", "|---|---|---|"]
    L += [f"| {icon[l]} {l} | {i} | {d} |" for l, i, d in results]

    L += ["", "## 2. 패치", "", "| 패치 | 삼각형 수 | 면적 [m²] |", "|---|---|---|"]
    L += [f"| {n} | {len(m.faces)} | {m.area:.4f} |" for n, m in meshes.items()]

    L += ["", "## 3. 가정·추정값 (사용자 확인 필요)", "",
          "| 항목 | 값 | 상태 | 비고 |", "|---|---|---|---|"]
    L += [f"| {f['item']} | {f['value']} | {f['status']} | {f['note']} |" for f in info["flags"]]

    L += ["", "## 4. 미리보기", "",
          "![section](preview_section.png)", "", "![iso](preview_iso.png)", "", "![back](preview_back.png)", "",
          "## 5. 사용자 확인 사항", "",
          "- [ ] 단면도가 PPT 스케치/실물과 일치하는가 (특히 출구 높이, 촉매층 위치)",
          "- [ ] 3장의 가정·추정값을 실측값으로 교체했거나, 가정으로 진행해도 되는가",
          "- [ ] 패치 구분(색상)이 의도와 일치하는가",
          "",
          "승인 시 `01_geometry/GEOMETRY_APPROVED` 파일을 직접 생성하세요. 이 파일이 없으면 격자 단계로 진행하지 않습니다."]
    (out / "geometry_report.md").write_text("\n".join(L), encoding="utf-8")
    return n_fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    info = json.loads((out / "geometry_out.json").read_text(encoding="utf-8"))
    names = [info["patch_roles"]["inlet"], info["patch_roles"]["outlet"], *info["patch_roles"]["walls"]]

    meshes = load_patches(out, names)
    for n, m in meshes.items():
        if len(m.faces) == 0:
            rec("FAIL", f"빈 패치 {n}")
    rec("PASS" if len(meshes) == len(names) else "FAIL", "패치 이름 spec 일치", ", ".join(names))

    check_mesh(meshes, info)
    check_design(info)
    run_surface_check(out)

    preview_3d(meshes, out, 20, -60, "preview_iso.png", "Patches (front iso)")
    preview_3d(meshes, out, 20, 120, "preview_back.png", "Patches (back iso)")
    preview_section(info, out)

    n_fail = write_report(info, meshes, out)
    for l, i, d in results:
        print(f"[{l}] {i} {('- ' + d) if d else ''}")
    print(f"\n보고서: {out/'geometry_report.md'}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
