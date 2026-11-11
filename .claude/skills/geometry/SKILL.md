---
name: geometry
description: 형상 정보(PPT, 스케치, 도면, 치수 메모)를 geometry_spec.yaml로 옮기고 snappyHexMesh용 패치별 STL을 생성·검증하는 작업. 사용자가 형상, 지오메트리, STL, 치수, 덕트/하우징/촉매층 위치를 언급하거나, 01_geometry 폴더를 다루거나, 격자 작업 전에 형상을 준비해야 할 때 반드시 이 skill을 사용할 것.
---

# Geometry 단계 (01_geometry)

목표: 사용자가 준 형상 정보 → `geometry_spec.yaml` → `out/fluid.stl` (+ 검증 보고서) → **사용자 승인 대기**.

## 절대 규칙
1. **값을 지어내지 않는다.** 원본 자료에 없는 치수는 합리적 가정값을 넣되 반드시 `status: assumed`와 `note`(근거)를 단다. 스케치 비율로 읽은 값은 `status: estimated`. 사용자가 확인한 값만 `given`.
2. **형상 변경은 spec에서만 한다.** 스크립트 안에 치수를 하드코딩하지 않는다.
3. **패치 이름은 spec의 `patches`가 기준**이다. 경계조건·후처리 지침과 철자까지 일치해야 한다. 이름을 바꿔야 하면 사용자에게 먼저 묻는다.
4. **단위는 m.** STL을 mm로 만들고 나중에 스케일하지 않는다.
5. **촉매층 등 다공성 영역은 STL로 만들지 않는다.** spec의 박스 범위로 기록만 하고, 격자 단계에서 topoSet cellZone으로 처리한다.
6. **`GEOMETRY_APPROVED` 파일은 절대 직접 만들지 않는다.** 사용자만 만든다.

## 작업 순서
1. 원본 자료 읽기 (`source/` 폴더). PPT는 텍스트와 그림을 모두 확인한다.
2. `geometry_spec.yaml` 작성/수정. 새로 추정한 값이 있으면 사용자에게 목록으로 알린다.
3. 실행:
   ```bash
   cd 01_geometry && ./Allgeometry
   ```
4. 실패 시 아래 문제 해결 표를 참고해 **spec 또는 스크립트**를 고치고 재실행한다. 사용자가 준 `given` 값을 바꿔서 통과시키지 않는다.
5. `out/geometry_report.md`의 판정과 WARN 항목, 가정값 목록을 사용자에게 요약하고 **멈춘다.**
   - 메시지 끝: "보고서와 preview 그림을 확인 후 승인하시면 `touch 01_geometry/GEOMETRY_APPROVED` 해 주세요."

## 통과 기준 (check_geometry.py)
- FAIL 0개: watertight, 법선 일관/바깥 방향, 단일 body, 단위 sanity, 패치 일치, surfaceCheck closed
- STL vs CAD 부피 오차 < 0.5%
- WARN은 통과를 막지 않지만 **반드시 사용자에게 설명**한다 (예: 출구가 촉매층 높이와 겹침).

## 현재 템플릿이 다루는 형상
`make_geometry.py`는 "사각 하우징 + 상부 원형 입구 덕트 + 원→사각 확장부 + 원형 출구 덕트(측면 ±x/±y 또는 바닥 -z)" 템플릿이다.
- 이 범위 안의 변경(치수, 출구 면 ±x/±y/-z, 위치)은 spec만 수정한다.
- 범위를 벗어나는 형상(엘보, 다중 출구, 내부 배플 등)은 `build_fluid()`와 `classify()`를 확장한다. 기존 패치 분류 방식(삼각화 **전** 면 bbox로 분류)을 유지하고, 새 패치는 spec `patches`에 먼저 추가한다.

## 문제 해결
| 증상 | 원인/조치 |
|---|---|
| solid가 2개 이상 | 덕트가 하우징과 안 겹침 → overlap 확인, 덕트 위치가 벽 범위 안인지 확인 |
| 생성되지 않은 패치 | `classify()` 기준 확인. 디버그: 각 face의 geomType과 bbox 출력 |
| watertight FAIL | `linear_deflection`을 줄이거나 boolean 후 `.clean()` 확인 |
| 부피 오차 큼 | `linear_deflection` 축소 (곡면 근사 오차) |
| surfaceCheck SKIP | OpenFOAM 환경 미로드 → `source <OpenFOAM>/etc/bashrc` 후 재실행 |

## 다음 단계로 넘기는 것
`out/geometry_out.json`: 패치 목록, bbox, 촉매 박스(`catalyst_zones`), `location_in_mesh` 후보. 격자 단계는 이 파일과 `out/fluid.stl`만 읽는다.
