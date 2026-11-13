#!/usr/bin/env python3
"""
3-1단계(유동) 결과 검증 + 그림 + 보고서

판정:
  FAIL  솔버 미실행/발산(NaN), 질량 보존 오차 > 0.5 %
  WARN  잔차 목표 미달, 모니터 값이 아직 변함(수렴 미흡), 촉매 내 물리 속도가 논문 계수 산출 범위 밖,
        CFD 압력손실이 1D 이론값과 15 % 이상 차이
  INFO  질량유량(입구 덕트 직경이 가정값인 경우), y+ 범위
출력: <case>/report/flow_report.md, flow_*.png  (FAIL 있으면 종료 코드 1)
사용법: python3 scripts/check_case.py --case flow_2layer [--spec case_spec.yaml]
"""
import argparse
import json
import math
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

vtk.vtkObject.GlobalWarningDisplayOff()
results = []


def rec(level, item, detail=""):
    results.append((level, item, detail))


def v(node):
    return node["value"] if isinstance(node, dict) and "value" in node else node


def read_dat(path):
    """OpenFOAM postProcessing .dat → (컬럼명 리스트, 행 리스트). 재시작으로 생긴 여러 시간 폴더를 합침"""
    files = sorted(path.parent.parent.glob(f"*/{path.name}"), key=lambda p: float(p.parent.name))
    cols, rows = None, {}
    for f in files:
        for line in f.read_text(errors="ignore").splitlines():
            if line.startswith("#"):
                if "Time" in line:
                    cols = [c.strip() for c in line[1:].split("\t") if c.strip()]
                continue
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            if parts:
                # 재시작 시 같은 시간은 나중 값으로. yPlus.dat처럼 한 시간에 패치별로 여러 행이 있으면
                # 두 번째 열(패치 이름)까지 키에 넣어 행이 덮어써지지 않게 함
                key = (float(parts[0]), "" if len(parts) < 2 or _isnum(parts[1]) else parts[1])
                rows[key] = parts
    return cols, [rows[k] for k in sorted(rows)]


