# 3-2단계: 열·반응 계산

## 처음 한 번: 솔버 컴파일
```bash
cd 03_cases && ./solver/Allwmake
```
`wmake` 또는 헤더가 없다는 메시지가 나오면 OpenFOAM 개발 패키지를 설치하세요 (예: `sudo apt install openfoam2606-dev`).

## 실행
```bash
python3 scripts/verify_kinetics_1d.py      # 반응식 점검 (마이크로리액터 재현, 수 초)
./Allreact                                  # reacting_spec.yaml 사용
```
- 유동장: `reacting_spec.yaml`의 `source_flow_case` (확정된 유동 케이스) 최종 결과를 고정해 사용
- 결과: `<meta.name>/report/reacting_report.md`, ParaView: `<meta.name>/case.foam`
- 주요 출력 필드: `T`, `XNO_ppm`, `XNH3_ppm`, `theta`(NH3 피복률), `R_SCR`, `kappaEff`

## 반응 모델 (`catalyst.kinetics.model`)
- `chatterjee_coverage` (기본): Chatterjee et al. (SAE 2005-01-0965) 원문 형태. 피복률 θ는 촉매에 머물고 정상상태 흡착점 수지로 계산.
- `ace_pseudo_gas`: Baek et al. 논문의 CFD-ACE+ 서브루틴 형태 (NH3*를 이동 가상 화학종으로). 논문 재현용.
