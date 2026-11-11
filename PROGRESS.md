# PROGRESS — SCR 3층 촉매 반응기 CFD

사각 하우징(1.0×1.0×2.7 m)에 촉매층 3개가 있는 SCR 반응기의 내부 유동을 OpenFOAM(ESI v2606)으로 해석하는 프로젝트.

마지막 갱신: 2026-11-11

## 현재 위치
- 형상 단계 rev1을 사용자가 **승인했습니다** (`01_geometry/GEOMETRY_APPROVED`, 2026-11-11 15:59).
- 격자 단계 rev0을 사용자가 **승인했습니다** (`02_mesh/MESH_APPROVED`, 2026-11-11 17:31).
- 다음은 계산 단계(03_cases)입니다. 유동 조건(유량, 온도, 물성)과 촉매층 다공성 계수를 사용자에게 받아야 합니다.

---

## 형상 (01_geometry)
- **상태:** 승인 완료 (rev1, 2026-11-11)
- **마지막 작업:** 2026-11-11 — 사용자 승인 (`GEOMETRY_APPROVED` 생성). 그 전: `./Allgeometry` 실행 (spec rev1 → `out/` 생성, 15:05)
- **주요 결과:**
  - geometry_report 판정 **PASS** (FAIL 0, WARN 0)
  - watertight, 법선 일관/바깥 방향, 단일 body, 패치 6개 이름 spec과 일치
  - 부피 STL 3.4248 / CAD 3.4252 m³ (오차 0.009%)
  - bbox [-0.5, -0.5, -2.3] ~ [0.5, 0.5, 4.7] m
  - OpenFOAM surfaceCheck: closed, unconnected parts 1, zones 1
  - 삼각형 3092개 (inlet 124, outlet 124, wall_inlet_duct 254, wall_diffuser 2199, wall_housing 139, wall_outlet_duct 252)
  - locationInMesh 후보 [0.0123, 0.0137, 1.9321]
  - 촉매층(cellZone 박스, STL로 만들지 않음): catalyst_1 z 2.10~2.70, catalyst_2 z 1.15~1.75, catalyst_3 z 0.20~0.80
- **남은 문제 / 확인 필요:**
  - 가정값/추정값은 rev1 값 그대로 승인됨 (아래 목록). 계산 단계에서 바꿔야 하면 형상 rev2 필요
  - 가정값(assumed): 입구 덕트 직경 0.4, 길이 0.3, 연장 1.2 / 디퓨저 높이 0.5 / 출구 덕트 길이 0.3, 연장 2.0
  - 추정값(estimated): 출구 중심 (0, 0), 촉매층 3개의 높이 위치 (두께 0.6은 given)
  - housing.height_z 2.7은 given이지만, "확장부가 포함되지 않는다"는 해석은 가정임 → 확인 필요
  - surfaceCheck 삼각형 품질: 42%가 0.05 미만이고 최소값은 ~0 (triangle 593). `out/badFaces`에 40개가 기록됨 (geometry_report에는 없는 내용). 원→사각 확장부의 CAD 삼각화 특성으로 보이지만, snappyHexMesh 결과에 영향을 주는지는 격자 단계에서 확인 필요
  - `01_geometry/README.md`의 경로가 예전 값(`~/work/01_geometry`)으로 남아 있음 → 현재 경로 `~/work/SCR/3layer/01_geometry`로 고쳐야 함
- **다음 할 일:** 없음 (README 경로 수정만 남음). 형상을 바꾸면 rev2로 진행하고 다시 승인 필요

