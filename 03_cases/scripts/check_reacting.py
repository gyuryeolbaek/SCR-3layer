#!/usr/bin/env python3
"""
3-2단계(열·반응) 결과 검증 + 그림 + 보고서

판정:
  FAIL  솔버 미실행/발산, 에너지 수지 오차 > 2 %, 음수 농도
  WARN  수렴 조건 미달(최대 반복 도달), 전환율이 측정 범위 밖, 1D 기준과 5 %p 이상 차이
  INFO  층별 전환율·온도, NH3 슬립, 벽 열손실
출력: <case>/report/reacting_report.md, reacting_*.png
사용법: python3 scripts/check_reacting.py --case react_2layer [--spec reacting_spec.yaml]
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

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import plugflow_1d  # noqa: E402
import verify_kinetics_1d  # noqa: E402

vtk.vtkObject.GlobalWarningDisplayOff()
results = []


def rec(level, item, detail=""):
    results.append((level, item, detail))


def v(n):
    return n["value"] if isinstance(n, dict) and "value" in n else n


def read_case(case):
    r = vtk.vtkOpenFOAMReader()
    r.SetFileName(str(case / "case.foam"))
    r.CreateCellToPointOff()
    r.UpdateInformation()
    tv = r.GetTimeValues()
    t_last = tv.GetValue(tv.GetNumberOfTuples() - 1) if tv and tv.GetNumberOfTuples() else 0
    r.UpdateTimeStep(t_last)
    mb = r.GetOutput()
    for i in range(mb.GetNumberOfBlocks()):
        if mb.GetMetaData(i).Get(vtk.vtkCompositeDataSet.NAME()) == "internalMesh":
            return mb.GetBlock(i), t_last
    return None, t_last


def cut(grid, origin, normal, fields):
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
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    area = np.array([abs(np.dot(sum(np.cross(P[k], P[(k + 1) % len(P)]) for k in range(len(P))), n)) / 2
                     for P in polys])
    out = {"polys": polys, "area": area}
    for f in fields:
        arr = pd.GetCellData().GetArray(f)
        out[f] = v2n(arr) if arr is not None else None
    return out


def area_avg(d, f):
    return float(np.sum(d[f] * d["area"]) / d["area"].sum())


def probe_line(grid, p0, p1, n, field):
    pts = vtk.vtkPoints()
    for s in np.linspace(0, 1, n):
        pts.InsertNextPoint(*(np.array(p0) * (1 - s) + np.array(p1) * s))
    poly = vtk.vtkPolyData(); poly.SetPoints(pts)
    pr = vtk.vtkProbeFilter(); pr.SetInputData(poly); pr.SetSourceData(grid); pr.Update()
    arr = pr.GetOutput().GetPointData().GetArray(field)
    return np.linspace(0, 1, n), (v2n(arr) if arr is not None else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--spec", default="reacting_spec.yaml")
    args = ap.parse_args()
    case = pathlib.Path(args.case)
    spec = yaml.safe_load(pathlib.Path(args.spec).read_text(encoding="utf-8"))
    ci = json.loads((case / "case_info.json").read_text(encoding="utf-8"))
    geo = json.loads(pathlib.Path("../01_geometry/out/geometry_out.json").read_text(encoding="utf-8"))
    rep = case / "report"; rep.mkdir(exist_ok=True)
    s = spec["solver"]
    W, D = geo["housing"]["W"], geo["housing"]["D"]
    Xin = ci["X_in"]
    Tin = ci["T_in_K"]
    kin = spec["catalyst"]["kinetics"]
    cp = float(v(spec["gas"]["cp"]))
    rng = spec["validation"]["measured_NOx_conversion_range"]

    # --- 로그 / 모니터 --------------------------------------------------------
    log = case / f"log.{s['application']}"
    txt = log.read_text(errors="ignore") if log.exists() else ""
    if not txt:
        rec("FAIL", "솔버 로그 없음", str(log))
    if re.search(r"\bnan\b", txt, re.I) or re.search(r"Foam::sigFpe::sigHandler|Floating point exception \(core dumped\)", txt):
        rec("FAIL", "발산 (NaN)", "log 확인")
    conv = "scrSteadyFoam: converged" in txt
    mon_f = case / "postProcessing" / "scr" / "monitor.dat"
    rows = [l.split("\t") for l in mon_f.read_text().splitlines() if l and not l.startswith("#")] if mon_f.exists() else []
    if not rows:
        rec("FAIL", "모니터 없음", str(mon_f))
        mon = None
    else:
        mon = np.array([[float(x) for x in r] for r in rows])
        it, eta, xno, xnh3, tout, qw, xs = mon[-1, :7]
        res = mon[-1, 7:]
        win = s.get("monitor_window", 200)
        if conv:   # 솔버 수렴 조건으로 일찍 끝난 경우 판정 구간을 반복 수의 1/3로
            win = min(win, max(int(mon[-1, 0] / 3), 10))
        sel = mon[:, 0] > mon[-1, 0] - win
        d_eta = (mon[sel, 1].max() - mon[sel, 1].min()) * 100
        d_T = mon[sel, 4].max() - mon[sel, 4].min()
        stable = (conv or mon[-1, 0] >= 1.5 * win) and d_eta < 0.05 and d_T < 0.05
        rec("PASS" if stable else "WARN", "모니터 안정성",
            f"마지막 {win}회 전환율 변동 {d_eta:.4f} %p, 출구 온도 변동 {d_T:.4f} K (기준 < 0.05)")
        rec("PASS" if conv else "INFO", "솔버 종료 조건",
            (f"{it:.0f}회에서 수렴 조건 만족" if conv else f"{it:.0f}회, 수렴 조건 미달")
            + f" (최종 잔차 최대 {res.max():.1e})")

    # --- 필드 ---------------------------------------------------------------
    grid, t_last = read_case(case)
    if t_last == 0:
        print("[FAIL] 결과 시간 폴더 없음 (솔버가 결과를 쓰기 전에 종료됨) → log 확인")
        sys.exit(1)
    flds = ["T", "XNO_ppm", "XNH3_ppm", "XNH3s_ppm", "theta", "NO", "NH3", "NH3s"]
    for f in ("NO", "NH3", "NH3s"):
        arr = grid.GetCellData().GetArray(f)
        if arr is not None and v2n(arr).min() < -1e-9:
            rec("FAIL", f"음수 농도 {f}", f"최소 {v2n(arr).min():.2e}")

    # 질량유량: 유동 케이스의 입구 유량 (유동 단계에서 질량 보존 확인됨)
    mdot = ci["mass_flow_kg_s"]
    Qv = mdot / ci["rho"]
    ndot = mdot / (ci["M_mix"] * 1e-3)
    U_s = Qv / (W * D)

    if mon is not None:
        # 에너지 수지: m cp (Tout - Tin) = Q_rxn + Q_wall
        dNO = (Xin["NO"] - xno * 1e-6) * ndot
        dNH3 = (Xin["NH3"] - (xnh3 + xs) * 1e-6) * ndot   # NH3 + NH3* 합계 소모
        r3_tot, r2_tot = dNO, max(dNH3 - dNO, 0.0)
        Qrxn = float(v(kin["dH3_kJmol"])) * 1e3 * r3_tot + float(v(kin["dH2_kJmol"])) * 1e3 * r2_tot
        lhs = mdot * cp * (tout - Tin)
        err = abs(lhs - (Qrxn + qw)) / max(abs(Qrxn), 1e-9) * 100
        rec("PASS" if err < 2 else "FAIL", "에너지 수지",
            f"ṁcpΔT {lhs:.1f} W vs 반응열 {Qrxn:.1f} W + 벽 {qw:.1f} W (오차 {err:.2f}% of 반응열)")
        nh3_bal = abs(dNH3 - (r2_tot + r3_tot)) / max(dNH3, 1e-12) * 100
        rec("INFO", "벽 열손실", f"{-qw:.1f} W (h {v(spec['wall_thermal']['h'])} W/m²K, 외기 {v(spec['wall_thermal']['T_ambient_K'])} K)")
        rec("PASS" if rng[0] <= eta <= rng[1] else "WARN", "출구 NO 전환율 (유량 가중)",
            f"{eta * 100:.2f} % (측정 {rng[0] * 100:.1f}~{rng[1] * 100:.1f} %)")
        if v(kin["model"]) == "ace_pseudo_gas":
            rec("INFO", "출구 NH3 슬립", f"NH3 {xnh3:.3f} ppm + NH3* {xs:.3f} ppm (모델상 흡착 NH3도 유동과 함께 이동), "
                f"출구 온도 {tout - 273.15:.2f} °C")
        else:
            rec("INFO", "출구 NH3 슬립", f"{xnh3:.3f} ppm, 출구 온도 {tout - 273.15:.2f} °C")

    # 반응식 해석 검증 (마이크로리액터 1D 재현)
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        old = sys.argv; sys.argv = ["x", args.spec]
        mr_rows, mr_worst, mr_ok = verify_kinetics_1d.main()
        sys.argv = old
    kmodel = v(kin["model"])
    if kmodel == "ace_pseudo_gas":
        rec("PASS" if mr_ok else "FAIL", "반응식 검증: 마이크로리액터 재현 (1D)",
            f"CFD-ACE+ 결과 대비 최대 {mr_worst:.2f} %p (허용 {spec['validation']['microreactor']['tolerance_pct_point']} %p)")
    else:
        rec("INFO", "마이크로리액터 비교 (1D, Chatterjee 원문 모델 vs CFD-ACE+ 모델)",
            f"최대 차이 {mr_worst:.2f} %p - 다른 모델이므로 참고용 (4절 표)")

    # 층별 (논문 field-average: 층 끝 단면 면적 평균)
    h = 0.05
    layer_rows = []
    for c in geo["catalyst_zones"]:
        if c["name"] not in ci["active_layers"]:
            continue
        d = cut(grid, (0, 0, c["z_min"] - 0.4 * h / 4), (0, 0, 1), flds)
        xno_a = area_avg(d, "XNO_ppm"); t_a = area_avg(d, "T")
        nh3_a = area_avg(d, "XNH3_ppm") + area_avg(d, "XNH3s_ppm")
        eta_c = 1 - xno_a * 1e-6 / Xin["NO"]
        layer_rows.append((c["name"], eta_c, t_a, nh3_a, d["T"].min(), d["T"].max()))
        rec("INFO", f"{c['name']} 끝 단면 평균", f"η_NO {eta_c * 100:.2f} %, T {t_a - 273.15:.2f} °C "
            f"(최소 {d['T'].min() - 273.15:.1f}, 최대 {d['T'].max() - 273.15:.1f}), NH3+NH3* {nh3_a:.2f} ppm")

    # 1D 기준 (단열, 균일 유속)
    layers = [c["z_max"] - c["z_min"] for c in geo["catalyst_zones"] if c["name"] in ci["active_layers"]]
    ref = plugflow_1d.run(spec, U_s, Tin, layers)
    if mon is not None:
        diff = (eta - ref[-1]["eta"]) * 100
        rec("PASS" if abs(diff) < 5 else "WARN", "1D 기준 대비 (단열·균일 유속)",
            f"3D {eta * 100:.2f} % vs 1D {ref[-1]['eta'] * 100:.2f} % (차이 {diff:+.2f} %p) "
            "- 차이 원인: 유속 불균일(체류시간 분포)과 벽 열손실")

    # 축 방향 단면 평균
    zs = np.arange(0.025, geo["housing"]["H"], 0.05)
    prof = []
    for z in zs:
        d = cut(grid, (0, 0, z), (0, 0, 1), ["XNO_ppm", "T"])
        if d is not None:
            prof.append((z, area_avg(d, "XNO_ppm"), area_avg(d, "T")))
    prof = np.array(prof)

    # --- 그림 ---------------------------------------------------------------
    y0 = 0.0071 * W
    third = ("theta", "NH3 coverage θ [-]") if v(kin["model"]) == "chatterjee_coverage" else ("XNH3s_ppm", "X_NH3* [ppm]")
    sec = cut(grid, (0, y0, 0), (0, 1, 0), ["T", "XNO_ppm", third[0]])
    fig, axs = plt.subplots(1, 3, figsize=(13, 9))
    for ax, f, lab, cm in zip(axs, ["T", "XNO_ppm", third[0]],
                              ["T [°C]", "X_NO [ppm]", third[1]], ["inferno", "viridis", "cividis"]):
        val = sec[f] - 273.15 if f == "T" else sec[f]
        pc = PolyCollection([P[:, [0, 2]] for P in sec["polys"]], array=val, cmap=cm, edgecolor="none")
        ax.add_collection(pc); ax.set_aspect("equal")
        ax.set_xlim(-W / 2 - 0.05, W / 2 + 0.05); ax.set_ylim(-0.3, geo["housing"]["H"] + 0.6)
        fig.colorbar(pc, ax=ax, label=lab, shrink=0.7)
        ax.set_title(lab.split(" [")[0]); ax.set_xlabel("x [m]")
    axs[0].set_ylabel("z [m]")
    fig.tight_layout(); fig.savefig(rep / "reacting_sections.png", dpi=140); plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))
    axs[0].plot(prof[:, 0], (1 - prof[:, 1] * 1e-6 / Xin["NO"]) * 100, "b-", label="3D area average")
    zc = []
    for c, rr in zip([c for c in geo["catalyst_zones"] if c["name"] in ci["active_layers"]], ref):
        axs[0].plot(c["z_min"], rr["eta"] * 100, "ro", label="1D adiabatic" if not zc else None)
        zc.append(c)
    axs[1].plot(prof[:, 0], prof[:, 2] - 273.15, "r-")
    for ax in axs:
        for c in geo["catalyst_zones"]:
            if c["name"] in ci["active_layers"]:
                ax.axvspan(c["z_min"], c["z_max"], color="0.85")
        ax.set_xlabel("z [m] (flow: right → left)"); ax.invert_xaxis()
    axs[0].set_ylabel("NO conversion [%]"); axs[0].legend()
    axs[1].set_ylabel("T [°C] (area average)")
    fig.tight_layout(); fig.savefig(rep / "reacting_axial.png", dpi=140); plt.close(fig)

    # 반경 방향 온도 (층 중간, 측면/대각선) - 논문 Figure 10 형식
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.2))
    for c in geo["catalyst_zones"]:
        if c["name"] not in ci["active_layers"]:
            continue
        zm = 0.5 * (c["z_min"] + c["z_max"]) + 0.003
        e = 0.499
        s1, t1 = probe_line(grid, (-e * W, y0, zm), (e * W, y0, zm), 201, "T")
        s2, t2 = probe_line(grid, (-e * W, -e * D, zm), (e * W, e * D, zm), 201, "T")
        if t1 is not None:
            axs[0].plot(s1, t1 - 273.15, label=c["name"])
        if t2 is not None:
            axs[1].plot(s2, t2 - 273.15, label=c["name"])
    axs[0].set_title("Lateral line (mid-layer)"); axs[1].set_title("Diagonal line (mid-layer)")
    for ax in axs:
        ax.set_xlabel("L_norm [-]"); ax.set_ylabel("T [°C]"); ax.legend()
    fig.tight_layout(); fig.savefig(rep / "reacting_radial_T.png", dpi=140); plt.close(fig)

    if mon is not None:
        fig, axs = plt.subplots(1, 2, figsize=(12, 4))
        axs[0].plot(mon[:, 0], mon[:, 1] * 100); axs[0].set_ylabel("eta_NO outlet [%]")
        for j, lab in zip(range(7, mon.shape[1]), ["T", "NO", "NH3", "NH3*", "O2", "H2O"]):
            if lab == "NH3*" and np.all(mon[:, j] == 0):
                continue
            axs[1].semilogy(mon[:, 0], np.maximum(mon[:, j], 1e-16), label=lab)
        axs[1].axhline(s["residual"], color="k", ls="--", lw=0.8); axs[1].legend(); axs[1].set_title("Initial residuals")
        for ax in axs:
            ax.set_xlabel("iteration")
        fig.tight_layout(); fig.savefig(rep / "reacting_convergence.png", dpi=130); plt.close(fig)

    # --- 보고서 -------------------------------------------------------------
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️"}
    nf = sum(r[0] == "FAIL" for r in results); nw = sum(r[0] == "WARN" for r in results)
    verdict = "FAIL" if nf else ("WARN - 확인 필요" if nw else "PASS")
    flags = []

    def collect(node, path=""):
        if isinstance(node, dict):
            if node.get("status") in ("assumed", "estimated"):
                flags.append((path, node.get("value"), node["status"], node.get("note", "")))
            for k, x in node.items():
                if k not in ("value", "status", "note"):
                    collect(x, f"{path}.{k}" if path else str(k))
    collect(spec)

    L = [f"# 열·반응 계산 보고서: {ci['name']} (rev {ci['revision']}, 3-2단계)", "",
         f"**판정: {verdict}**  (FAIL {nf}, WARN {nw})", "",
         f"- 시나리오: {spec['meta']['scenario']}",
         f"- 유동: {ci['source_flow_case']} (iter {ci['flow_time']}) 고정, 반응 촉매층: {', '.join(ci['active_layers'])}",
         f"- 반응 모델: {v(kin['model'])} (A_SCR {kin['R3_scr']['A']:.3g}, E_ox {kin['R2_oxi']['E_kJmol']} kJ/mol)",
         f"- 입구: {Tin - 273.15:.0f} °C, NO {Xin['NO'] * 1e6:.0f} ppm, NH3 {Xin['NH3'] * 1e6:.0f} ppm, "
         f"O2 {Xin['O2'] * 100:.1f} %, H2O {Xin['H2O'] * 100:.1f} %, 질량유량 {mdot * 3600:.0f} kg/h",
         "", "## 1. 자동 검사", "", "| 결과 | 항목 | 내용 |", "|---|---|---|"]
    L += [f"| {icon[l]} {l} | {i} | {d} |" for l, i, d in results]
    L += ["", "## 2. 촉매층별 결과 (층 끝 단면 면적 평균, 논문 field-average 방식)", "",
          "| 층 | η_NO [%] | T 평균 [°C] | T 최소~최대 [°C] | NH3+NH3* [ppm] | 1D 단열 η [%] | 1D T [°C] |",
          "|---|---|---|---|---|---|---|"]
    L += [f"| {a} | {b * 100:.2f} | {c - 273.15:.2f} | {e - 273.15:.1f}~{f - 273.15:.1f} | {d:.2f} | "
          f"{rr['eta'] * 100:.2f} | {rr['T'] - 273.15:.2f} |" for (a, b, c, d, e, f), rr in zip(layer_rows, ref)]
    L += ["", "## 3. 그림", "", "![sections](reacting_sections.png)", "", "![axial](reacting_axial.png)", "",
          "![radial](reacting_radial_T.png)", "", "![conv](reacting_convergence.png)", "",
          f"## 4. 마이크로리액터 비교 (1D, 등온, 모델: {v(kin['model'])})", "",
          "| T [°C] | U [m/s] | CFD-ACE+ [%] | 1D [%] | 차이 [%p] |", "|---|---|---|---|---|",
          *[f"| {r[0]:.0f} | {r[1]:.3f} | {r[2]:.1f} | {r[3]:.1f} | {r[4]:+.2f} |" for r in mr_rows],
          "", "## 5. 확인 필요한 가정·추정값", "", "| 항목 | 값 | 상태 | 비고 |", "|---|---|---|---|"]
    L += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in flags]
    L += ["", "## 6. 검증 메모", "",
          "- 에너지 수지는 솔버 구현 점검용: 출구 엔탈피 증가 = 반응열(출구 농도 변화로 역산) + 벽 열손실.",
          "- 1D 기준은 같은 반응식을 단열·균일 유속으로 적분한 값. 3D와 수 %p 이내면 구현이 일관된 것.",
          "- 측정 전환율(88.8~99.7 %)은 실제 엔진 3단 촉매 기준이므로 2단 결과와의 비교는 참고용."]
    (rep / "reacting_report.md").write_text("\n".join(L), encoding="utf-8")

    for l, i, d in results:
        print(f"[{l}] {i} {('- ' + d) if d else ''}")
    print(f"\n판정: {verdict}\n보고서: {rep / 'reacting_report.md'}")
    sys.exit(1 if nf else 0)


if __name__ == "__main__":
    main()
