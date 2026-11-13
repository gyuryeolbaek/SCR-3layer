---
name: react
description: SCR 열·화학반응 계산(3-2단계)을 만들고 실행하고 검증하는 작업 (03_cases의 reacting_spec.yaml, Allreact, solver/scrSteadyFoam). 사용자가 반응, 열, 온도, NOx/NO 전환율, NH3 슬립, 흡착, NH3*, 반응열, 벽 열손실, 촉매 온도, 반응 계수, scrSteadyFoam, 2단/3단 반응 계산을 언급하면 반드시 이 skill을 사용할 것. 유동 계산 자체는 solve skill을 따른다.
---

# 열·반응 단계 (03_cases, 3-2단계)

목표: 확정된 유동 케이스 + `reacting_spec.yaml` → `./Allreact` → `<케이스>/report/reacting_report.md` → **사용자 검토 대기**.

## 구조
- `solver/scrSteadyFoam/` (C++): 고정 유동장(U, phi, nut) 위에서 에너지 + 화학종(NO, NH3, O2, H2O)을 정상상태로 푼다.
  - 반응 모델은 `reacting_spec.yaml`의 `catalyst.kinetics.model`로 선택:
    - **chatterjee_coverage (기본, 사용자 결정)**: Chatterjee et al. SAE 2005-01-0965 원문 형태. NH3 피복률 θ(0~1)는 촉매에 머물고, 정상상태 흡착점 수지(r_ads − r_des − r_ox − r_NO = 0)를 셀마다 이분법으로 풂 (Ω 상쇄). 속도는 촉매 부피당 [mol/m³_cat/s], 기상 농도 C [mol/m³], 반응기 부피당 = 고체 분율(1−ε) × 속도. 기상 NH3 순소모는 r_ox + r_NO로 계산 (흡착·탈착 항을 따로 두면 stiff해서 수렴이 극도로 느림 → 바꾸지 말 것).
    - ace_pseudo_gas: Baek et al. 논문의 CFD-ACE+ 서브루틴 형태 (NH3*를 이동 가상 화학종으로, 같은 숫자를 몰분율·표면적×sV 기준으로). 논문 결과 재현용 (E_ox 141.6 kJ/mol과 함께).
  - 다공성 유효 열전도도 식 (5), λS = 0.9558 − 2.09e-4·T[K], 반응열(NH3/NO 1 mol당), 대류 벽(mixed BC, 솔버가 매 반복 갱신).
  - 컴파일: `./solver/Allwmake` (Allreact가 솔버가 없으면 자동 실행). OpenFOAM 개발 패키지(wmake, 헤더) 필요.
- `scripts/make_reacting_case.py`: 사양 → 케이스 (유동 케이스 최종 시간의 U, phi, nut를 0/에 복사해 고정)
- `scripts/check_reacting.py`: 판정·그림·보고서
- `scripts/plugflow_1d.py`: 솔버와 같은 반응식의 1D 모델 (두 모델) / `scripts/verify_kinetics_1d.py`: 저자 CFD-ACE+ 마이크로리액터 결과와 비교 (수 초)

## 절대 규칙
1. **유동 케이스는 확정된 것만** `source_flow_case`로 쓴다 (PROGRESS.md 확인).
2. **반응 계수, 다공성 물성, 입구 조성·온도, 벽 조건은 사용자 승인 없이 바꾸지 않는다.** 측정 전환율에 맞추려는 계수 조정은 사용자가 명시적으로 요청할 때만.
3. 반응식이나 솔버를 수정하면 **3D 보고서의 에너지 수지(≤ 2 %)와 1D 기준 비교(≤ 5 %p)가 PASS인지** 확인하고 PROGRESS.md에 기록. `plugflow_1d.py`와 솔버 C++의 반응식은 항상 같게 유지. ace_pseudo_gas를 쓸 때는 `verify_kinetics_1d.py` PASS도 확인.
4. 결과가 있는 케이스는 덮어쓰지 않는다 (`meta.name` 변경, `--force`는 사용자 요청 시만).
5. YAML 화학종 키는 따옴표 필수 (`"NO"` — 따옴표 없으면 false로 읽힘).

## 판정 기준 (check_reacting.py)
- FAIL: 발산, 음수 농도, 결과 없음, 에너지 수지 오차 > 2 % (ṁcpΔT = 반응열 + 벽 열손실), (ace 모델) 마이크로리액터 재현 실패
- WARN: 모니터(전환율·출구 온도) 미안정, 출구 전환율이 측정 범위(88.8~99.7 %) 밖, 1D 기준과 5 %p 이상 차이
- 마이크로리액터 비교(4절)는 실험값(Koo et al. 2016 Fig. 8 fresh, `validation.microreactor.experiment`)과 CFD-ACE+ 값을 함께 보여줌. chatterjee 모델은 실험 대비 차이만 보고 (판정 없음), ace 모델은 CFD-ACE+ 대비로 판정

## 확인 대기 중인 항목 (보고서 5절에 자동 표시)
- Chatterjee 원문 Table 6은 계수의 "대략적 크기"(정확한 회귀값 아님), E_ox는 원문 118 kJ/mol 사용 (Baek 논문은 141.6)
- 촉매 부피 기준 → 반응기 부피 기준 환산 계수 = 고체 분율 1−ε (0.313)
- 원문은 기공 확산 포함 1D+1D 모델용 고유 속도. 다공성 매질 모델에는 기공 확산·물질전달 저항이 없음
- 반응열(표준 생성 엔탈피), cp·λF·Dm(N2 @ 600 K), O2 2 %, H2O 1 %

## 작업 순서
1. PROGRESS.md 확인 → 2. `python3 scripts/verify_kinetics_1d.py` (반응식 점검) → 3. `./Allreact` → 4. 보고서 요약 보고 후 **멈춤** → 5. PROGRESS.md 갱신, 커밋
3단 계산: 3단 유동 케이스(active_layers에 catalyst_3)를 먼저 확정한 뒤 `source_flow_case`와 `meta.name`만 바꿔 실행.
