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
                rows[float(parts[0])] = parts  # 재시작 시 같은 시간은 나중 값으로
    return cols, [rows[t] for t in sorted(rows)]


def series(case, fo, file="surfaceFieldValue.dat"):
    cols, rows = read_dat(case / "postProcessing" / fo / "0" / file)
    if not rows:
        return np.array([]), np.array([])
    t = np.array([float(r[0]) for r in rows])
    y = np.array([float(r[1]) for r in rows])
    return t, y


# ---------------------------------------------------------------------------
def read_fields(case):
    r = vtk.vtkOpenFOAMReader()
    r.SetFileName(str(case / "case.foam"))
    r.ReadZonesOn()
    r.CreateCellToPointOff()
    r.UpdateInformation()
    tv = r.GetTimeValues()
    t_last = tv.GetValue(tv.GetNumberOfTuples() - 1) if tv and tv.GetNumberOfTuples() else 0
    r.UpdateTimeStep(t_last)
    mb = r.GetOutput()
    internal = None
    for i in range(mb.GetNumberOfBlocks()):
        if mb.GetMetaData(i).Get(vtk.vtkCompositeDataSet.NAME()) == "internalMesh":
            internal = mb.GetBlock(i)
    return internal, t_last


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
    U = v2n(pd.GetCellData().GetArray("U"))
    p = v2n(pd.GetCellData().GetArray("p"))
    area = np.array([poly_area(P, normal) for P in polys])
    return {"polys": polys, "U": U, "p": p, "area": area}


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
    rec("PASS" if converged else ("WARN" if n_it >= s["max_iterations"] else "INFO"), "반복 수",
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
        rec("PASS" if worst <= s["residual_target"] * 10 else "WARN", "최종 초기 잔차",
            ", ".join(f"{k} {x:.1e}" for k, x in last.items()) + f" (목표 {s['residual_target']:.0e})")

    # --- 질량 보존 ----------------------------------------------------------
    t_in, m_in = series(case, "massFlow_inlet")
    t_out, m_out = series(case, "massFlow_outlet")
    if len(m_in):
        imb = abs(m_in[-1] + m_out[-1]) / abs(m_in[-1]) * 100
        rec("PASS" if imb < 0.5 else "FAIL", "질량 보존 (입구 vs 출구)", f"오차 {imb:.3f}%")
        mdot = abs(m_in[-1]) * rho
        note = " ← 입구 덕트 직경이 가정값" if any(f["item"].startswith("inlet_duct.diameter") and f["status"] != "given"
                                            for f in info.get("flags", [])) else ""
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
    layer_rows = []
    for c in info["catalyst_zones"]:
        if c["name"] not in ci["active_layers"]:
            continue
        t1, a = series(case, f"p_{c['name']}_in")
        t2, b = series(case, f"p_{c['name']}_out")
        if len(a) and len(b):
            n = min(len(a), len(b))
            dp = (a[:n] - b[:n]) * rho
            mon[f"ΔP {c['name']}"] = (t1[:n], dp)
            L = c["z_max"] - c["z_min"]
            dp_1d = L * (mu * pc["d_axial"] * U_s + 0.5 * rho * pc["f_axial"] * U_s ** 2)
            dev = (dp[-1] - dp_1d) / dp_1d * 100
            layer_rows.append((c["name"], dp[-1], dp_1d, dev))
            rec("PASS" if abs(dev) < 15 else "WARN", f"압력손실 {c['name']}",
                f"CFD {dp[-1]:.1f} Pa vs 1D 이론 {dp_1d:.1f} Pa (차이 {dev:+.1f}%, 균일 유동 가정)")
    if "전체 ΔP" in mon:
        rec("INFO", "전체 압력손실 (입구→출구)", f"{mon['전체 ΔP'][1][-1]:.1f} Pa")

    win = s["monitor_window"]
    for name, (t, y) in mon.items():
        sel = t > t[-1] - win
        if t[-1] < win * 1.5:
            rec("WARN", f"수렴 판정 불가: {name}", f"반복 수({t[-1]:.0f})가 판정 구간({win})에 비해 짧음")
            break
        var = (y[sel].max() - y[sel].min()) / max(abs(y[-1]), 1e-12)
        rec("PASS" if var <= s["monitor_tolerance"] else "WARN", f"모니터 안정성: {name}",
            f"마지막 {win}회 변동 {var * 100:.2f}% (기준 ≤ {s['monitor_tolerance'] * 100:.1f}%)")

    # --- 필드: 촉매 입구 균일도, 속도 -----------------------------------------
    internal, t_last = read_fields(case)
    lo, hi = spec["validation"]["paper_catalyst_velocity_range"]
    uni_rows = []
    planes = {}
    for c in info["catalyst_zones"]:
        if c["name"] not in ci["active_layers"]:
            continue
        # 촉매층 길이의 1/3 지점: 다공성 경계면 첫 셀은 셀 중심 속도가 왜곡되므로 피함.
        # 가로 저항이 매우 커서 층 내부에서는 입구의 유속 분포가 그대로 유지됨 (논문 Figure 7)
        z = c["z_max"] - (c["z_max"] - c["z_min"]) / 3
        d = cut(internal, (0, 0, z), (0, 0, 1))
        planes[c["name"]] = (z, d)
        w = -d["U"][:, 2]  # 아래 방향 유동을 양수로
        g, m = uniformity(w, d["area"])
        uphys = w / eps
        uni_rows.append((c["name"], g, m, w.max(), w.min(), m / eps))
        rec("INFO", f"촉매 유속 균일도 {c['name']} (층 1/3 깊이)",
            f"γ = {g:.3f}, 표면속도 평균 {m:.2f} (최대 {w.max():.2f}, 최소 {w.min():.2f}) m/s")
        ok = lo <= m / eps <= hi
        rec("PASS" if ok else "WARN", f"촉매 내 물리 속도 {c['name']}",
            f"평균 {m / eps:.2f} m/s, 범위 {uphys.min():.2f}~{uphys.max():.2f} m/s "
            f"(논문 계수 산출 범위 {lo}~{hi} m/s)" + ("" if ok else " → 범위 밖이면 다공성 계수 외삽"))

    # y+
    cols, rows = read_dat(case / "postProcessing" / "yPlus" / "0" / "yPlus.dat")
    if rows:
        tl = max(float(r[0]) for r in rows)
        yp = {r[1]: (float(r[2]), float(r[3]), float(r[4])) for r in rows if float(r[0]) == tl}
        rec("INFO", "y+ (min/max/평균)", "; ".join(f"{k} {a:.0f}/{b:.0f}/{c:.0f}" for k, (a, b, c) in yp.items())
            + " → 벽함수 권장 30~300")

    # --- 그림 ---------------------------------------------------------------
    y0 = 0.0071 * W
    sec = cut(internal, (0, y0, 0), (0, 1, 0))
    fig, ax = plt.subplots(figsize=(5.5, 12))
    pc_ = PolyCollection([P[:, [0, 2]] for P in sec["polys"]], array=np.linalg.norm(sec["U"], axis=1),
                         cmap="viridis", edgecolor="none")
    ax.add_collection(pc_); ax.autoscale_view(); ax.set_aspect("equal")
    for c in info["catalyst_zones"]:
        st = "-" if c["name"] in ci["active_layers"] else ":"
        ax.add_patch(plt.Rectangle((-W / 2, c["z_min"]), W, c["z_max"] - c["z_min"], fill=False, ec="w", ls=st, lw=1))
    fig.colorbar(pc_, ax=ax, label="|U| [m/s] (superficial)", shrink=0.6)
    ax.set_title(f"Velocity, section y~0 (iter {t_last:.0f})\nsolid box: active catalyst, dotted: inactive")
    ax.set_xlabel("x [m]"); ax.set_ylabel("z [m]")
    fig.tight_layout(); fig.savefig(rep / "flow_velocity_section.png", dpi=150); plt.close(fig)

    n = len(planes)
    if n:
        fig, axs = plt.subplots(1, n, figsize=(5.2 * n, 4.6))
        axs = np.atleast_1d(axs)
        for ax, (name, (z, d)) in zip(axs, planes.items()):
            w = -d["U"][:, 2]
            m = np.sum(w * d["area"]) / d["area"].sum()
            pcm = PolyCollection([P[:, [0, 1]] for P in d["polys"]], array=w / m, cmap="coolwarm",
                                 edgecolor="none", clim=(0.5, 1.5))
            ax.add_collection(pcm); ax.autoscale_view(); ax.set_aspect("equal")
            fig.colorbar(pcm, ax=ax, label="w / w_mean")
            ax.set_title(f"{name}, 1/3 depth (z={z:.3f} m)")
        fig.tight_layout(); fig.savefig(rep / "flow_catalyst_inlet.png", dpi=150); plt.close(fig)

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
        L += ["", "## 2. 촉매층 압력손실", "", "| 층 | CFD [Pa] | 1D 이론 [Pa] | 차이 |", "|---|---|---|---|"]
        L += [f"| {a} | {b:.1f} | {c:.1f} | {d:+.1f}% |" for a, b, c, d in layer_rows]
    if uni_rows:
        L += ["", "## 3. 촉매 입구 유속 분포", "",
              "| 층 | 균일도 γ | 표면속도 평균 | 최대 | 최소 | 물리 속도 평균 |", "|---|---|---|---|---|---|"]
        L += [f"| {a} | {b:.3f} | {c:.2f} | {d:.2f} | {e:.2f} | {f:.2f} |" for a, b, c, d, e, f in uni_rows]
    L += ["", "## 4. 그림", "", "![section](flow_velocity_section.png)", "",
          "![inlet](flow_catalyst_inlet.png)", "", "![conv](flow_convergence.png)", "",
          "## 5. 검증 메모", "",
          "- 3-1단계는 반응이 없어 NOx 전환율(측정 88.8~99.7 %) 비교는 3-2단계에서 수행.",
          "- 촉매층 압력손실이 1D 이론값과 가까우면 다공성 계수 변환(d = 1/k, f = 2cF/√k)이 의도대로 적용된 것.",
          "", "## 6. 사용자 확인 사항", "",
          "- [ ] 질량유량이 실제 엔진 조건(1.6 MW, LNG 50 % 부하)과 맞는가",
          "- [ ] 촉매 입구 유속 분포가 상식적인가",
          "- [ ] WARN 항목을 이대로 진행해도 되는가"]
    (rep / "flow_report.md").write_text("\n".join(L), encoding="utf-8")

    for l, i, d in results:
        print(f"[{l}] {i} {('- ' + d) if d else ''}")
    print(f"\n판정: {verdict}\n보고서: {rep / 'flow_report.md'}")
    sys.exit(1 if nf else 0)


if __name__ == "__main__":
    main()
