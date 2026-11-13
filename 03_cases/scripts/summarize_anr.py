#!/usr/bin/env python3
"""
ANR 범위 반응 케이스 비교표 (3-2단계)
각 케이스의 report/reacting_report.md 자동 검사 표를 읽어 출구·층별 결과를 한 표로 모으고,
측정 NOx 전환율 범위(validation.measured_NOx_conversion_range)에 해당하는 ANR 구간을 선형 보간으로 표시한다.

사용법 (03_cases에서): python3 scripts/summarize_anr.py [--out react_3layer_anr_summary.md] case1 case2 ...
  ANR은 각 보고서 머리말의 입구 NO·NH3 ppm으로 계산한다.
"""
import argparse, pathlib, re, sys
import yaml

NUM = r"([-+]?\d+(?:\.\d+)?)"


def parse(case):
    md = (pathlib.Path(case) / "report" / "reacting_report.md").read_text(encoding="utf-8")
    g = lambda pat: re.search(pat, md)
    r = {"case": case}
    m = g(r"\*\*판정: ([^*]+)\*\*")
    r["verdict"] = m.group(1).strip() if m else "?"
    m = g(rf"NO {NUM} ppm, NH3 {NUM} ppm")
    r["NO_in"], r["NH3_in"] = float(m.group(1)), float(m.group(2))
    r["ANR"] = r["NH3_in"] / r["NO_in"]
    r["eta_out"] = float(g(rf"출구 NO 전환율 \(유량 가중\) \| {NUM} %").group(1))
    m = g(rf"출구 NH3 슬립 \| {NUM} ppm, 출구 온도 {NUM} °C")
    r["slip"], r["T_out"] = float(m.group(1)), float(m.group(2))
    r["Q_wall"] = float(g(rf"벽 열손실 \| {NUM} W").group(1))
    r["layers"] = {}
    for m in re.finditer(rf"\| (catalyst_\d) 끝 단면 평균 \| η_NO {NUM} %, T {NUM} °C .*?NH3\+NH3\* {NUM} ppm", md):
        r["layers"][m.group(1)] = (float(m.group(2)), float(m.group(3)), float(m.group(4)))
    m = g(r"\| (?:✅ PASS|⚠️ WARN|ℹ️ INFO) \| 솔버 종료 조건 \| ([^|]+) \|")
    r["term"] = m.group(1).strip() if m else ""
    return r


def anr_at(rows, target):
    """전환율(ANR에 단조 증가)이 target이 되는 ANR. 표 범위 밖이면 양 끝 두 점으로 외삽하고 표시."""
    pts = sorted((x["ANR"], x["eta_out"]) for x in rows)
    for (a0, e0), (a1, e1) in zip(pts, pts[1:]):
        if e0 <= target <= e1 and e1 > e0:
            return a0 + (target - e0) * (a1 - a0) / (e1 - e0), "보간"
    (a0, e0), (a1, e1) = (pts[0], pts[1]) if target < pts[0][1] else (pts[-2], pts[-1])
    if e1 == e0:
        return None, "포화"
    return a0 + (target - e0) * (a1 - a0) / (e1 - e0), "외삽"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--spec", default="reacting_spec.yaml")
    ap.add_argument("--out", default="react_3layer_anr_summary.md")
    a = ap.parse_args()
    lo, hi = yaml.safe_load(open(a.spec, encoding="utf-8"))["validation"]["measured_NOx_conversion_range"]
    lo, hi = lo * 100, hi * 100
    rows = sorted((parse(c) for c in a.cases), key=lambda x: x["ANR"])
    layers = sorted({k for x in rows for k in x["layers"]})

    L = ["# 3단 반응 계산 ANR 비교 (react_3layer)", "",
         "- 유동: flow_3layer (고정), 반응 모델 chatterjee_coverage, 입구 325 °C, NO 680 ppm, 벽 h 10 W/m²K",
         "- 케이스 간 차이는 입구 NH3 (= ANR × 680 ppm) 하나뿐",
         f"- 측정 NOx 전환율 범위 {lo:.1f}~{hi:.1f} % (1.6 MW 엔진 50 % 부하, 시간 변동). 측정 ANR은 모름", "",
         "## 1. 케이스 비교", "",
         "| 케이스 | ANR | NH3 입구 [ppm] | 출구 η_NO [%] | " + " | ".join(f"{l} 끝 η [%]" for l in layers)
         + " | 출구 NH3 슬립 [ppm] | 출구 T [°C] | 벽 열손실 [kW] | 측정 범위 | 판정 |",
         "|---|---|---|---|" + "---|" * len(layers) + "---|---|---|---|---|"]
    for x in rows:
        inr = "✅ 안" if lo <= x["eta_out"] <= hi else ("▲ 위" if x["eta_out"] > hi else "▼ 아래")
        L.append(f"| {x['case']} | {x['ANR']:.2f} | {x['NH3_in']:.0f} | {x['eta_out']:.2f} | "
                 + " | ".join(f"{x['layers'][l][0]:.2f}" if l in x["layers"] else "-" for l in layers)
                 + f" | {x['slip']:.2f} | {x['T_out']:.2f} | {x['Q_wall'] / 1e3:.2f} | {inr} | {x['verdict']} |")

    a_lo, m_lo = anr_at(rows, lo)
    a_hi, m_hi = anr_at(rows, hi)
    fmt = lambda v, m: ("?" if v is None else f"{v:.3f}") + f" ({m})"
    L += ["", "## 2. 측정 전환율 범위에 해당하는 ANR 구간", "",
          "| 측정 전환율 | ANR (계산 케이스 사이 선형 보간) |", "|---|---|",
          f"| 하한 {lo:.1f} % | {fmt(a_lo, m_lo)} |", f"| 상한 {hi:.1f} % | {fmt(a_hi, m_hi)} |", "",
          f"**해당 ANR 구간: 약 {fmt(a_lo, m_lo).split()[0]} ~ {fmt(a_hi, m_hi).split()[0]}**", "",
          "- 외삽: 계산한 ANR 범위 밖이라 가장 가까운 두 케이스로 직선 연장한 값.",
          "- 상한 쪽은 ANR 1.00 → 1.05 사이에서 전환율이 100 %로 포화하므로 선형 보간이 실제 꺾임을 반영하지 못함 "
          "(실제 경계는 보간값보다 1.00 쪽에 가까울 수 있음). 경계를 정확히 보려면 ANR 1.01~1.04 케이스 추가 필요.", "",
          "## 3. 솔버 종료", ""]
    L += [f"- {x['case']}: {x['term']}" for x in rows]
    pathlib.Path(a.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
