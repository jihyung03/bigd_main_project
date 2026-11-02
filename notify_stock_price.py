"""
종목 하나의 현재가를 네이버 API로 조회해 텔레그램 메시지로 보내는 스크립트.

지난주에는 "안녕하세요" 같은 고정된 문구를 보냈지만, 이번 주부터는 **조회해서 얻은 값**을
메시지에 담아 보낸다. 관심종목을 여러 개로 늘리는 것은 다음에 한다.

send_price_notification()으로 알림 로직 전체를 함수 하나에 감싸 두었다.
`python notify_stock_price.py`로 직접 실행하면 그 함수가 그대로 실행된다.
"""
import os
import io
import re
import time
import html
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# 조회할 종목코드. 지금은 여기 직접 적어 두고, 나중에 파일에서 읽어오도록 바꾼다.
STOCK_CODE = "000660"  # 삼성전자
# STOCK_CODE = "000660"  # SK하이닉스

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def read_watchlist(path: str = WATCHLIST_FILE) -> list:
    """watchlist.csv를 읽어 종목코드 리스트를 반환합니다. 실패 시 None."""
    if not os.path.exists(path):
        print(f"❌ '{path}' 파일이 존재하지 않습니다.")
        return None

    try:
        import pandas as pd

        # pd.read_csv()는 CSV 파일을 읽어 표 형태의 자료구조인 DataFrame으로 돌려준다.
        # dtype={"code": str}을 안 주면 pandas가 "005930"을 숫자로 착각해 앞자리 0을
        # 없애버린다(5930) — 앞자리에 0이 있는 종목코드가 깨지지 않도록 문자열로 강제한다.
        watchlist_df = pd.read_csv(path, dtype={"code": str})

        # 컬럼명의 공백을 제거하고 소문자로 통일하여 'code' 컬럼을 찾습니다.
        watchlist_df.columns = [col.strip().lower() for col in watchlist_df.columns]

        if "code" not in watchlist_df.columns:
            print("❌ CSV 파일에 'code' 컬럼이 존재하지 않습니다.")
            return None

        # DataFrame의 "code" 열(Series)을 파이썬 기본 리스트로 변환한다.
        codes = watchlist_df["code"].tolist()
        print(f"📋 읽어온 관심 종목 리스트: {codes}")
        return codes

    except Exception as e:
        print(f"❌ CSV 파일을 읽는 동안 오류가 발생했습니다: {e}")
        return None


