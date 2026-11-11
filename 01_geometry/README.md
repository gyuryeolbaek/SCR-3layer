# 01_geometry — 형상 → snappyHexMesh용 STL

## 처음 한 번 (WSL)
```bash
cd ~/work/SCR/3layer/01_geometry
python3 -m venv ~/venvs/cfd && source ~/venvs/cfd/bin/activate
pip install -r requirements.txt
```

## 실행
```bash
source <OpenFOAM etc/bashrc>   # surfaceCheck까지 돌리려면
./Allgeometry
```

## 결과 (out/)
| 파일 | 용도 |
|---|---|
| fluid.stl | snappyHexMesh 입력 (패치별 solid 이름 포함, 단위 m) |
| patches/*.stl | 패치별 개별 STL |
| fluid.step | Windows FreeCAD에서 형상 확인용 |
| geometry_out.json | 격자 단계로 넘기는 정보 (패치, 촉매 박스, locationInMesh) |
| geometry_report.md | 체크포인트 보고서 (사람이 확인) |
| preview_*.png | 단면도 / 패치 색상 3D 그림 |

Windows에서 보기: 탐색기 주소창에 `\\wsl.localhost\Ubuntu\home\<사용자>\work\SCR\3layer\01_geometry\out`

## 승인
보고서와 그림을 확인한 뒤 직접 생성:
```bash
touch ~/work/SCR/3layer/01_geometry/GEOMETRY_APPROVED
```
`./Allgeometry`를 다시 돌리면 승인은 자동 해제됩니다.

## 형상을 바꾸고 싶을 때
`geometry_spec.yaml`의 값만 수정하고 다시 실행. 실측값으로 바꾼 항목은 `status: given`으로 변경.
