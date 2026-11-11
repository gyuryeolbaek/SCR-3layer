# PROGRESS — SCR 3층 촉매 반응기 CFD

사각 하우징(1.0×1.0×2.7 m)에 촉매층 3개가 있는 SCR 반응기의 내부 유동을 OpenFOAM(ESI v2606)으로 해석하는 프로젝트.

마지막 갱신: 2026-11-11

## 현재 위치
- 형상 단계 rev1 작업이 끝났습니다. 자동 검사 결과는 PASS이며, **사용자 검토를 기다리고 있습니다** (`GEOMETRY_APPROVED` 없음).
- 사용자가 가정값 11개를 확인하고 승인하면 격자 단계(02_mesh)를 시작합니다.

---

## 형상 (01_geometry)
- **상태:** 사용자 검토 대기
- **마지막 작업:** 2026-11-11 — `./Allgeometry` 실행 (spec rev1 → `out/` 생성, 15:05)
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
  - `GEOMETRY_APPROVED` 없음 → 사용자가 승인해야 함
  - 가정값(assumed): 입구 덕트 직경 0.4, 길이 0.3, 연장 1.2 / 디퓨저 높이 0.5 / 출구 덕트 길이 0.3, 연장 2.0
  - 추정값(estimated): 출구 중심 (0, 0), 촉매층 3개의 높이 위치 (두께 0.6은 given)
  - housing.height_z 2.7은 given이지만, "확장부가 포함되지 않는다"는 해석은 가정임 → 확인 필요
  - surfaceCheck 삼각형 품질: 42%가 0.05 미만이고 최소값은 ~0 (triangle 593). `out/badFaces`에 40개가 기록됨 (geometry_report에는 없는 내용). 원→사각 확장부의 CAD 삼각화 특성으로 보이지만, snappyHexMesh 결과에 영향을 주는지는 격자 단계에서 확인 필요
  - `01_geometry/README.md`의 경로가 예전 값(`~/work/01_geometry`)으로 남아 있음 → 현재 경로 `~/work/SCR/3layer/01_geometry`로 고쳐야 함
- **다음 할 일:** 사용자가 보고서와 preview 그림을 검토하고 가정값을 확정/교체 → 승인 시 `touch 01_geometry/GEOMETRY_APPROVED`

## 격자 (02_mesh)
- **상태:** 미착수
- **마지막 작업:** -
- **주요 결과:** -
- **남은 문제 / 확인 필요:** 격자 skill/지침 없음 (확인 필요). 위 STL 저품질 삼각형의 영향 확인
- **다음 할 일:** 형상 승인 후 blockMesh/snappyHexMesh 구성, 촉매층 topoSet cellZone 생성, 병렬 코어 8/12/16 속도 비교

## 계산 (03_cases)
- **상태:** 미착수
- **마지막 작업:** -
- **주요 결과:** -
- **남은 문제 / 확인 필요:** 유동 조건(유량, 온도, 물성), 촉매층 다공성 계수 → 확인 필요
- **다음 할 일:** 격자 승인 후 케이스 구성

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