def fetch_naver_current_price(code: str, retries: int = 2) -> dict:
    """네이버 금융 비공식 API로 종목의 현재가(장중) 또는 최근 종가(장마감)를 조회합니다.

    순간적인 네트워크 오류에 대비해 최대 retries회까지 재시도합니다 — 한 번 실패했다고
    그 종목을 건너뛰면 알림에서 통째로 빠져 버리기 때문입니다.

    조회 자체가 실패했을 때뿐 아니라, 응답은 왔지만 가격을 숫자로 읽지 못했을 때(0원)도
    0이 담긴 dict 대신 None을 반환합니다 — 호출부가 "실패"로 명확히 구분할 수 있게.

    반환 dict에는 가격·등락률·장 개장 여부와 함께 그 가격이 체결된 시각("traded_at")도
    담깁니다 — 조회 시각이 아니라 체결 시각이라 저장할 거래일을 정하는 기준으로 쓸 수 있습니다."""
    url = f"https://m.stock.naver.com/api/stock/{code}/basic"
    # 왜 재시도가 필요한가: 여기서 실패하면 그 종목은 이번 알림에서 통째로 빠진다. 순간적인
    # 네트워크 지연이나 일시적인 오류 때문에 그런 일이 생기는 걸 막으려고 최소한의
    # 재시도(기본 2회)를 넣었다.
    for attempt in range(1, retries + 1):
        try:
            # requests.get()으로 이 주소에 HTTP GET 요청을 보낸다. User-Agent 헤더가 없으면 일부
            # 서버가 "브라우저가 아닌 요청"으로 판단해 응답을 거부하기도 해서 브라우저인 척 흉내낸다.
            # timeout=3: 3초 안에 응답이 없으면 기다리지 않고 바로 예외를 발생시킨다(무한 대기 방지).
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
            if response.status_code != 200:
                print(f"❌ [{code}] 네이버 현재가 조회 실패 (응답 코드: {response.status_code}, {attempt}/{retries}번째 시도)")
            else:
                # JSON(JavaScript Object Notation)은 API가 데이터를 주고받을 때 가장 흔히 쓰는 텍스트
                # 형식이다. 생김새가 파이썬의 딕셔너리·리스트와 거의 그대로 대응된다:
                #   {"stockName": "삼성전자", "closePrice": "71,000", "marketStatus": "OPEN"}
                # 위 문자열이 바로 JSON이고, response.json()은 이 문자열을 실제 파이썬 딕셔너리로
                # 변환해준다 — 그 뒤로는 data["stockName"]처럼 평범한 딕셔너리 다루듯 쓸 수 있다.
                data = response.json()
                # data.get("closePrice", "0"): "closePrice" 키가 없으면 기본값 "0"을 쓴다(에러 방지).
                # 네이버 응답은 가격에 천단위 콤마가 찍혀 있어서("70,000") 숫자로 바꾸기 전에 지운다.
                price_str = data.get("closePrice", "0").replace(",", "")
                # 삼항 표현식(조건부 표현식): "조건이 참이면 A, 아니면 B"를 한 줄로 쓴 것.
                # price_str.isdigit()은 문자열이 숫자로만 이루어졌는지 확인 — 혹시 이상한 값이
                # 와도 int() 변환 중 프로그램이 멈추지 않고 0으로 처리하고 넘어가게 한다.
                price = int(price_str) if price_str.isdigit() else 0
                # 0원짜리 결과는 절대 그대로 돌려주지 않는다. 파싱에 실패했을 뿐인데 값이 있는 것처럼
                # 넘기면 텔레그램에 "0원"이라는 멀쩡해 보이는 가격이 표시되고, 그 값으로 그래프까지
                # 그려지면 전일 대비 계산이 통째로 깨진다.
                # 그래서 "조회 실패"와 똑같이 취급해 (재시도 후에도 안 되면) None을 반환하게 만든다.
                if price <= 0:
                    print(f"❌ [{code}] 가격을 숫자로 읽지 못했습니다 "
                          f"(closePrice: {data.get('closePrice')!r}, {attempt}/{retries}번째 시도)")
                else:
                    return {
                        "code": code,
                        "name": data.get("stockName", "알 수 없음"),
                        "price": price,
                        # "fluctuationsRatio"가 없거나 빈 문자열("")이면 or 뒤의 "0"을 대신 쓴다.
                        "rate": float(data.get("fluctuationsRatio", "0") or "0"),
                        "is_open": data.get("marketStatus") == "OPEN",
                        # localTradedAt: 네이버가 주는 실제 체결(갱신) 시각. "2026-07-29T16:10:20+09:00"
                        # 형태이고 KST 오프셋(+09:00)까지 붙어 있다. 조회 시각이 아니라 체결 시각이라
                        # 자정을 넘겨 조회해도 그 종목이 마지막으로 거래된 날을 그대로 가리킨다 —
                        # 나중에 종가를 날짜별로 기록하게 되면, "언제 실행됐는가"가 아니라
                        # "이 가격이 언제 체결된 것인가"를 기준으로 날짜를 정하는 데 쓴다.
                        "traded_at": data.get("localTradedAt"),
                    }
        except Exception as e:
            print(f"❌ [{code}] 네이버 현재가 조회 중 오류 발생: {e} ({attempt}/{retries}번째 시도)")

        if attempt < retries:
            time.sleep(1)  # 순간적인 오류일 수 있으니 짧게 대기 후 재시도

    return None


