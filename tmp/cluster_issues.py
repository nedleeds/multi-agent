import json, sys
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

# Issue data (key, summary, description_preview)
issues = [
    {"key": "HIXM-5355", "summary": "센서 기반 힘 제어; bypass 모드, fctrl on 진행 중 수동 전환 후 fctrl on 재 실행 시 통신 에러", "desc": "[리뷰 검토 항목] … bypass 모드에서 fctrl on 상태에서 수동으로 강제 전환(PlayBackStop) 이후, fctrl on 명령을 실행하면 통신 에러 발생"},
    {"key": "HIXM-5354", "summary": "중복 스텝 실행 시 TP 에서 yes_cancel_box 실행 안됨 현상", "desc": "[리뷰 검토 항목] … 중복 스텝 기록 시, main 서비스 함수 Job::ins_cmd_line_sub() 이 반환하는 ERR_DUP_RECORD 가 TP 에 전달되지 않아 중복 스텝 기록이"},
    {"key": "HIXM-5353", "summary": "TP 네트워크 게이트웨이 수정 안되는 문제", "desc": "[리뷰 검토 항목] … TP 네트워크 설정 값이 저장되지 않음"},
    {"key": "HIXM-5352", "summary": "[더테스트] cowork 명령문 영역 외에서 스텝 명령문 실행 시 \"로봇 협조 대기 중\" 발생 및 동작 불가", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5351", "summary": "[마북 시험동_SW] 자동모드에서 모드 변경 시 좌측 패널 변경안됨 및 반복 사이클 설정 안됨", "desc": "[리뷰 검토 항목] … 자동모드에서 모드 변경 시 좌측 패널 변경안됨 및 반복 사이클 설정 안됨"},
    {"key": "HIXM-5350", "summary": "[Hi5a 기능 이식] 내장 PLC로 날짜/시간 설정", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5349", "summary": "[HMC전주]HX300L 브레이크 검사기능 사용시 1축소음발생", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5348", "summary": "스터드 용접 시, 포지셔너가 축 지령 최고속 초과 오류가 발생하는 현상", "desc": "[리뷰 검토 항목] … 스터드 용접 중, 포지셔너가 동작하면 축 지령 최고속 초과 오류 발생"},
    {"key": "HIXM-5347", "summary": "터치센싱 tool_prj 실행시 \"E14180\" 발생 문제 보완", "desc": "[리뷰 검토 항목] … 터치센싱 tool_prj 실행시 \"E14180\" 발생"},
    {"key": "HIXM-5346", "summary": "[더테스트] 슬레이브 로봇에서 cmov 명령어 실행 시 E12036 에러 발생함", "desc": "[리뷰 검토 항목] … 슬레이브 로봇에서 cmov 명령어 실행 시 E12036 에러"},
    {"key": "HIXM-5345", "summary": "중력 보상에 대한 제한 기준 반영", "desc": "[리뷰 검토 항목] … 중력 보상 제한 기준 반영"},
    {"key": "HIXM-5344", "summary": "캘리브레이션 후, 중력 보상 활성화가 늦게 되는 현상", "desc": "[리뷰 검토 항목] … 캘리브레이션 수행 후, 중력 보상 기능이 비활성화 되어 있는 현상"},
    {"key": "HIXM-5343", "summary": "대기 이슈", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5342", "summary": "[대창단조]V60.30-11 사용중 전역변수 초기화 발생", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5341", "summary": "Hi7 제어기에서 스터드 용접 시 E3060 오류 발생", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5340", "summary": "제어 시뮬레이션", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5339", "summary": "MV603207_HDR08-14_E2780(2축 서보록 유지 불가능 - 배선 전류생성문제", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5338", "summary": "call 프로그램 동작 후 리턴 즉시 정지 하고 StepBWD 후 끝까지 재생시 서브 Job Step0로 이동 현상 보완; 아진산업", "desc": "[리뷰 검토 항목] … call 프로그램이 반환된 직후 멈추고, StepBWD 후 end까지 재생하면 서브 Job이 Step 0으로 이동"},
    {"key": "HIXM-5337", "summary": "제어기 부팅 시 쓰지 않는 플러그인 제거", "desc": "[리뷰 검토 항목] … deprecated 된 플러그인이 /ata0:2/hi6/lib/apps 에 설치 되면 삭제하기 전까지 남아있는 혅"},
    {"key": "HIXM-5336", "summary": "TSM3104S2039E705, TSM7580S1B21E200, TSM1040S1020E200 신규 모터 등록", "desc": "[리뷰 검토 항목] … 신규 모터 등록"},
    {"key": "HIXM-5335", "summary": "로봇 파일 정보 XML 파라미터 제한값 초과 에러처리 보완", "desc": "[리뷰 검토 항목] … XML 파라미터가 제한값을 초과되어 에러(-51)가 리턴되도 에러 발생되지 않음"},
    {"key": "HIXM-5334", "summary": "NT모델 코드 리펙토링", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5333", "summary": "[LGD]W23001 에러 발생", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5332", "summary": "정적 분석 경고 개선", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5331", "summary": "[성우하이텍]DO 신호 부논리 출력 오류", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5330", "summary": "센서 기반 힘제어; 명령어 입력 시 OnlineTracking Start 시작되는 현상", "desc": "[리뷰 검토 항목] … Job에서 fctrl on 명령어 입력 시 \"E0272 지원하지 않는 센서 데이터\" 발생"},
    {"key": "HIXM-5329", "summary": "로봇언어 save_csv 명령어 동작 불능", "desc": "[리뷰 검토 항목] … save_csv 는 전역변수 최상위 배열을 저장하는 기능으로, 포즈변수를 직접 저장하면서 저장이 안되는 현상"},
    {"key": "HIXM-5328", "summary": "새 프로그램을 작성할 수 없는 문제 보완; 현풍트레이닝센터", "desc": "[리뷰 검토 항목] … 새 프로그램을 선택할 수 없음"},
    {"key": "HIXM-5326", "summary": "태스크 유효 동작 주기 초과 발생; 마북 시험동_SW_US220-0D", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5325", "summary": "포지셔너 및 스터드 용접 시 정지 현상; [미국 아진산업]", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5324", "summary": "센서 기반 힘 제어; 어드미턴스 모델 튜닝", "desc": "[리뷰 검토 항목] … 기존 및 추가 모델 튜닝"},
    {"key": "HIXM-5323", "summary": "[아크센싱] 재기동 시 전류,전압,wvs 데이터 깨짐 현상 보완", "desc": "[리뷰 검토 항목] … 재기동 시 10번에 1번 꼴로 wvs, 전류, 전압 유지데이터 크기가 달라지는 현상"},
    {"key": "HIXM-5322", "summary": "미사용 변수 대입 코드 개선", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5321", "summary": "[일성엠텍] 자동모드에서 태스크데몬 실행 이상", "desc": "[리뷰 검토 항목] … 자동모드에서 태스크데몬 화면 진입 후 시작, 정지, 리셋 버튼 클릭 시 tp fault"},
    {"key": "HIXM-5320", "summary": "BitBlock 포인터 맴버 변수 delete 사용 오류", "desc": "[리뷰 검토 항목] … BitBlock 클래스의 포인터 타입 변수 bytep_ 가 배열로 할당 되었으나 delete[] 로 삭제되지 않음"},
    {"key": "HIXM-5319", "summary": "프로그램 선택시 태스크 데몬 실행이 사라지는 문제 보완", "desc": "[리뷰 검토 항목] … 프로그램 선택시 태스크 데몬 실행이 사라짐"},
    {"key": "HIXM-5318", "summary": "제진제어 v2 기능 구현", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5317", "summary": "신제진제어(Fuzzy 기반) 구현", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5316", "summary": "HRCV60.91-10_20260406_develop-1988 버전 업데이트 후 부팅 불능; 마북 시험동_SW_US220-0D", "desc": "[리뷰 검토 항목] … 문제 현상: HRCV60… 부팅 불능"},
    {"key": "HIXM-5315", "summary": "LogManager 생성 전 발생하는 event 들도 기록하는 기능의 오류 보완 (set_as_loaded)", "desc": "[리뷰 검토 항목] … 부팅 초기에 발생한 에러, 실행 이력이 이력 창과 HRWorkbench에 안 나오는 문제"},
    {"key": "HIXM-5314", "summary": "위치편차 도움말 보완", "desc": "[리뷰 검토 항목] … 부가축 가감속 파라미터 설정 오류로 인한 위치편차"},
    {"key": "HIXM-5313", "summary": "가상 함수 override 키워드 명시", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5312", "summary": "내장PLC에서 시스템 변수 얻기 개발", "desc": "[리뷰 검토 항목] … 내장 PLC에서 슬롯영역에서 시스템변수 설정만 존재함"},
    {"key": "HIXM-5311", "summary": "E0150 CPU Fault 보완; [신성미네랄] 팔레타이징 로봇 재부팅후 타겟 복구 중", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5310", "summary": "센서 기반 힘 제어; 가변 댐핑 설계", "desc": "[리뷰 검토 항목] … 강성이 큰 물체에 초기 접촉 시 진동 발생"},
    {"key": "HIXM-5309", "summary": "센서 기반 힘 제어; 목표 힘 커브 형태로 설정", "desc": "[리뷰 검토 항목] … 목표 힘을 Impulse 입력과 같이 주어 힘이 클 수록 허공에서 빠르게 진입"},
    {"key": "HIXM-5308", "summary": "[아진산업_SW_HH300] 로봇 오동작에 따른 제품 충돌 발생", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
    {"key": "HIXM-5307", "summary": "아크용접기 신호 편집 후 enter 두 번 누를 시 '이미 할당되어 있는 번호입니다' 에러 발생", "desc": "[리뷰 검토 항목] … 아크용접기 신호 편집 후 enter 두 번 누를 시 '이미 할당되어 있는 번호입니다' 에러"},
    {"key": "HIXM-5306", "summary": "HDC25-18 HDC50-17 mPPI 튜닝", "desc": "[리뷰 검토 항목] … HDC25-18 HDC50-17 mPPI 튜닝"},
    {"key": "HIXM-5305", "summary": "힘제어 기능 개발 시 버전 관련 릴리즈 노트 이슈", "desc": "[리뷰 검토 항목] … 문제 현상: 1 ~ 2줄로 간략히 작성"},
]

# Build documents
texts = []
keys = []
for issue in issues:
    doc = f"{issue['summary']} {issue['desc']}"
    texts.append(doc)
    keys.append(issue['key'])

vectorizer = TfidfVectorizer(stop_words='korean', max_features=2000)
X = vectorizer.fit_transform(texts)

# Choose number of clusters (8 as example)
num_clusters = 8
kmeans = KMeans(n_clusters=num_clusters, random_state=42, n_init='auto')
kmeans.fit(X)
labels = kmeans.labels_

# Get top terms per cluster
order_centroids = kmeans.cluster_centers_.argsort()[:, ::-1]
terms = vectorizer.get_feature_names_out()
cluster_terms = []
for i in range(num_clusters):
    top_terms = [terms[ind] for ind in order_centroids[i, :10]]
    cluster_terms.append(top_terms)

# Build result mapping
result = {}
for key, label in zip(keys, labels):
    result.setdefault(label, []).append(key)

output = {
    "cluster_terms": cluster_terms,
    "clusters": {str(k): v for k, v in result.items()},
    "num_clusters": num_clusters
}
print(json.dumps(output, ensure_ascii=False, indent=2))
