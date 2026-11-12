#!/usr/bin/env python3
"""
반응식 점검: 저자의 CFD-ACE+ 마이크로리액터 결과(온도별 NO 전환율)와 1D 플러그 흐름(등온) 비교.
  ace_pseudo_gas      : CFD-ACE+ 모델 재현 → 허용 오차로 PASS/FAIL 판정
  chatterjee_coverage : 다른 모델이므로 비교만 보고 (항상 종료 코드 0)
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
    rows, worst = [], 0.0
    for Tc, U, ref in mr["points"]:
        res = pf.integrate(kp, sV, U, Tc + 273.15, x0, [L])[-1]
        d = res["eta"] * 100 - ref
        worst = max(worst, abs(d))
        rows.append((Tc, U, ref, res["eta"] * 100, d, res["XNH3_ppm"], res["XNH3s_ppm"]))
    print(f"마이크로리액터 비교 [{kp['model']}] (L = {L} m, A_SCR = {kp['A4']:.3g}, E_ox = {kp['E3'] / 1e3:.1f} kJ/mol)")
    print(" T[°C]  U[m/s]  CFD-ACE+[%]  1D[%]   차이[%p]  NH3[ppm]  NH3*[ppm]")
    for r in rows:
        print(f" {r[0]:5.0f}  {r[1]:6.3f}  {r[2]:10.1f}  {r[3]:6.1f}  {r[4]:+7.2f}  {r[5]:8.2f}  {r[6]:9.2f}")
    if kp["model"] == "ace_pseudo_gas":
        ok = worst <= tol
        print(f"\n최대 차이 {worst:.2f} %p (허용 {tol} %p) → {'PASS' if ok else 'FAIL'}")
    else:
        ok = True
        print(f"\n최대 차이 {worst:.2f} %p (Chatterjee 원문 모델: CFD-ACE+ 모델과의 비교, 판정 없음)")
    return rows, worst, ok


if __name__ == "__main__":
    _, _, ok = main()
    sys.exit(0 if ok else 1)
