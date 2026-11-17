
from datetime import datetime
from stock_utils import (
    KST,
    read_watchlist,
    update_price_history,
    fetch_naver_current_price,
    get_turso_client,
)
watchlist_codes = read_watchlist()
if watchlist_codes is None:
    exit()
print(f"🚀 종가 수집 시작... (실행 시각 {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST)")
collected = 0
with get_turso_client() as client:
    for code in watchlist_codes:
        info = fetch_naver_current_price(code)
        if info is None:
            continue
        if info["is_open"]:
            print(f"⚠️⚠️ [{code}]  아직 장중입니다. 확정되지 않은 가격이라 기록하지 않습니다.")
            continue
        traded_at = info.get("traded_at") or ""
        if len(traded_at) >= 10:
            date_str = traded_at[:10].replace("-", "")
        else:
            date_str = datetime.now(KST).strftime("%Y%m%d")
            print(f"⚠️⚠️ [{code}]  체결 시각을 읽지 못해 실행 시각 기준으로 날짜를 정합니다.")
        update_price_history(code, date_str, info["price"], client=client)
        collected += 1
        print(f"✅ [{code}] {info['name']} {date_str} 종가 {info['price']:,}원 기록")
if collected == 0:
    print("❌ 기록된 종가가 하나도 없습니다.")
    exit()
print(f"🎉 종가 히스토리를 Turso DB에 저장했습니다 ({collected}/{len(watchlist_codes)}종목).")