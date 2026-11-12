#!/usr/bin/env python3
"""
1D 플러그 흐름 반응 모델 (scrSteadyFoam과 같은 반응식, 두 모델 지원)
  chatterjee_coverage : Chatterjee 원문 형태 (피복률 θ, 정상 흡착점 수지, 촉매 부피당 속도 × 고체 분율)
  ace_pseudo_gas      : CFD-ACE+ 서브루틴 형태 (NH3*를 이동 가상 화학종으로)

  - 3D 결과 점검용 기준값 (단열 또는 등온, 균일 유속)
  - 마이크로리액터 재현(verify_kinetics_1d.py)에 사용
"""
import math

RU = 8.314   # 저자 서브루틴과 같은 값


def v(n):
    return n["value"] if isinstance(n, dict) and "value" in n else n


def kinetics(spec):
    k = spec["catalyst"]["kinetics"]
    eps = 0.687
    return dict(model=v(k["model"]), fcat=float(v(k.get("solid_fraction", 1 - eps))),A1=k["R1_ads"]["A"], A2=k["R1_des"]["A"], E2=k["R1_des"]["E_kJmol"] * 1e3, g=k["R1_des"]["gamma"],
                A3=k["R2_oxi"]["A"], E3=k["R2_oxi"]["E_kJmol"] * 1e3, b=k["R2_oxi"]["beta"],
                A4=k["R3_scr"]["A"], E4=k["R3_scr"]["E_kJmol"] * 1e3, K=k["R3_scr"]["K_LH"],
                dH3=float(v(k["dH2_kJmol"])) * 1e3, dH4=float(v(k["dH3_kJmol"])) * 1e3)


def rates(kp, T, x):
    s = min(max(x["S"], 0.0), 1 - 1e-12)
    f = (max(x["O2"], 0.0) / 0.02) ** kp["b"]
    r1 = kp["A1"] * max(x["NH3"], 0.0) * (1 - s)
    r2 = kp["A2"] * math.exp(-kp["E2"] / (RU * T) * (1 - kp["g"] * s)) * s
    r3 = kp["A3"] * math.exp(-kp["E3"] / (RU * T)) * f * s
    r4 = kp["A4"] * math.exp(-kp["E4"] / (RU * T)) * max(x["NO"], 0.0) * s / (1 + kp["K"] * s / (1 - s)) * f
    return r1, r2, r3, r4


def chatterjee_step(kp, T, x, p):
    """정상 흡착점 수지로 θ를 구하고 (r_ox, r_NO, θ) 반환 [mol/m3_cat/s]"""
    C = p / (RU * T)
    cNH3, cNO = max(x["NH3"], 0.0) * C, max(x["NO"], 0.0) * C
    f = (max(x["O2"], 0.0) / 0.02) ** kp["b"]
    kox = kp["A3"] * math.exp(-kp["E3"] / (RU * T)) * f
    kno = kp["A4"] * math.exp(-kp["E4"] / (RU * T)) * cNO * f

    def g(th):
        return (kp["A1"] * cNH3 * (1 - th) - kp["A2"] * math.exp(-kp["E2"] * (1 - kp["g"] * th) / (RU * T)) * th
                - kox * th - kno * th / (1 + kp["K"] * th / (1 - th)))
    lo, hi = 0.0, 1 - 1e-12
    for _ in range(80):
        m = 0.5 * (lo + hi)
        lo, hi = (m, hi) if g(m) > 0 else (lo, m)
    th = 0.5 * (lo + hi)
    return kox * th, kno * th / (1 + kp["K"] * th / (1 - th)), th


def integrate(kp, sV, U, T0, x0, lengths, cp_molar=None, n_per_m=250000, p=101325.0):
    """lengths: 촉매층 길이 목록 (층 사이 빈 공간은 반응 없음 → 생략).
    cp_molar=None이면 등온, 값이 있으면 단열 (반응열로 온도 상승). 반환: 층 끝마다 (전환율, x, T)"""
    if kp["model"] == "chatterjee_coverage":
        return integrate_chatterjee(kp, U, T0, x0, lengths, cp_molar, p=p)
    x = dict(x0); x.setdefault("S", 0.0)
    T = T0
    out = []
    for L in lengths:
        n = max(int(L * n_per_m), 20000)   # 흡착·탈착이 빨라(stiff) 작은 간격 필요
        dz = L / n
        for _ in range(n):
            F = U * p / (RU * T)                     # 몰 유속 [mol/m2/s] (표면속도 기준)
            r1, r2, r3, r4 = rates(kp, T, x)
            k = sV * dz / F
            x["NH3"] += k * (-r1 + r2)
            x["S"] += k * (r1 - r2 - 4 * r3 - 4 * r4)
            x["NO"] += k * (-4 * r4)
            x["O2"] += k * (-3 * r3 - r4)
            if cp_molar:
                T += k * 4 * (kp["dH3"] * r3 + kp["dH4"] * r4) / cp_molar
        out.append({"eta": 1 - x["NO"] / x0["NO"], "XNH3_ppm": x["NH3"] * 1e6, "XNH3s_ppm": x["S"] * 1e6, "T": T})
    return out


def integrate_chatterjee(kp, U, T0, x0, lengths, cp_molar=None, n_per_m=4000, p=101325.0):
    x = dict(x0)
    T = T0
    out = []
    th = 0.0
    for L in lengths:
        n = max(int(L * n_per_m), 2000)
        dz = L / n
        for _ in range(n):
            F = U * p / (RU * T)
            rox, rno, th = chatterjee_step(kp, T, x, p)
            k = kp["fcat"] * dz / F
            x["NO"] = max(x["NO"] - k * rno, 0.0)
            x["NH3"] = max(x["NH3"] - k * (rox + rno), 0.0)
            x["O2"] -= k * (0.75 * rox + 0.25 * rno)
            if cp_molar:
                T += k * (kp["dH3"] * rox + kp["dH4"] * rno) / cp_molar
        out.append({"eta": 1 - x["NO"] / x0["NO"], "XNH3_ppm": x["NH3"] * 1e6, "XNH3s_ppm": 0.0,
                    "theta": th, "T": T})
    return out


def run(spec, U, T0, lengths):
    """3D 비교용: 반응 사양의 입구 조성으로 단열 적분"""
    kp = kinetics(spec)
    gas = spec["gas"]
    x0 = {k: float(v(x)) for k, x in gas["inlet_mole_fraction"].items()}
    cpm = float(v(gas["cp"])) * float(v(gas["M_mix_gmol"])) * 1e-3
    return integrate(kp, float(v(spec["catalyst"]["surface_to_volume"])), U, T0, x0, lengths, cp_molar=cpm)
