# -*- coding: utf-8 -*-
"""
유튜브 떡상 수집기 (홈페이지용)
- 한국/미국을 요일별로 번갈아 수집 (쿼터 절약)
- 11개 카테고리를 각각 여러 키워드로 검색 → 정확도 향상
- 구독자 1만 이하 채널만 필터링, 떡상 점수 계산
- 결과를 docs/data/{국가}.json 으로 저장 (홈페이지가 읽음)
- 구독자 스냅샷을 저장해 급증 채널 탐지

필요한 환경변수:
  YT_API_KEY        : YouTube Data API v3 키
선택:
  FORCE_REGION      : KR 또는 US (요일 자동선택 대신 강제 지정, 테스트용)
  MAX_SUBSCRIBERS   : 구독자 상한 (기본 10000)
  LOOKBACK_HOURS    : 검색 기간 (기본 48)
  TOP_N_PER_CATEGORY: 카테고리별 상위 개수 (기본 8)
"""

import os
import json
import math
from datetime import datetime, timedelta, timezone

import requests

API_KEY = os.environ["YT_API_KEY"]
MAX_SUBS = int(os.environ.get("MAX_SUBSCRIBERS", "10000"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))
TOP_N = int(os.environ.get("TOP_N_PER_CATEGORY", "8"))

# 카테고리별 검색 키워드. 유튜브 공식 카테고리에 없는 분류라 키워드로 잡는다.
# 키워드를 늘리면 정확도는 오르지만 쿼터를 더 쓴다 (키워드 1개 = 검색 100유닛).
CATEGORIES = {
    "경제":        {"KR": ["경제", "주식", "부동산", "재테크", "투자"],
                   "US": ["economy", "stock market", "investing", "personal finance"]},
    "건강":        {"KR": ["건강", "운동", "다이어트", "영양"],
                   "US": ["health", "workout", "nutrition", "fitness"]},
    "역사":        {"KR": ["역사", "한국사", "세계사", "역사스토리"],
                   "US": ["history", "world history", "history documentary"]},
    "야담":        {"KR": ["야담", "괴담", "미스터리", "사연", "썰"],
                   "US": ["mystery", "creepy story", "unsolved", "scary story"]},
    "스포츠":      {"KR": ["스포츠", "축구", "야구", "하이라이트"],
                   "US": ["sports", "highlights", "basketball", "football"]},
    "플레이리스트": {"KR": ["플레이리스트", "노래모음", "잔잔한노래", "감성"],
                   "US": ["playlist", "music mix", "chill music", "lofi"]},
    "심리":        {"KR": ["심리", "심리학", "심리테스트", "마음"],
                   "US": ["psychology", "mindset", "personality test", "mental"]},
    "연애":        {"KR": ["연애", "썸", "이별", "소개팅", "연애상담"],
                   "US": ["dating advice", "relationship", "breakup", "love advice"]},
    "영화드라마":   {"KR": ["영화리뷰", "드라마리뷰", "영화해석", "결말포함"],
                   "US": ["movie review", "film analysis", "movie recap", "ending explained"]},
    "예능":        {"KR": ["예능", "웃긴영상", "챌린지", "몰카"],
                   "US": ["funny video", "challenge", "prank", "comedy skit"]},
    "기타":        {"KR": ["인기급상승", "화제영상"],
                   "US": ["trending", "viral video"]},
}

# 요일별 국가 배정 (0=월 ... 6=일). 한국/미국을 격일로.
DAY_REGION = {0: "KR", 1: "US", 2: "KR", 3: "US", 4: "KR", 5: "US", 6: "KR"}

MIN_VIEWS = 5000
MIN_MULTIPLIER = 5.0
SUB_GROWTH_ALERT = 0.20

API_BASE = "https://www.googleapis.com/youtube/v3"
KST = timezone(timedelta(hours=9))
DOCS_DIR = "docs"
DATA_DIR = os.path.join(DOCS_DIR, "data")


def api_get(endpoint, params):
    params["key"] = API_KEY
    r = requests.get(f"{API_BASE}/{endpoint}", params=params, timeout=30)
    if r.status_code == 403:
        raise RuntimeError(f"API 403 (쿼터 초과 가능성): {r.text[:300]}")
    r.raise_for_status()
    return r.json()


def search_keyword(keyword, region_code, published_after):
    """키워드 1개로 검색 (100유닛). 실패 원인은 로그로 출력."""
    params = {
        "part": "snippet",
        "type": "video",
        "order": "viewCount",
        "q": keyword,
        "publishedAfter": published_after,
        "regionCode": region_code,
        "relevanceLanguage": "ko" if region_code == "KR" else "en",
        "maxResults": 50,
    }
    try:
        data = api_get("search", params)
        return [it["id"]["videoId"] for it in data.get("items", []) if it["id"].get("videoId")]
    except Exception as e:
        print(f"    [검색오류] '{keyword}' ({region_code}): {e}")
        return []


def chunked(lst, n=50):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def fetch_video_details(video_ids):
    videos = {}
    for chunk in chunked(list(video_ids)):
        data = api_get("videos", {"part": "snippet,statistics", "id": ",".join(chunk), "maxResults": 50})
        for item in data.get("items", []):
            stats = item.get("statistics", {})
            snip = item.get("snippet", {})
            thumbs = snip.get("thumbnails", {})
            thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            videos[item["id"]] = {
                "video_id": item["id"],
                "title": snip.get("title", ""),
                "channel_id": snip.get("channelId", ""),
                "channel_title": snip.get("channelTitle", ""),
                "published_at": snip.get("publishedAt", ""),
                "thumb": thumb,
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0) or 0),
            }
    return videos