def fetch_naver_intraday_minutes(code: str, date_str: str = None) -> list:
    """네이버 API로 당일 1분 단위 종가를 조회합니다. 반환값은 시간 오름차순
    [{"time": "HHMM", "price": 가격}, ...]. 실패하거나 아직 장이 시작 전이면 빈 리스트.

    이 API는 과거 데이터를 저장해두는 게 아니라 그 시점까지의 당일 흐름을 즉시 다시 계산해
    돌려주는 방식이라, 우리 쪽에 별도로 저장할 필요가 없다.
    응답이 EUC-KR 인코딩과 표준이 아닌 JSON이 섞여 있어 json.loads 대신 데이터 행만 정규식으로
    추출한다 (헤더의 한글 라벨은 깨지지만 숫자로 된 데이터 행은 영향받지 않는다)."""
    date_str = date_str or datetime.now(KST).strftime("%Y%m%d")
    url = "https://api.finance.naver.com/siseJson.naver"
    params = {"symbol": code, "requestType": 1, "startTime": date_str, "endTime": date_str, "timeframe": "minute"}
    try:
        # params에 담은 딕셔너리는 requests가 자동으로 "?symbol=005930&requestType=1&..." 같은
        # 쿼리스트링으로 바꿔서 URL 뒤에 붙여준다.
        response = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        if response.status_code != 200:
            print(f"❌ [{code}] 당일 분봉 조회 실패 (응답 코드: {response.status_code})")
            return []
        # 이 API는 표준 JSON이 아니라 EUC-KR 한글이 깨진 상태로 섞여 있어 response.json()이
        # 그대로 안 통한다. 대신 정규식(re.findall)으로 우리가 원하는 형태의 데이터 행만 직접
        # 골라낸다. 정규식 \["(\d{12})",\s*null,\s*null,\s*null,\s*(\d+),\s*\d+,\s*null\] 뜻:
        #   ["202607230905", null, null, null, 71000, ..., null] 같은 한 줄에서
        #   ( ) 로 감싼 두 부분 — 12자리 시각("YYYYMMDDHHMM")과 가격 숫자 — 만 뽑아온다.
        rows = re.findall(r'\["(\d{12})",\s*null,\s*null,\s*null,\s*(\d+),\s*\d+,\s*null\]', response.text)
        if not rows and re.search(r'\["\d{12}"', response.text):
            # 타임스탬프가 찍힌 행은 있는데 우리가 기대한 형식(위 정규식)과 안 맞는 경우 —
            # 장이 아직 안 열려서 데이터가 없는 것과는 다른, API 응답 형식 자체가 바뀐 상황일 수 있다.
            print(f"⚠️ [{code}] 당일 분봉 응답 형식이 예상과 달라 파싱하지 못했습니다. "
                  f"네이버 API 응답 형식이 바뀌었을 수 있습니다.")
        # rows는 [("202607230905", "71000"), ...] 같은 (시각, 가격) 튜플들의 리스트다.
        # 리스트 컴프리헨션으로 각 튜플을 {"time": "HHMM", "price": 정수} 딕셔너리로 바꾼다.
        # timestamp[8:]: 12자리 문자열("YYYYMMDDHHMM")에서 앞 8글자(날짜)를 잘라내고 뒤 4글자
        # (시:분)만 남긴다 — 문자열 슬라이싱.
        minutes = [{"time": timestamp[8:], "price": int(price)} for timestamp, price in rows]
        minutes.reverse()  # 응답이 최신순이라 시간 오름차순으로 뒤집는다
        return minutes
    except Exception as e:
        print(f"❌ [{code}] 당일 분봉 조회 중 오류 발생: {e}")
        return []


def send_telegram_message(text: str) -> bool:
    """텔레그램 sendMessage API로 텍스트 메시지를 전송합니다."""
    # 텔레그램 Bot API는 "https://api.telegram.org/bot{토큰}/{기능이름}" 형태의 URL로 호출한다.
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # parse_mode: "HTML"로 지정하면 text 안의 <b>굵게</b> 같은 간단한 HTML 태그가 실제로
    # 굵게/기울임 등으로 렌더링된다.
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, data=data, timeout=15)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ 텔레그램 메시지 전송 중 오류 발생: {e}")
        return False


