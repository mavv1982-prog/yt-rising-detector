# 🚀 유튜브 떡상 탐지기

구독자 1만 이하 소형 채널 중 최근 48시간 내 조회수가 폭발한 영상과 구독자가 급증한 채널을 매일 아침 7시(한국시간) 이메일로 받아보는 무료 자동화 툴입니다. 서버 없이 GitHub Actions에서 실행됩니다.

---

## 설정 방법 (약 20분, 코딩 지식 불필요)

### 1단계. YouTube API 키 발급 (무료)

1. https://console.cloud.google.com 접속 → 구글 계정 로그인
2. 상단에서 **새 프로젝트** 생성 (이름 아무거나)
3. 왼쪽 메뉴 **API 및 서비스 → 라이브러리** → "YouTube Data API v3" 검색 → **사용 설정**
4. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → API 키**
5. 생성된 키를 복사해두기 (나중에 씁니다)

### 2단계. Gmail 앱 비밀번호 발급

일반 비밀번호가 아니라 "앱 비밀번호"가 필요합니다.

1. 구글 계정에 **2단계 인증**이 켜져 있어야 합니다 (myaccount.google.com → 보안)
2. https://myaccount.google.com/apppasswords 접속
3. 앱 이름 아무거나 입력 → 생성 → 16자리 비밀번호 복사

### 3단계. GitHub 저장소 만들기

1. https://github.com 가입 후 **New repository** → 이름 예: `yt-rising-detector` → **Private** 선택 → 생성
2. 이 폴더의 파일들을 업로드:
   - **Add file → Upload files**로 `analyzer.py`, `README.md` 업로드
   - `.github/workflows/daily.yml`은 폴더 구조가 필요하므로 **Add file → Create new file** 선택 후 파일명 칸에 `.github/workflows/daily.yml` 이라고 직접 입력하고 내용을 붙여넣기

### 4단계. 비밀 정보 등록

저장소에서 **Settings → Secrets and variables → Actions → New repository secret**으로 아래 4개를 등록:

| 이름 | 값 |
|---|---|
| `YT_API_KEY` | 1단계에서 받은 API 키 |
| `GMAIL_ADDRESS` | 발신용 Gmail 주소 |
| `GMAIL_APP_PASSWORD` | 2단계의 16자리 앱 비밀번호 |
| `TO_EMAIL` | 받을 이메일 주소 (쉼표로 여러 개 가능) |

### 5단계. (선택) 국가/조건 변경

같은 화면의 **Variables** 탭에서 등록하면 코드 수정 없이 조건을 바꿀 수 있습니다:

| 이름 | 예시 값 | 설명 |
|---|---|---|
| `REGION_CODES` | `KR,US,JP` | 국가 코드 (기본 KR) |
| `MAX_SUBSCRIBERS` | `10000` | 구독자 상한 |
| `LOOKBACK_HOURS` | `48` | 검색 기간(시간) |
| `TOP_N_PER_CATEGORY` | `5` | 카테고리별 상위 개수 |

### 6단계. 테스트 실행

저장소의 **Actions 탭 → 유튜브 떡상 리포트 → Run workflow** 버튼을 누르면 즉시 한 번 실행됩니다. 1~2분 뒤 이메일이 오면 성공! 이후로는 매일 아침 7시에 자동 실행됩니다.

---

## ⚠️ 꼭 알아둘 것

**API 쿼터**: 무료 쿼터는 하루 10,000유닛이고 검색 1회에 100유닛을 씁니다. 기본 설정(카테고리 8개 × 국가 1개)은 약 800유닛이라 여유롭지만, 국가를 늘리면 `카테고리 수 × 국가 수 × 100`으로 계산해서 9,000을 넘지 않게 하세요. 국가 3개면 2,400유닛으로 안전합니다.

**구독자 급증 탐지**: 첫 실행에는 비교할 과거 데이터가 없어서 안 나옵니다. 2일차부터 자동으로 탐지되며, 데이터는 저장소의 `channel_history.json`에 매일 쌓입니다.

**국가 필터의 한계**: YouTube API의 국가 필터는 "그 나라에서 인기 있고 시청 가능한 영상" 기준이라, 100% 그 나라 채널만 나오지는 않습니다.

**카테고리 변경**: `analyzer.py` 상단의 `CATEGORIES` 부분에서 추가/삭제할 수 있습니다. 주요 ID: 스포츠 17, 음악 10, 게임 20, 인물/브이로그 22, 코미디 23, 엔터 24, 노하우/스타일 26, 교육 27, 과학기술 28, 뉴스/정치 25.

**떡상 기준 조절**: `analyzer.py`의 `MIN_VIEWS`(최소 조회수), `MIN_MULTIPLIER`(구독자 대비 배수), `SUB_GROWTH_ALERT`(구독자 급증 %)를 취향껏 수정하세요.
