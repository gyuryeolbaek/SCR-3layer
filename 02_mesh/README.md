# 02_mesh — snappyHexMesh 격자

## 실행 (3layer 폴더 기준)
```bash
cd 02_mesh && ./Allmesh
```
진입 조건: `01_geometry/GEOMETRY_APPROVED` 존재, OpenFOAM 환경 로드, `~/venvs/cfd` 가상환경.

## 흐름
mesh_spec.yaml + 01_geometry/out(geometry_out.json, fluid.stl)
→ `scripts/make_mesh_case.py` 가 `case/` 생성 (system/*Dict 자동 작성)
→ blockMesh → surfaceFeatureExtract → snappyHexMesh(격자+snap) → checkMesh
→ snappyHexMesh(레이어) → topoSet(촉매 cellZone) → checkMesh
→ `scripts/check_mesh.py` 가 `report/` 에 보고서·단면 그림 생성

## 결과
| 위치 | 내용 |
|---|---|
| case/constant/polyMesh | 격자 (git 제외, 스크립트로 재생성) |
| case/log.* | 각 단계 로그 |
| case/case.foam | Windows ParaView에서 열기용 |
| report/mesh_report.md | 체크포인트 보고서 (Ctrl+Shift+V) |
| report/mesh_section_*.png | 단면 격자 그림 |

## 승인
```bash
touch 02_mesh/MESH_APPROVED
```
`./Allmesh` 재실행 시 승인은 자동 해제됩니다.

## 설정을 바꾸고 싶을 때
`mesh_spec.yaml`만 수정 (case/system 의 Dict 파일은 매번 다시 생성되므로 직접 고치지 않음).