def send_telegram_photo(photo_buffer: io.BytesIO, caption: str) -> bool:
    """텔레그램 sendPhoto API로 이미지를 전송합니다."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    # 파일 업로드는 requests의 files= 파라미터로 한다(멀티파트 폼 전송). 튜플의 세 값은
    # 각각 (파일이름, 파일 내용, MIME 타입)이다. photo_buffer는 디스크에 저장된 실제 파일이
    # 아니라 build_price_chart()가 메모리 안에 만들어둔 io.BytesIO(바이트 버퍼)인데, requests는
    # 이런 파일과 비슷하게 동작하는 객체("file-like object")도 그대로 파일처럼 다룰 수 있다.
    files = {"photo": ("chart.png", photo_buffer, "image/png")}
    # parse_mode="HTML": 캡션 안에 <b>...</b> 같은 HTML 태그를 서식(굵게 등)으로 해석하게 한다.
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    try:
        response = requests.post(url, data=data, files=files, timeout=15)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ 텔레그램 이미지 전송 중 오류 발생: {e}")
        return False


def describe_price_trend(daily_closes: list, intraday_minutes: list) -> str:
    """그래프에 어떤 데이터가 담겼는지 설명하는 문구를 반환합니다. 그래프를 보여주는 쪽이
    어디든 같은 문구를 쓰도록 함수로 빼 두었습니다. "최근 N일"이라고 말하려면 N을 세어줄 종가 히스토리가
    반드시 있어야 하므로 daily_closes를 먼저 확인합니다."""
    # daily_closes(과거 종가)가 있어야만 "최근 N일"이라고 말할 수 있다. 분봉(intraday_minutes)을
    # 먼저 보면, 히스토리가 하나도 없는 상태에서 장중에 실행됐을 때 len(daily_closes)가 0이라
    # "최근 0일 종가 + 현재가 추이"라는 이상한 문구가 나온다.
    if daily_closes:
        return f"최근 {len(daily_closes)}일 종가 + 현재가 추이"
    # 여기까지 왔다면 종가 히스토리가 아직 없는 상태다(과거 종가를 아직 어디에도 쌓아 두지
    # 않았거나, 새로 추가한 종목이라 쌓인 것이 없는 경우). 분봉이라도 있으면 오늘 하루치
    # 추이는 그려지므로 그렇게 안내한다.
    if intraday_minutes:
        return "오늘 현재가 추이 (종가 히스토리 누적 전)"
    # 히스토리도 분봉도 없으면 현재가 한 점만 찍힌다.
    return "현재가 (종가 히스토리 누적 전)"


def format_rate_badge(price: int, rate: float) -> str:
    """가격과 등락률(%)을 "가격원 세모이모지 부호율%" 형태의 문자열로 변환합니다
    (예: "254,000원 🔺 +1.2%", "254,000원 ▼ -0.5%"). 세모 이모지 바로 앞에 가격 숫자를
    붙입니다. 텔레그램 텍스트 메시지와 사진 캡션이 동일한 표기를 쓰도록 공용으로 뺐습니다."""
    prefix = f"{price:,}원"
    if rate > 0:
        return f"{prefix} 🔺 +{rate}%"
    if rate < 0:
        return f"{prefix} ▼ {rate}%"
    return f"{prefix} ▫️ 0.0%"


info = fetch_naver_current_price(STOCK_CODE)

if info is None:
    print("❌ 현재가를 가져오지 못했습니다. 네이버 API 상태를 확인해 주세요.")
else:
    # 두 줄짜리 메시지: 첫 줄은 이름과 종목코드, 둘째 줄은 4칸 들여쓴 가격·등락.
    telegram_message = (
        f"📈 {info['name']} ({STOCK_CODE})"
        f"\n    {format_rate_badge(info['price'], info['rate'])}"
    )

    if send_telegram_message(telegram_message):
        print("✅ 현재가 메시지를 텔레그램으로 전송했습니다!")
    else:
        print("❌ 현재가 메시지 전송에 실패했습니다.")
