#!/usr/bin/env python3
"""
반응식 점검: 마이크로리액터 조건에서 1D 플러그 흐름(등온) 결과를
저자의 CFD-ACE+ 결과, Koo et al. (2016) Fig. 8 fresh 실험값(있으면)과 비교.
  ace_pseudo_gas      : CFD-ACE+ 모델 재현 → CFD-ACE+ 대비 허용 오차로 PASS/FAIL 판정
  chatterjee_coverage : 실험값 대비 차이 보고 (판정 없음, 항상 종료 코드 0)
수 초 소요, OpenFOAM 불필요.

  plug flow에서 전환율은 sV·L/U(=면적속도의 역수)에만 의존하므로 1D로 충분.
사용법: python3 scripts/verify_kinetics_1d.py [reacting_spec.yaml]
종료 코드: 모든 점이 허용 오차(tolerance_pct_point) 안이면 0
"""
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import plugflow_1d as pf  # noqa: E402


def main():
    spec_path = sys.argv[1] if len(sys.argv) > 1 else "reacting_spec.yaml"
    spec = yaml.safe_load(pathlib.Path(spec_path).read_text(encoding="utf-8"))
    mr = spec["validation"]["microreactor"]
    kp = pf.kinetics(spec)
    sV = float(pf.v(spec["catalyst"]["surface_to_volume"]))
    L = float(pf.v(mr["catalyst_length"]))
    x0 = {k: float(x) for k, x in mr["inlet_mole_fraction"].items()}
    tol = float(mr["tolerance_pct_point"])
    exp = {float(t): float(e) for t, e in (mr.get("experiment") or {}).get("points", [])}
    rows, worst, worst_exp = [], 0.0, None
    for Tc, U, ref in mr["points"]:
        res = pf.integrate(kp, sV, U, Tc + 273.15, x0, [L])[-1]
        eta = res["eta"] * 100
        d = eta - ref
        worst = max(worst, abs(d))
        e = exp.get(float(Tc))
        de = None if e is None else eta - e
        if de is not None and (worst_exp is None or abs(de) > abs(worst_exp)):
            worst_exp = de
        # rows: (T, U, CFD-ACE+, 1D, 1D-ACE, NH3, NH3*, 실험, 1D-실험)
        rows.append((Tc, U, ref, eta, d, res["XNH3_ppm"], res["XNH3s_ppm"], e, de))
    f = lambda x, w, fmt: f"{'-':>{w}}" if x is None else format(x, fmt)
    print(f"마이크로리액터 비교 [{kp['model']}] (L = {L} m, A_SCR = {kp['A4']:.3g}, E_ox = {kp['E3'] / 1e3:.1f} kJ/mol)")
    print(" T[°C]  U[m/s]  실험[%]  CFD-ACE+[%]  1D[%]  1D-실험[%p]  1D-ACE[%p]  NH3[ppm]  NH3*[ppm]")
    for r in rows:
        print(f" {r[0]:5.0f}  {r[1]:6.3f}  {f(r[7], 7, '7.1f')}  {r[2]:11.1f}  {r[3]:5.1f}  {f(r[8], 11, '+11.2f')}"
              f"  {r[4]:+10.2f}  {r[5]:8.2f}  {r[6]:9.2f}")
    if exp:
        print(f"실험값: {pf.v(mr['experiment']['source'])}")
    if kp["model"] == "ace_pseudo_gas":
        ok = worst <= tol
        print(f"\nCFD-ACE+ 대비 최대 차이 {worst:.2f} %p (허용 {tol} %p) → {'PASS' if ok else 'FAIL'}")
    else:
        ok = True
        msg = "실험값 없음" if worst_exp is None else f"실험값 대비 최대 차이 {worst_exp:+.2f} %p"
        print(f"\n{msg} (Chatterjee 원문 모델, 판정 없음). 참고: CFD-ACE+ 모델 대비 최대 {worst:.2f} %p")
    return rows, worst, ok, worst_exp


if __name__ == "__main__":
    _, _, ok, _ = main()
    sys.exit(0 if ok else 1)
