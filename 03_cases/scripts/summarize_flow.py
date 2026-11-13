#!/usr/bin/env python3
"""
유동 케이스 비교표 (3-1단계)
각 케이스의 report/flow_report.md와 case_info.json을 읽어 입구 조건, 층별 균일도·압력손실·물리 속도,
전체 압력손실을 한 표로 모은다.

사용법 (03_cases에서): python3 scripts/summarize_flow.py [--out flow_3layer_mflow_summary.md] case1 case2 ...
"""
import argparse, json, pathlib, re, sys

NUM = r"([-+]?\d+(?:\.\d+)?)"


def parse(case):
    d = pathlib.Path(case)
    md = (d / "report" / "flow_report.md").read_text(encoding="utf-8")
    ci = json.loads((d / "case_info.json").read_text(encoding="utf-8"))
    r = {"case": case, "U_in": ci["inlet_velocity"], "cond": ci.get("inlet_condition", "velocity")}
    m = re.search(r"\*\*판정: ([^*]+)\*\*\s*\(([^)]*)\)", md)
    r["verdict"] = f"{m.group(1).strip()} ({m.group(2)})" if m else "?"
    m = re.search(rf"\| 질량유량 \| {NUM} kg/s = {NUM} kg/h", md)
    r["mdot"] = float(m.group(2))
    r["dp_tot"] = float(re.search(rf"전체 압력손실 \(입구→출구\)[^|]*\| {NUM} Pa", md).group(1))
    r["layers"] = {}
    for m in re.finditer(rf"\| (catalyst_\d) \| {NUM} \| {NUM} \| {NUM}% \|", md):          # 2절 압력손실
        r["layers"].setdefault(m.group(1), {}).update(dp=float(m.group(2)), dp1d=float(m.group(4)))
    for m in re.finditer(rf"\| (catalyst_\d) \| {NUM} \| {NUM} \| {NUM} \| {NUM} \| {NUM} \|", md):  # 3절 유속
        r["layers"].setdefault(m.group(1), {}).update(gamma=float(m.group(2)), u_phys=float(m.group(6)))
    for m in re.finditer(rf"촉매 내 물리 속도 (catalyst_\d)[^|]*\| 평균 {NUM} m/s, 범위 {NUM}~{NUM} m/s", md):
        r["layers"].setdefault(m.group(1), {}).update(u_min=float(m.group(3)), u_max=float(m.group(4)))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--out", default="flow_3layer_mflow_summary.md")
    a = ap.parse_args()
    rows = sorted((parse(c) for c in a.cases), key=lambda x: x["mdot"])
    layers = sorted({k for x in rows for k in x["layers"]})
    g = lambda x, l, k, f: f"{x['layers'][l][k]:{f}}" if k in x["layers"].get(l, {}) else "-"
    L = ["# 3단 유동 질량유량 비교", "",
         "- 형상·격자·덕트 직경(0.4 m)·다공성·출력 설정 동일, 입구 조건만 다름. 값은 평균 필드(UMean, pMean, iter 2000~3000)",
         "- 질량유량은 계산 결과(입구 patch 면 유량). 촉매 내 물리 속도 = 층 1/3 깊이 표면속도 / ε. 논문 계수 산출 범위 1.75~3.0 m/s", "",
         "## 1. 입구·전체", "",
         "| 케이스 | 입구 조건 | 질량유량 [kg/h] | 입구 속도 [m/s] | 전체 ΔP [Pa] | 판정 |", "|---|---|---|---|---|---|"]
    L += [f"| {x['case']} | {'질량유량' if x['cond'] == 'mass_flow' else '속도 35 m/s'} | {x['mdot']:.0f} | {x['U_in']:.2f} | "
          f"{x['dp_tot']:.1f} | {x['verdict']} |" for x in rows]
    L += ["", "## 2. 층별 균일도 γ (층 1/3 깊이)", "", "| 케이스 | " + " | ".join(layers) + " |", "|---|" + "---|" * len(layers)]
    L += [f"| {x['case']} | " + " | ".join(g(x, l, "gamma", ".3f") for l in layers) + " |" for x in rows]
    L += ["", "## 3. 층별 압력손실 [Pa] (괄호: 1D 이론 대비)", "", "| 케이스 | " + " | ".join(layers) + " | 층 합계 |",
          "|---|" + "---|" * (len(layers) + 1)]
    for x in rows:
        cells = [f"{x['layers'][l]['dp']:.1f} ({x['layers'][l]['dp1d']:+.1f}%)" if "dp" in x["layers"].get(l, {}) else "-"
                 for l in layers]
        L.append(f"| {x['case']} | " + " | ".join(cells) + f" | {sum(v['dp'] for v in x['layers'].values() if 'dp' in v):.1f} |")
    L += ["", "## 4. 촉매 내 물리 속도 [m/s] (평균, 범위 최소~최대)", "", "| 케이스 | " + " | ".join(layers) + " |",
          "|---|" + "---|" * len(layers)]
    for x in rows:
        cells = [f"{g(x, l, 'u_phys', '.2f')} ({g(x, l, 'u_min', '.2f')}~{g(x, l, 'u_max', '.2f')})" for l in layers]
        L.append(f"| {x['case']} | " + " | ".join(cells) + " |")
    pathlib.Path(a.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
