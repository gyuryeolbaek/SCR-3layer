---
name: mesh
description: snappyHexMesh로 OpenFOAM 격자를 만들고 검증하는 작업 (02_mesh 폴더). 사용자가 격자, 메쉬, mesh, snappyHexMesh, blockMesh, checkMesh, 레이어, y+, 셀 수, 세분화, 촉매 cellZone을 언급하거나, 형상 승인 후 다음 단계로 진행할 때 반드시 이 skill을 사용할 것.
---

# Mesh 단계 (02_mesh)

목표: `01_geometry/out/` → `mesh_spec.yaml` → `./Allmesh` → `report/mesh_report.md` → **사용자 승인 대기**.

## 절대 규칙
1. **진입 조건**: `01_geometry/GEOMETRY_APPROVED`가 없으면 격자 작업을 시작하지 않는다. 형상이 이상해 보여도 STL이나 geometry spec을 이 단계에서 고치지 않고, 사용자에게 보고한다.
2. **설정은 `mesh_spec.yaml`에서만 바꾼다.** `case/system/*Dict`는 매번 자동 생성되므로 직접 수정하지 않는다. spec에 없는 snappy 옵션이 필요하면 `scripts/make_mesh_case.py`를 확장하고 spec에 항목을 추가한다.
3. **품질 기준(`quality`)을 완화해서 통과시키지 않는다.** 기준 변경이 필요하면 이유와 함께 사용자에게 먼저 묻는다.
4. **셀 수 상한**: 예상 셀 수가 `quality.max_cells`를 넘는 변경은 사용자에게 먼저 묻는다.
5. **`MESH_APPROVED`는 절대 직접 만들지 않는다.**
6. spec을 바꿀 때마다 `meta.revision`을 올리고, 변경 이유를 PROGRESS.md 결정 기록에 남긴다.

## 작업 순서
1. PROGRESS.md와 `01_geometry/out/geometry_report.md`로 현재 형상 상태 확인
2. `cd 02_mesh && ./Allmesh`
3. 실패하거나 FAIL/WARN이 있으면 아래 "조정 가이드"에 따라 spec을 고치고 재실행. **한 번에 한 가지만** 바꾸고 결과를 비교한다.
4. `report/mesh_report.md`의 판정, 셀 수, WARN, 단면 그림을 요약해 사용자에게 보고하고 **멈춘다.**
   - "보고서와 그림 확인 후 승인하시면 `touch 02_mesh/MESH_APPROVED` 해 주세요."

## 판정 기준 (check_mesh.py)
- FAIL: checkMesh 표준 검사 실패, max non-ortho/skewness 기준 초과, 음수 체적, 패치 누락·빈 패치·예상 밖 패치(누설 신호), 촉매 cellZone 누락, 유체 체적 오차 ≥ 1%, cellZone 체적 오차 ≥ 2%
- WARN: 레이어 커버리지 < `target_coverage`, 촉매층 경계-격자면 불일치, cellZone 체적 오차 0.5~2%
- `-allGeometry`의 concave cells, small determinant는 snappy 격자에서 흔하므로 참고만 한다.

## 설계 원칙 (이 케이스)
- 배경 격자 크기 `cell_size`는 **촉매층 경계 z값들의 공약수**가 되게 고른다. 그래야 topoSet 박스가 격자면과 정확히 맞아 촉매층 두께가 정확하다 (현재 0.05 m). 세분화는 2의 거듭제곱으로 나뉘므로 이 정렬은 유지된다.
- 촉매층은 STL이 아니라 `topoSet` cellZone으로 만든다 (geometry_out.json의 catalyst_zones).
- 레이어는 2단계: `snappyHexMeshDict`(격자+snap) 성공 후 `snappyHexMeshDict.layers`로 추가.

## 조정 가이드
| 증상 | 조치 (한 번에 하나) |
|---|---|
| 예상 밖 패치에 면 / 체적이 형상보다 큼 | 누설 또는 locationInMesh 오류. geometry_out.json의 location_in_mesh와 STL 닫힘 확인 |
| 유체 체적이 형상보다 작음 | 영역 일부가 잘림. 좁은 곳의 세분화 레벨 +1 |
| 레이어 커버리지 낮음 | `n_layers` 감소 → `final_layer_thickness` 0.3 → `min_thickness` 0.05 순으로 시도 |
| non-ortho 초과 | 해당 부위 세분화 레벨 +1, 또는 `n_cells_between_levels` 증가 |
| 형상 모서리가 뭉개짐 | `feature_level` +1 |
| 셀 수가 너무 많음 | 세분화 레벨부터 낮춤. `cell_size`는 촉매 경계 z의 공약수여야 하므로(현재 경계 기준 0.05 또는 0.025) 함부로 키우지 않음 |
| 해상도를 높이고 싶음 | `cell_size` 0.05 → 0.025 (셀 수 약 8배) 또는 특정 패치 레벨 +1 |

## 병렬 실행
- 셀 100만 개 미만: `n_procs: 1` (직렬)로 충분.
- 그 이상: CPU가 P코어 8 + E코어 12 구조이므로 8부터 시작해 8/12/16을 비교하고 가장 빠른 값을 PROGRESS.md에 기록.

## 다음 단계로 넘기는 것
`case/constant/polyMesh` (patch: inlet, outlet, wall_*; cellZone: catalyst_1~3). 계산 단계는 이 격자를 복사해서 쓴다.
