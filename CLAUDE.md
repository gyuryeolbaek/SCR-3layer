# CFD 자동화 프로젝트 공통 규칙

## 환경
- OpenFOAM: ESI (openfoam.com) v2606 (api=2606, patch=0)
- 환경 로드: `source /usr/lib/openfoam/openfoam2606/etc/bashrc` (~/.bashrc에서 자동 로드됨)
- Python venv: `source ~/venvs/cfd/bin/activate`
- 사용 가능 코어 수: 논리 28 (i7-14700F, 물리 20 = P8+E12)
- 병렬 코어 기본값: 8 (P코어 수). 격자 단계에서 8/12/16코어 속도를 비교해 최종 결정
- 작업 경로: ~/work/SCR/3layer (WSL 리눅스 파일시스템. /mnt/c 사용 금지)

## 단계와 승인 게이트
| 단계 | 폴더 | 다음 단계 진입 조건 |
|---|---|---|
| 형상 | 01_geometry | `01_geometry/GEOMETRY_APPROVED` 존재 |
| 격자 | 02_mesh | `02_mesh/MESH_APPROVED` 존재 |
| 계산 | 03_cases | - |

- 승인 파일(`*_APPROVED`)은 **사용자만** 만든다. Claude는 절대 생성하지 않는다.
- 각 단계 작업은 해당 skill(geometry 등)의 지침을 따른다.

## 공통 금지사항
- 사용자가 준 값(`status: given`)을 임의로 변경하지 않는다.
- 가정한 값은 반드시 기록하고 사용자에게 알린다.
- 파일 삭제는 해당 단계의 `out/` 폴더 안에서만 한다.

## 작업 기록 규칙
- 작업을 시작할 때 `PROGRESS.md`를 먼저 읽고 현재 위치를 파악한다.
- 한 단계의 작업이 끝나거나 사용자 검토 대기로 멈출 때 `PROGRESS.md`를 갱신한다 (마지막 갱신 날짜, 현재 위치, 해당 단계 섹션).
- 사용자와 합의한 결정은 `PROGRESS.md`의 "결정 기록"에 `날짜 | 결정 내용 | 이유` 형식으로 남긴다.
- `PROGRESS.md`를 갱신한 뒤 git 커밋한다.

### 커밋 규칙
- 단계 작업이 끝날 때 커밋한다. 메시지는 한국어로 `[단계] 무엇을 했는지` 형식 (예: `[형상] rev2: 출구 덕트 연장 3.0 m로 변경`).
- force push, `reset --hard`, 이력 삭제(rebase로 커밋 제거, `filter-branch` 등)처럼 되돌릴 수 없는 git 명령은 사용자 승인 없이 실행하지 않는다.
- `*_APPROVED` 파일은 사용자가 만든 것이므로 커밋에는 포함하되, Claude가 생성하거나 삭제하지 않는다.