def fetch_channel_stats(channel_ids):
    channels = {}
    for chunk in chunked(list(channel_ids)):
        data = api_get("channels", {"part": "statistics,snippet", "id": ",".join(chunk), "maxResults": 50})
        for item in data.get("items", []):
            stats = item.get("statistics", {})
            hidden = stats.get("hiddenSubscriberCount", False)
            channels[item["id"]] = {
                "title": item.get("snippet", {}).get("title", ""),
                "subs": None if hidden else int(stats.get("subscriberCount", 0)),
            }
    return channels


def score_video(video, subs):
    published = datetime.fromisoformat(video["published_at"].replace("Z", "+00:00"))
    hours = max((datetime.now(timezone.utc) - published).total_seconds() / 3600, 1.0)
    vph = video["views"] / hours
    multiplier = video["views"] / max(subs, 1)
    score = vph * math.log10(multiplier + 1)
    return {"vph": round(vph), "multiplier": round(multiplier, 1), "hours": round(hours), "score": score}


# ---- 구독자 급증 히스토리 ----
def history_path(region):
    return os.path.join(DATA_DIR, f"history_{region}.json")


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def update_growth(region, channels):
    history = load_json(history_path(region), {})
    today = datetime.now(KST).strftime("%Y-%m-%d")
    alerts = []
    for cid, ch in channels.items():
        if ch["subs"] is None:
            continue
        entry = history.setdefault(cid, {"title": ch["title"], "snapshots": []})
        entry["title"] = ch["title"]
        snaps = entry["snapshots"]
        prev = next((s for s in reversed(snaps) if s["date"] != today), None)
        if prev and prev["subs"] >= 100:
            g = (ch["subs"] - prev["subs"]) / prev["subs"]
            if g >= SUB_GROWTH_ALERT:
                alerts.append({"channel_id": cid, "title": ch["title"],
                               "prev_subs": prev["subs"], "now_subs": ch["subs"],
                               "growth": round(g * 100), "since": prev["date"]})
        if not snaps or snaps[-1]["date"] != today:
            snaps.append({"date": today, "subs": ch["subs"]})
        else:
            snaps[-1]["subs"] = ch["subs"]
    for cid in history:
        history[cid]["snapshots"] = history[cid]["snapshots"][-30:]
    save_json(history_path(region), history)
    alerts.sort(key=lambda x: x["growth"], reverse=True)
    return alerts


def main():
    region = os.environ.get("FORCE_REGION", "").upper()
    if region not in ("KR", "US"):
        region = DAY_REGION[datetime.now(KST).weekday()]

    published_after = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")

    total_kw = sum(len(v[region]) for v in CATEGORIES.values())
    print(f"오늘의 국가: {region} | 카테고리 {len(CATEGORIES)}개 | 총 키워드 {total_kw}개 "
          f"| 예상 검색 쿼터: 약 {total_kw * 100}유닛")

    # 1) 카테고리별 검색
    cat_video_ids = {}
    all_ids = set()
    for cat, kwmap in CATEGORIES.items():
        ids = set()
        for kw in kwmap[region]:
            print(f"  검색: [{region}] {cat} / '{kw}'")
            ids.update(search_keyword(kw, region, published_after))
        cat_video_ids[cat] = ids
        all_ids.update(ids)

    if not all_ids:
        print("검색 결과 없음. 종료.")
        return

    # 2) 상세 조회
    print(f"영상 {len(all_ids)}개 상세 조회...")
    videos = fetch_video_details(all_ids)
    channel_ids = {v["channel_id"] for v in videos.values() if v["channel_id"]}
    print(f"채널 {len(channel_ids)}개 조회...")
    channels = fetch_channel_stats(channel_ids)

    # 3) 필터 + 점수 → 카테고리별 상위
    result_categories = {}
    tracked = {}
    for cat, ids in cat_video_ids.items():
        candidates = []
        seen = set()
        for vid in ids:
            v = videos.get(vid)
            if not v or vid in seen:
                continue
            seen.add(vid)
            ch = channels.get(v["channel_id"])
            if not ch or ch["subs"] is None or ch["subs"] > MAX_SUBS:
                continue
            m = score_video(v, ch["subs"])
            if v["views"] >= MIN_VIEWS or m["multiplier"] >= MIN_MULTIPLIER:
                tracked[v["channel_id"]] = ch
                candidates.append({
                    "video_id": v["video_id"], "title": v["title"],
                    "channel_title": v["channel_title"], "channel_id": v["channel_id"],
                    "subs": ch["subs"], "views": v["views"], "thumb": v["thumb"],
                    "vph": m["vph"], "multiplier": m["multiplier"], "hours": m["hours"],
                })
        candidates.sort(key=lambda x: x["vph"] * math.log10(x["multiplier"] + 1), reverse=True)
        result_categories[cat] = candidates[:TOP_N]

    # 4) 급증 채널
    growth = update_growth(region, tracked)

    # 5) 저장
    out = {
        "region": region,
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "max_subs": MAX_SUBS,
        "lookback_hours": LOOKBACK_HOURS,
        "categories": result_categories,
        "growth": growth,
    }
    save_json(os.path.join(DATA_DIR, f"{region}.json"), out)

    total = sum(len(v) for v in result_categories.values())
    print(f"저장 완료: {region}.json | 영상 {total}개, 급증채널 {len(growth)}개")


if __name__ == "__main__":
    main()