## 격자 (02_mesh)
- **상태:** 승인 완료 (rev0, 2026-11-11)
- **마지막 작업:** 2026-11-11 — 사용자 승인 (`MESH_APPROVED` 생성). 그 전: `./Allmesh` 실행 (mesh_spec rev0, 직렬, 약 57초)
- **주요 결과:**
  - mesh_report 판정 **PASS** (FAIL 0, WARN 0), checkMesh "Mesh OK"
  - 총 셀 233,884개 (hex 207,328 / poly 20,860 / prism 5,605 / 기타 91). 레이어 넣기 전 135,886개
  - 배경 격자 22×22×142, h = 0.05 m. 레벨별: L0 15,441 (50 mm), L1 164,459 (25 mm), L2 53,984 (12.5 mm)
  - max non-ortho 57.0 (기준 ≤ 65, 평균 7.6), max skewness 2.71 (기준 ≤ 4.0), max aspect ratio 16.2, 최소 체적 5.7e-8 m³
  - 유체 체적 격자 3.4228 / 형상 3.4252 m³ (오차 0.07%)
  - 패치 6개 이름·타입 일치, 예상 밖 패치 없음 (누설 없음)
  - 레이어 3층 커버리지: inlet_duct 100%, diffuser 87%, housing 88%, outlet_duct 98% (기준 ≥ 80%)
  - cellZone 체적 오차: catalyst_1 0.34%, catalyst_2 0.03%, catalyst_3 0.00%
  - 형상 단계에서 걱정했던 STL 저품질 삼각형은 격자 품질에 문제를 일으키지 않음
- **남은 문제 / 확인 필요:**
  - 가정값(assumed): cell_size 0.05 m, n_layers 3 → 이번 검증용 초기값. 계산 단계에서 y+를 확인하고 필요하면 조정
  - 참고(판정 제외): -allGeometry에서 concave cells 11,325개, small determinant 68개. snappy 격자에서 흔한 항목
  - 하우징 모서리와 확장부-하우징 접합부(z = 2.7)에서 레이어가 얇아짐 (일반적인 snappy 동작, 커버리지 기준은 만족)
  - 셀 수가 100만 개 미만이라 skill 지침에 따라 직렬(n_procs 1)로 실행함 → 8/12/16코어 비교는 하지 않음. 해상도를 높여 100만 개를 넘으면 그때 비교
- **다음 할 일:** 없음. `./Allmesh`를 다시 실행하면 승인이 자동 해제되므로 격자를 바꿀 때는 rev1로 올리고 다시 승인 필요

## 계산 (03_cases)
- **상태:** 착수 가능 (격자 승인됨)
- **마지막 작업:** -
- **주요 결과:** -
- **남은 문제 / 확인 필요:** 계산 skill/지침 없음 (확인 필요). 유동 조건(유량, 온도, 물성), 촉매층 다공성 계수 → 확인 필요
- **다음 할 일:** 유동 조건 확인 후 케이스 구성 (`02_mesh/case/constant/polyMesh` 복사해서 사용)

## 후처리 (04_reports)
- **상태:** 미착수
- **마지막 작업:** -
- **주요 결과:** -
- **남은 문제 / 확인 필요:** 평가 지표(속도 균일도, 압력 손실 등) → 확인 필요. CLAUDE.md 단계 표에는 이 단계가 아직 없음
- **다음 할 일:** -

---

## 결정 기록
| 날짜 | 결정 내용 | 이유 |
|---|---|---|
| 확인 필요 (2026-11-11 이전) | 출구를 측면에서 하우징 바닥 중앙(-z)으로 이동 (spec rev1) | 수정된 스케치 `source/geometry_1.pptx` 반영 |
| 확인 필요 (2026-11-11 이전) | 촉매층 두께 0.6 m 확정 (given) | 사용자 확정 (spec rev1 changelog) |
| 2026-11-11 | 병렬 코어 기본값 8 (P코어 수). 격자 단계에서 8/12/16코어 속도 비교 후 최종 결정 | i7-14700F는 P8+E12 혼합 구조라 코어를 늘린다고 선형으로 빨라지지 않음 |
| 2026-11-11 | 작업 경로 `~/work/SCR/3layer` | 프로젝트 폴더 구조 확정 |
| 2026-11-11 | git으로 이력 관리, 단계 작업이 끝날 때마다 PROGRESS.md 갱신 후 커밋 | 작업 위치·결정 추적 |
| 2026-11-11 | 형상 rev1 승인 (가정값·추정값은 rev1 그대로 사용) | 사용자가 보고서 검토 후 `GEOMETRY_APPROVED` 생성 |
| 2026-11-11 | 격자 rev0 승인 (cell_size 0.05 m, 레이어 3층, 23.4만 셀, 직렬 생성) | 사용자가 mesh_report 검토 후 `MESH_APPROVED` 생성 |