def _isnum(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def series(case, fo, file="surfaceFieldValue.dat"):
    cols, rows = read_dat(case / "postProcessing" / fo / "0" / file)
    if not rows:
        return np.array([]), np.array([])
    t = np.array([float(r[0]) for r in rows])
    y = np.array([float(r[1]) for r in rows])
    return t, y


# ---------------------------------------------------------------------------
def read_fields(case, patches=()):
    """마지막 시간의 내부 격자 + 지정한 패치(면 데이터). 반환: internal, {patch: polydata}, t_last"""
    r = vtk.vtkOpenFOAMReader()
    r.SetFileName(str(case / "case.foam"))
    r.ReadZonesOn()
    r.CreateCellToPointOff()
    r.UpdateInformation()
    for i in range(r.GetNumberOfPatchArrays()):
        name = r.GetPatchArrayName(i)
        base = name.split("/")[-1]
        r.SetPatchArrayStatus(name, 1 if (name == "internalMesh" or base in patches) else 0)
    tv = r.GetTimeValues()
    t_last = tv.GetValue(tv.GetNumberOfTuples() - 1) if tv and tv.GetNumberOfTuples() else 0
    r.UpdateTimeStep(t_last)
    mb = r.GetOutput()
    internal, pdata = None, {}
    for i in range(mb.GetNumberOfBlocks()):
        nm = mb.GetMetaData(i).Get(vtk.vtkCompositeDataSet.NAME())
        if nm == "internalMesh":
            internal = mb.GetBlock(i)
        elif nm in ("boundary", "patches", "patch"):
            bb = mb.GetBlock(i)
            for j in range(bb.GetNumberOfBlocks()):
                pn = bb.GetMetaData(j).Get(vtk.vtkCompositeDataSet.NAME())
                if pn in patches:
                    pdata[pn] = bb.GetBlock(j)
    return internal, pdata, t_last


def cell_array_names(ds):
    cd = ds.GetCellData()
    return [cd.GetArrayName(i) for i in range(cd.GetNumberOfArrays())]


def area_average(ds, names):
    """면(패치) 데이터의 면적 가중 평균"""
    f = vtk.vtkCellSizeFilter(); f.SetInputData(ds)
    f.ComputeAreaOn(); f.ComputeVolumeOff(); f.ComputeLengthOff(); f.ComputeVertexCountOff(); f.Update()
    a = v2n(f.GetOutput().GetCellData().GetArray("Area"))
    out = {}
    for n in names:
        arr = ds.GetCellData().GetArray(n)
        if arr is not None:
            out[n] = float(np.sum(v2n(arr) * a) / a.sum())
    return out


def cell_centers(grid):
    f = vtk.vtkCellCenters(); f.SetInputData(grid); f.VertexCellsOff(); f.Update()
    return v2n(f.GetOutput().GetPoints().GetData())


def cut(grid, origin, normal):
    pl = vtk.vtkPlane(); pl.SetOrigin(*origin); pl.SetNormal(*normal)
    c = vtk.vtkCutter(); c.SetCutFunction(pl); c.SetInputData(grid); c.GenerateTrianglesOff(); c.Update()
    pd = c.GetOutput()
    if not pd.GetNumberOfPoints():
        return None
    pts = v2n(pd.GetPoints().GetData())
    polys, ids = [], vtk.vtkIdList()
    for i in range(pd.GetNumberOfCells()):
        pd.GetCellPoints(i, ids)
        polys.append(pts[[ids.GetId(k) for k in range(ids.GetNumberOfIds())]])
    d = {"polys": polys, "area": np.array([poly_area(P, normal) for P in polys])}
    for n in cell_array_names(pd):   # U, p, UMean, pMean, initialResidual:* 등 모든 셀 배열
        d[n] = v2n(pd.GetCellData().GetArray(n))
    return d


def plane_avg(d, name):
    return float(np.sum(d[name] * d["area"]) / d["area"].sum())


def poly_area(P, n):
    n = np.asarray(n, float) / np.linalg.norm(n)
    s = np.zeros(3)
    for i in range(len(P)):
        s += np.cross(P[i], P[(i + 1) % len(P)])
    return abs(np.dot(s, n)) / 2


def uniformity(w, a):
    m = np.sum(w * a) / np.sum(a)
    return 1 - np.sum(a * np.abs(w - m)) / (2 * abs(m) * np.sum(a)), m


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--spec", default="case_spec.yaml")
    args = ap.parse_args()
    case = pathlib.Path(args.case)
    spec = yaml.safe_load(pathlib.Path(args.spec).read_text(encoding="utf-8"))
    ci = json.loads((case / "case_info.json").read_text(encoding="utf-8"))
    mdir = pathlib.Path(spec["mesh"]["dir"])
    mspec = yaml.safe_load((mdir / "mesh_spec.yaml").read_text(encoding="utf-8"))
    info = json.loads(((mdir / mspec["input"]["geometry_dir"]) / mspec["input"]["info"]).read_text(encoding="utf-8"))
    rep = case / "report"; rep.mkdir(exist_ok=True)
    s = spec["solver"]
    rho, mu, eps = ci["rho"], ci["mu"], ci["porosity"]
    W, D = info["housing"]["W"], info["housing"]["D"]

    # --- 솔버 로그 -----------------------------------------------------------
    log = case / f"log.{s['application']}"
    txt = log.read_text(errors="ignore") if log.exists() else ""
    iters = [int(float(x)) for x in re.findall(r"^Time = ([\d.e+]+)", txt, re.M)]
    n_it = iters[-1] if iters else 0
    if not txt:
        rec("FAIL", "솔버 로그 없음", str(log))
    if re.search(r"\bnan\b", txt, re.I) or re.search(r"Foam::sigFpe::sigHandler|Floating point exception \(core dumped\)", txt):
        rec("FAIL", "발산 (NaN)", "log 확인")
    converged = "SIMPLE solution converged" in txt
    rec("PASS" if converged else "INFO", "반복 수",
        f"{n_it} / 최대 {s['max_iterations']}" + (" (잔차 목표 도달로 종료)" if converged else ""))

    cols, rows = read_dat(case / "postProcessing" / "solverInfo" / "0" / "solverInfo.dat")
    res_hist = {}
    if rows:
        for name in ("p", "Ux", "Uy", "Uz", "k", "omega"):
            if f"{name}_initial" in cols:
                j = cols.index(f"{name}_initial")
                res_hist[name] = np.array([float(r[j]) for r in rows])
        it_hist = np.array([float(r[0]) for r in rows])
        last = {k: h[-1] for k, h in res_hist.items()}
        worst = max(last.values())
        # 2026-11-11 사용자 결정: 잔차는 참고용. 박리 유동 때문에 잔차가 평평해져도 촉매층 결과는 수렴함
        rec("INFO", "최종 초기 잔차 (참고)",
            ", ".join(f"{k} {x:.1e}" for k, x in last.items()) + f" (솔버 종료 목표 {s['residual_target']:.0e}, 판정 제외)")

    # --- 질량 보존 ----------------------------------------------------------
    t_in, m_in = series(case, "massFlow_inlet")
    t_out, m_out = series(case, "massFlow_outlet")
    if len(m_in):
        imb = abs(m_in[-1] + m_out[-1]) / abs(m_in[-1]) * 100
        rec("PASS" if imb < 0.5 else "FAIL", "질량 보존 (입구 vs 출구)", f"오차 {imb:.3f}%")
        mdot = abs(m_in[-1]) * rho
        note = " ← 입구 덕트 직경이 가정값" if any(f["item"].startswith("inlet_duct.diameter") and f["status"] != "given"
                                            for f in info.get("flags", [])) else ""
        if ci.get("inlet_condition") == "mass_flow":   # 질량유량 기준 입구 (2026-11-13~)
            tgt = ci["mass_flow_kg_h_nominal"]
            rec("INFO", "질량유량", f"{mdot:.3f} kg/s = {mdot * 3600:.0f} kg/h (지정 {tgt:.0f} kg/h, 차이 "
                f"{(mdot * 3600 / tgt - 1) * 100:+.2f}%, 입구 {ci['inlet_velocity']:.3f} m/s, D {ci['inlet_diameter']} m) "
                f"← 질량유량이 가정값 (case_spec operating.inlet_mass_flow_kg_h)")
        else:
            rec("INFO", "질량유량", f"{mdot:.3f} kg/s = {mdot * 3600:.0f} kg/h (입구 {ci['inlet_velocity']} m/s, "
                f"D {ci['inlet_diameter']} m){note}")
    else:
        mdot = ci["mass_flow_kg_h_nominal"] / 3600

    # --- 압력손실 & 수렴 모니터 ------------------------------------------------
    U_s = mdot / (rho * W * D)  # 하우징 단면 평균 표면속도
    pc = ci["porous_coeffs"]
    mon = {}
    t_pi, p_in = series(case, "p_inlet")
    t_po, p_out = series(case, "p_outlet")
    if len(p_in):
        mon["전체 ΔP"] = (t_pi, (p_in - p_out[:len(p_in)]) * rho)
    dp_1d_of, mon_last = {}, {}
    for c in info["catalyst_zones"]:
        if c["name"] not in ci["active_layers"]:
            continue
        t1, a = series(case, f"p_{c['name']}_in")
        t2, b = series(case, f"p_{c['name']}_out")
        if len(a) and len(b):
            n = min(len(a), len(b))
            dp = (a[:n] - b[:n]) * rho
            mon[f"ΔP {c['name']}"] = (t1[:n], dp)
            mon_last[c["name"]] = dp[-1]
        L = c["z_max"] - c["z_min"]
        dp_1d_of[c["name"]] = L * (mu * pc["d_axial"] * U_s + 0.5 * rho * pc["f_axial"] * U_s ** 2)

    win = s["monitor_window"]
    mon_var = {}
    for name, (t, y) in mon.items():
        if t[-1] < win * 1.5:
            rec("WARN", f"수렴 판정 불가: {name}", f"반복 수({t[-1]:.0f})가 판정 구간({win})에 비해 짧음")
            break
        sel = t > t[-1] - win
        mon_var[name] = (y[sel].max() - y[sel].min()) / max(abs(y[-1]), 1e-12)

    # --- 필드 (마지막 시간): 순간값 U,p / 평균값 UMean,pMean -------------------
    roles = info["patch_roles"]
    internal, pdata, t_last = read_fields(case, patches=(roles["inlet"], roles["outlet"]))
    names = cell_array_names(internal)
    has_mean = "UMean" in names and "pMean" in names
    fa = ci.get("field_average", {}) or {}
    kinds = [("inst", "U", "p")] + ([("mean", "UMean", "pMean")] if has_mean else [])
    label = {"inst": f"순간값 (iter {t_last:.0f})",
             "mean": f"평균값 (iter {fa.get('time_start', '?')}~{t_last:.0f})"}
    prim = "mean" if has_mean else "inst"

    # 수렴 판정 (2026-11-11 사용자 결정): 촉매층 ΔP 모니터 안정성 + 순간/평균 차이 ≤ tol.
    # 전체 ΔP 모니터는 출구 덕트 박리로 흔들리므로, 평균 필드가 있으면 순간/평균 차이로 판정하고 모니터 변동은 참고
    tol = s["monitor_tolerance"]
    for name, var in mon_var.items():
        lvl = "PASS" if var <= tol else "WARN"
        if name == "전체 ΔP" and has_mean:
            lvl = "INFO"
        rec(lvl, f"모니터 안정성: {name} (순간값)",
            f"마지막 {win}회 변동 {var * 100:.2f}% (기준 ≤ {tol * 100:.1f}%)" + (" → 참고, 순간/평균 차이로 판정" if lvl == "INFO" else ""))
    if has_mean:
        rec("INFO", "판정 기준 필드", f"평균 필드 UMean, pMean 사용 ({label['mean']}). 순간값과의 비교는 7절")
    elif fa.get("enabled"):
        rec("WARN", "평균 필드 없음", "fieldAverage가 켜져 있지만 UMean/pMean이 마지막 시간에 없음 → 순간값으로 판정")

    # 압력손실: 모니터와 같은 평면(촉매층 위/아래 0.4h)의 면적 평균 p
    dp_field = {k: {} for k, _, _ in kinds}
    for c in info["catalyst_zones"]:
        if c["name"] not in ci["active_layers"]:
            continue
        zi = ci["monitor_planes"][f"{c['name']}_in"]
        zo = ci["monitor_planes"][f"{c['name']}_out"]
        di, do = cut(internal, (0, 0, zi), (0, 0, 1)), cut(internal, (0, 0, zo), (0, 0, 1))
        for k, _, pn in kinds:
            dp_field[k][c["name"]] = (plane_avg(di, pn) - plane_avg(do, pn)) * rho
    tot = {}
    if roles["inlet"] in pdata and roles["outlet"] in pdata:
        for k, _, pn in kinds:
            ai = area_average(pdata[roles["inlet"]], [pn]); ao = area_average(pdata[roles["outlet"]], [pn])
            if pn in ai and pn in ao:
                tot[k] = (ai[pn] - ao[pn]) * rho

    layer_rows = []
    for name, dp_1d in dp_1d_of.items():
        dp = dp_field[prim].get(name, mon_last.get(name))
        if dp is None:
            continue
        dev = (dp - dp_1d) / dp_1d * 100
        layer_rows.append((name, dp, dp_1d, dev))
        rec("PASS" if abs(dev) < 15 else "WARN", f"압력손실 {name}" + (" (평균값)" if has_mean else ""),
            f"CFD {dp:.1f} Pa vs 1D 이론 {dp_1d:.1f} Pa (차이 {dev:+.1f}%, 균일 유동 가정)")
    if prim in tot:
        rec("INFO", "전체 압력손실 (입구→출구)" + (" (평균값)" if has_mean else ""), f"{tot[prim]:.1f} Pa")
    elif "전체 ΔP" in mon:
        rec("INFO", "전체 압력손실 (입구→출구)", f"{mon['전체 ΔP'][1][-1]:.1f} Pa")

    # 촉매 유속 균일도: 층 깊이 1/3 지점
    lo, hi = spec["validation"]["paper_catalyst_velocity_range"]
    uni = {k: {} for k, _, _ in kinds}
    planes = {}
    for c in info["catalyst_zones"]:
        if c["name"] not in ci["active_layers"]:
            continue
        # 촉매층 길이의 1/3 지점: 다공성 경계면 첫 셀은 셀 중심 속도가 왜곡되므로 피함.
        # 가로 저항이 매우 커서 층 내부에서는 입구의 유속 분포가 그대로 유지됨 (논문 Figure 7)
        z = c["z_max"] - (c["z_max"] - c["z_min"]) / 3
        d = cut(internal, (0, 0, z), (0, 0, 1))
        planes[c["name"]] = (z, d)
        for k, un, _ in kinds:
            w = -d[un][:, 2]  # 아래 방향 유동을 양수로
            g, m = uniformity(w, d["area"])
            uni[k][c["name"]] = (g, m, w.max(), w.min())
    uni_rows = []
    for name, (g, m, wmax, wmin) in uni[prim].items():
        uni_rows.append((name, g, m, wmax, wmin, m / eps))
        tag = " (평균값)" if has_mean else ""
        rec("INFO", f"촉매 유속 균일도 {name} (층 1/3 깊이){tag}",
            f"γ = {g:.3f}, 표면속도 평균 {m:.2f} (최대 {wmax:.2f}, 최소 {wmin:.2f}) m/s")
        ok = lo <= m / eps <= hi
        rec("PASS" if ok else "WARN", f"촉매 내 물리 속도 {name}{tag}",
            f"평균 {m / eps:.2f} m/s, 범위 {wmin / eps:.2f}~{wmax / eps:.2f} m/s "
            f"(논문 계수 산출 범위 {lo}~{hi} m/s)" + ("" if ok else " → 범위 밖이면 다공성 계수 외삽"))

    # 순간값 vs 평균값 비교표
    cmp_rows = []
    if has_mean:
        for name in uni["inst"]:
            gi, gm = uni["inst"][name][0], uni["mean"][name][0]
            cmp_rows.append((f"균일도 γ {name}", f"{gi:.3f}", f"{gm:.3f}", f"{gi - gm:+.3f}"))
            for j, lab in ((2, "최대 표면속도"), (3, "최소 표면속도")):
                xi, xm = uni["inst"][name][j], uni["mean"][name][j]
                cmp_rows.append((f"{lab} {name} [m/s]", f"{xi:.2f}", f"{xm:.2f}", f"{(xi - xm) / xm * 100:+.1f}%"))
        for name in dp_field["inst"]:
            xi, xm = dp_field["inst"][name], dp_field["mean"][name]
            cmp_rows.append((f"ΔP {name} [Pa]", f"{xi:.1f}", f"{xm:.1f}", f"{(xi - xm) / xm * 100:+.2f}%"))
        if "inst" in tot and "mean" in tot:
            cmp_rows.append(("전체 ΔP [Pa]", f"{tot['inst']:.1f}", f"{tot['mean']:.1f}",
                             f"{(tot['inst'] - tot['mean']) / tot['mean'] * 100:+.2f}%"))

    if has_mean:
        checks = [(f"ΔP {n}", dp_field["inst"][n], dp_field["mean"][n]) for n in dp_field["inst"]]
        if "inst" in tot and "mean" in tot:
            checks.append(("전체 ΔP", tot["inst"], tot["mean"]))
        checks += [(f"균일도 γ {n}", uni["inst"][n][0], uni["mean"][n][0]) for n in uni["inst"]]
        for nm, xi, xm in checks:
            dv = abs(xi - xm) / max(abs(xm), 1e-12)
            rec("PASS" if dv <= tol else "WARN", f"순간/평균 차이: {nm}",
                f"{dv * 100:.2f}% (기준 ≤ {tol * 100:.1f}%)")

    # y+
    cols, rows = read_dat(case / "postProcessing" / "yPlus" / "0" / "yPlus.dat")
    if rows:
        tl = max(float(r[0]) for r in rows)
        yp = {r[1]: (float(r[2]), float(r[3]), float(r[4])) for r in rows if float(r[0]) == tl}
        rec("INFO", "y+ (min/max/평균)", "; ".join(f"{k} {a:.0f}/{b:.0f}/{c:.0f}" for k, (a, b, c) in yp.items())
            + " → 벽함수 권장 30~300")

    # --- 잔차 필드: 어디서 잔차가 큰가 ----------------------------------------
    res_names = [n for n in names if n.startswith("initialResidual:")]
    res_rows = []
    if res_names:
        H, Hd = info["housing"]["H"], info["diffuser_height"]
        cc = cell_centers(internal)
        zc = cc[:, 2]
        regions = {"입구 덕트": zc > H + Hd, "확장부": (zc > H) & (zc <= H + Hd),
                   "하우징": (zc >= 0) & (zc <= H), "출구 덕트": zc < 0}
        for n in res_names:
            r_ = v2n(internal.GetCellData().GetArray(n))
            r_ = np.abs(r_) if r_.ndim == 1 else np.abs(r_).max(axis=1)  # 잔차 필드는 부호가 있음
            thr = np.quantile(r_, 0.99)
            top = r_ >= thr
            imax = int(np.argmax(r_))
            share = {k: top[m].sum() / max(top.sum(), 1) * 100 for k, m in regions.items()}
            res_rows.append((n.split(":", 1)[1], float(r_.max()), cc[imax], float(np.median(r_)), share))
    elif ci.get("write_residual_fields"):
        rec("WARN", "잔차 필드 없음", "write_residual_fields가 켜져 있지만 initialResidual:* 필드가 없음")

    # --- 그림 ---------------------------------------------------------------
    y0 = 0.0071 * W
    sec = cut(internal, (0, y0, 0), (0, 1, 0))
    fig, axs = plt.subplots(1, len(kinds), figsize=(5.5 * len(kinds), 12))
    axs = np.atleast_1d(axs)
    vmax = max(np.linalg.norm(sec[un], axis=1).max() for _, un, _ in kinds)
    for ax, (k, un, _) in zip(axs, kinds):
        pc_ = PolyCollection([P[:, [0, 2]] for P in sec["polys"]], array=np.linalg.norm(sec[un], axis=1),
                             cmap="viridis", edgecolor="none", clim=(0, vmax))
        ax.add_collection(pc_); ax.autoscale_view(); ax.set_aspect("equal")
        for c in info["catalyst_zones"]:
            st = "-" if c["name"] in ci["active_layers"] else ":"
            ax.add_patch(plt.Rectangle((-W / 2, c["z_min"]), W, c["z_max"] - c["z_min"], fill=False, ec="w", ls=st, lw=1))
        fig.colorbar(pc_, ax=ax, label=f"|{un}| [m/s] (superficial)", shrink=0.6)
        ttl = f"iter {t_last:.0f}" if k == "inst" else f"mean, iter {fa.get('time_start', '?')}-{t_last:.0f}"
        ax.set_title(f"|{un}|, section y~0 ({ttl})\nsolid box: active catalyst, dotted: inactive")
        ax.set_xlabel("x [m]"); ax.set_ylabel("z [m]")
    fig.tight_layout(); fig.savefig(rep / "flow_velocity_section.png", dpi=150); plt.close(fig)

    n = len(planes)
    if n:
        fig, axs = plt.subplots(len(kinds), n, figsize=(5.2 * n, 4.6 * len(kinds)), squeeze=False)
        for row, (k, un, _) in enumerate(kinds):
            for ax, (name, (z, d)) in zip(axs[row], planes.items()):
                w = -d[un][:, 2]
                m = np.sum(w * d["area"]) / d["area"].sum()
                pcm = PolyCollection([P[:, [0, 1]] for P in d["polys"]], array=w / m, cmap="coolwarm",
                                     edgecolor="none", clim=(0.5, 1.5))
                ax.add_collection(pcm); ax.autoscale_view(); ax.set_aspect("equal")
                fig.colorbar(pcm, ax=ax, label="w / w_mean")
                ax.set_title(f"{name}, 1/3 depth (z={z:.3f} m), {'instant' if k == 'inst' else 'mean'}\n"
                             f"gamma = {uni[k][name][0]:.3f}")
        fig.tight_layout(); fig.savefig(rep / "flow_catalyst_inlet.png", dpi=150); plt.close(fig)

    if res_names:
        nr = len(res_names)
        fig, axs = plt.subplots(1, nr, figsize=(3.6 * nr, 12), squeeze=False)
        for ax, nm in zip(axs[0], res_names):
            r_ = np.abs(sec[nm]) if sec[nm].ndim == 1 else np.abs(sec[nm]).max(axis=1)
            lr = np.log10(np.maximum(r_, 1e-12))
            hi_ = np.log10(max(float(np.abs(v2n(internal.GetCellData().GetArray(nm))).max()), 1e-12))
            pcm = PolyCollection([P[:, [0, 2]] for P in sec["polys"]], array=lr, cmap="inferno",
                                 edgecolor="none", clim=(hi_ - 4, hi_))
            ax.add_collection(pcm); ax.autoscale_view(); ax.set_aspect("equal")
            for c in info["catalyst_zones"]:
                st = "-" if c["name"] in ci["active_layers"] else ":"
                ax.add_patch(plt.Rectangle((-W / 2, c["z_min"]), W, c["z_max"] - c["z_min"], fill=False, ec="c", ls=st, lw=0.8))
            fig.colorbar(pcm, ax=ax, label=f"log10 {nm}", shrink=0.5)
            ax.set_title(f"{nm.split(':', 1)[1]} residual\n(y~0, iter {t_last:.0f})")
            ax.set_xlabel("x [m]")
        axs[0][0].set_ylabel("z [m]")
        fig.tight_layout(); fig.savefig(rep / "flow_residual_section.png", dpi=130); plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(12, 4.2))
    for k, h in res_hist.items():
        axs[0].semilogy(it_hist, h, label=k)
    axs[0].axhline(s["residual_target"], color="k", ls="--", lw=0.8)
    axs[0].set_xlabel("iteration"); axs[0].set_title("Initial residuals"); axs[0].legend()
    for name, (t, y) in mon.items():
        axs[1].plot(t, y, label=name.replace("전체 ΔP", "total dP").replace("ΔP", "dP"))
    axs[1].set_xlabel("iteration"); axs[1].set_ylabel("Pa"); axs[1].set_title("Pressure drop monitors"); axs[1].legend()
    fig.tight_layout(); fig.savefig(rep / "flow_convergence.png", dpi=130); plt.close(fig)

    # --- 보고서 -------------------------------------------------------------
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️"}
    nf = sum(r[0] == "FAIL" for r in results); nw = sum(r[0] == "WARN" for r in results)
    verdict = "FAIL" if nf else ("WARN - 확인 필요" if nw else "PASS")
    L = [f"# 유동 계산 보고서: {ci['name']} (rev {ci['revision']}, 3-1단계 유동)", "",
         f"**판정: {verdict}**  (FAIL {nf}, WARN {nw})", "",
         f"- 시나리오: {spec['meta']['scenario']}",
         f"- 활성 촉매층: {', '.join(ci['active_layers'])} / 솔버 {s['application']}, 난류 {v(spec['turbulence']['model'])}",
         f"- 물성: N2 @ {ci['T_in_K'] - 273.15:.0f} °C, ρ {rho:.4f} kg/m³, μ {mu:.3e} Pa·s",
         f"- 다공성: ε {eps}, d(축) {pc['d_axial']:.3e} 1/m², f(축) {pc['f_axial']:.2f} 1/m, 가로 ×{float(v(spec['catalyst']['transverse_factor'])):.0e}",
         f"- 하우징 평균 표면속도 {U_s:.2f} m/s → 촉매 채널 물리 속도 {U_s / eps:.2f} m/s", "",
         "## 1. 자동 검사", "", "| 결과 | 항목 | 내용 |", "|---|---|---|"]
    L += [f"| {icon[l]} {l} | {i} | {d} |" for l, i, d in results]
    if layer_rows:
        L += ["", "## 2. 촉매층 압력손실" + (" (평균값)" if has_mean else ""), "",
              "| 층 | CFD [Pa] | 1D 이론 [Pa] | 차이 |", "|---|---|---|---|"]
        L += [f"| {a} | {b:.1f} | {c:.1f} | {d:+.1f}% |" for a, b, c, d in layer_rows]
    if uni_rows:
        L += ["", "## 3. 촉매 입구 유속 분포" + (" (평균값)" if has_mean else ""), "",
              "| 층 | 균일도 γ | 표면속도 평균 | 최대 | 최소 | 물리 속도 평균 |", "|---|---|---|---|---|---|"]
        L += [f"| {a} | {b:.3f} | {c:.2f} | {d:.2f} | {e:.2f} | {f:.2f} |" for a, b, c, d, e, f in uni_rows]
    L += ["", "## 4. 그림", "", "![section](flow_velocity_section.png)", "",
          "![inlet](flow_catalyst_inlet.png)", "", "![conv](flow_convergence.png)", ""]
    if res_names:
        L += ["![residual](flow_residual_section.png)", ""]
    L += [
          "## 5. 검증 메모", "",
          "- 3-1단계는 반응이 없어 NOx 전환율(측정 88.8~99.7 %) 비교는 3-2단계에서 수행.",
          "- 촉매층 압력손실이 1D 이론값과 가까우면 다공성 계수 변환(d = 1/k, f = 2cF/√k)이 의도대로 적용된 것.",
          "", "## 6. 사용자 확인 사항", "",
          "- [ ] 질량유량이 실제 엔진 조건(1.6 MW, LNG 50 % 부하)과 맞는가",
          "- [ ] 촉매 입구 유속 분포가 상식적인가",
          "- [ ] WARN 항목을 이대로 진행해도 되는가"]
    if cmp_rows:
        L += ["", f"## 7. 순간값 vs 평균값 ({label['inst']} / {label['mean']})", "",
              "| 항목 | 순간값 | 평균값 | 차이 (순간-평균) |", "|---|---|---|---|"]
        L += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in cmp_rows]
        L += ["", "- 압력손실은 모니터와 같은 평면(촉매층 위/아래 0.4 h)의 면적 평균, 전체 ΔP는 입구/출구 패치 면적 평균."]
    if res_rows:
        L += ["", f"## 8. 셀별 잔차 분포 (iter {t_last:.0f}, solverInfo initialResidual)", "",
              "상위 1 % 잔차 셀이 어느 영역에 있는지 (영역별 비율, 합 100 %)", "",
              "| 필드 | 최대 | 최대 위치 (x, y, z) [m] | 중앙값 | " + " | ".join(res_rows[0][4]) + " |",
              "|---|---|---|---|" + "---|" * len(res_rows[0][4])]
        L += [f"| {n} | {mx:.2e} | ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f}) | {md:.1e} | "
              + " | ".join(f"{x:.0f}%" for x in sh.values()) + " |" for n, mx, c, md, sh in res_rows]
    (rep / "flow_report.md").write_text("\n".join(L), encoding="utf-8")

    for l, i, d in results:
        print(f"[{l}] {i} {('- ' + d) if d else ''}")
    print(f"\n판정: {verdict}\n보고서: {rep / 'flow_report.md'}")
    sys.exit(1 if nf else 0)


if __name__ == "__main__":
    main()
