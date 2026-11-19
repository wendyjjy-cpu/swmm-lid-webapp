# LID 유출 저감 시나리오 분석 웹앱

EPA SWMM 5.2 엔진을 웹에서 직접 구동해 **LID 종류·적용률·배치 위치·막힘 상태**에 따른
개발지 유출 저감 효과를 분석하는 Streamlit 웹앱입니다.

- 대상 모형: EPA SWMM 응용 매뉴얼(Applications Manual) Example 2 (`models/Example2-Post.inp`, 약 11.7 ha 주거 개발지 배수 관망)
- 계산 엔진: EPA SWMM 5.2 (`swmm-toolkit` 패키지)
- 화면: 모의조건 설정 → 모델 실행 → 결과 대시보드 → 시나리오 비교 → 검증
- 강우 입력: 매뉴얼 설계강우(2·10·100년), 총강우량 조정, 균등 강우, 5분 간격 직접 입력

## 검증 결과 요약

LID 없는 기준 모형을 매뉴얼 결과표 36개 항목(표 2-7, 표 1-9, 표 2-6)과 비교한 결과,
35개가 1% 이내, 최대 차이 1.35%(관로 C10 최대유량/만관유량 0.75 vs 0.74, 소수 둘째 자리 반올림 차이)입니다.
예: 동역학파 첨두유량 2·10·100년 = 33.44 / 62.28 / 164.13 cfs (매뉴얼 33.4 / 62.2 / 164.1 cfs).

## 폴더 구조

```
app.py                  Streamlit 화면
swmm_lid/
  inp.py                INP 섹션 읽기·쓰기
  model.py              모형 정보(단위, 소유역, 관망, 좌표, 강우) 추출
  lid.py                LID 설계값 템플릿, 막힘, 배치 → LID_CONTROLS/LID_USAGE 생성
  runner.py             SWMM 엔진 호출 (swmm-toolkit, 동시 실행 잠금)
  outfile.py            SWMM 이진 결과(.out) 판독
  report.py             SWMM 리포트(.rpt) 요약표 판독
  scenario.py           시나리오 실행과 KPI 계산
  validation.py         기준 결과·GUI 리포트 비교
  charts.py             Plotly 그래프
models/                 INP 파일
validation/manual_reference.csv   매뉴얼 기준 결과표
docs/guide.md           웹앱 안의 사용 안내
tests/                  자동 시험
```

## 내 컴퓨터에서 실행 (Windows)

1. Python 3.11 설치 (python.org, 설치 시 "Add python.exe to PATH" 체크)
2. 이 폴더에서 명령 프롬프트를 열고:
   ```
   pip install -r requirements.txt
   streamlit run app.py
   ```
3. 브라우저에서 http://localhost:8501 이 열립니다.

## 웹 배포 (Streamlit Community Cloud, 무료)

1. GitHub에 새 저장소를 만들고 이 폴더의 파일을 모두 올립니다.
2. https://share.streamlit.io 에 GitHub 계정으로 로그인 → **Create app**
3. 저장소, 브랜치(main), 실행 파일(`app.py`)을 고릅니다.
4. **Advanced settings**에서 Python 버전을 **3.11**로 고릅니다.
5. **Deploy**를 누르면 몇 분 뒤 외부에서 접속 가능한 주소가 생깁니다.

## 자동 시험

```
python -m unittest discover -s tests -v
```

`tests/test_example2.py`는 매뉴얼 기준값 36개 재현, 정상류 100년 월류, 사용자 강우 기능을 시험합니다.
`tests/data/test_site.inp`는 자동 시험 전용 가상 모형입니다.

## 검증 방법

1. LID 없는 기준 모형을 응용 매뉴얼 결과표와 비교 (`validation/manual_reference.csv`)
2. 웹앱이 만든 시나리오 INP를 EPA SWMM 5.2 GUI에서 실행한 리포트와 비교 (검증 탭에서 업로드)
3. 유출·흐름 추적·LID 물수지 연속성 오차 확인

## 출처

- Rossman, L. A., & Simon, M. A. (2022). *Storm Water Management Model User's Manual Version 5.2*. EPA/600/R-22/030.
- Gironás, J., Roesner, L. A., & Davis, J. (2009). *Storm Water Management Model Applications Manual*. EPA/600/R-09/000.
- swmm-toolkit (pyswmm 프로젝트): EPA SWMM 엔진의 파이썬 연결 패키지.
- `models/Example2-Post.inp`는 위 응용 매뉴얼과 함께 EPA가 배포한 예제 입력 파일입니다.
