# 03_cases — 계산

## 실행 (3layer 폴더 기준)
```bash
cd 03_cases && ./Allrun            # case_spec.yaml 사용
./Allrun other_spec.yaml           # 다른 사양 파일
./Allrun --force                   # 같은 이름 케이스의 기존 결과를 덮어쓰기
```
진입 조건: `02_mesh/MESH_APPROVED`, OpenFOAM 환경, `~/venvs/cfd`.
진행 상황 보기: `tail -f 03_cases/<케이스이름>/log.porousSimpleFoam`

## 단계
| stage | 내용 | 상태 |
|---|---|---|
| flow | 유동만: porousSimpleFoam + 비등방성 Darcy-Forchheimer (촉매층) | 구현됨 |
| reacting | 열·화학반응: 전용 솔버 (논문 반응 모델) | 3-2단계에서 구현 예정 |

## 구성
- `case_spec.yaml`: 운전 조건, 촉매 물성, 솔버 설정 (모든 값에 given/assumed/estimated 표시)
- `scripts/make_case.py`: 사양 → `<meta.name>/` 케이스 폴더 생성 (격자는 02_mesh에서 복사)
- `scripts/check_case.py`: 결과 검증 → `<meta.name>/report/flow_report.md`
  - 결과만 다시 분석: `python3 scripts/check_case.py --case <이름>`

## 결과 보기
- 보고서: `<이름>/report/flow_report.md` (Ctrl+Shift+V)
- ParaView: `<이름>/case.foam`
- 속도는 표면속도(superficial). 촉매 채널 안의 물리 속도 = 표면속도 / 공극률(0.687)

## 여러 조건 비교
`meta.name`을 바꾼 사양 파일을 만들어 실행하면 케이스 폴더가 따로 생겨 결과가 보존됩니다.
예: `flow_2layer`(검증 2단) → `flow_3layer`(active_layers에 catalyst_3 추가)
