# -*- coding: utf-8 -*-
"""
유튜브 떡상 탐지기 (소형 채널 전용)
- 최근 48시간 내 업로드된 영상을 카테고리/국가별로 검색
- 구독자 1만 이하 채널만 필터링
- 시간당 조회수, 조회수/구독자 배수로 떡상 점수 계산
- 채널 구독자 스냅샷을 저장해 구독자 급증 채널 탐지
- 결과를 이메일로 발송
"""

import os
import sys
import json
import math
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

import requests

API_KEY = os.environ["YT_API_KEY"]
REGION_CODES = [r.strip().upper() for r in os.environ.get("REGION_CODES", "KR").split(",") if r.strip()]
MAX_SUBS = int(os.environ.get("MAX_SUBSCRIBERS", "10000"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))
TOP_N = int(os.environ.get("TOP_N_PER_CATEGORY", "5"))

CATEGORIES = {
    "10": "음악",
    "20": "게임",
    "22": "인물/브이로그",
    "23": "코미디",
    "24": "엔터테인먼트",
    "26": "노하우/스타일",
    "27": "교육",
    "28": "과학기술",
}

MIN_VIEWS = 5000
MIN_MULTIPLIER = 5.0
SUB_GROWTH_ALERT = 0.20

HISTORY_FILE = "channel_history.json"
API_BASE = "https://www.googleapis.com/youtube/v3"

KST = timezone(timedelta(hours=9))


def api_get(endpoint, params):
    params["key"] = API_KEY
    r = requests.get(f"{API_BASE}/{endpoint}", params=params, timeout=30)
    if r.status_code == 403:
        raise RuntimeError(f"API 403 오류 (쿼터 초과 가능성): {r.text[:300]}")
    r.raise_for_status()
    return r.json()


def _do_search(params):
    """검색 1회 실행. 성공하면 videoId 리스트, 실패하면 예외 그대로 올림."""
    data = api_get("search", params)
    ids = [item["id"]["videoId"] for item in data.get("items", []) if item["id"].get("videoId")]
    return ids


def search_videos(category_id, region_code, published_after):
    """카테고리+국가별 최근 영상 검색.
    1차: 카테고리 필터로 검색. 0개면
    2차: 카테고리 키워드로 일반 검색(우회). 이래도 0개면 그대로 0개.
    실패 원인은 로그에 그대로 출력한다."""
    base = {
        "part": "snippet",
        "type": "video",
        "order": "viewCount",
        "publishedAfter": published_after,
        "regionCode": region_code,
        "relevanceLanguage": "ko",
        "maxResults": 50,
    }

    # 1차: 카테고리 ID 필터
    try:
        ids = _do_search({**base, "videoCategoryId": category_id})
        if ids:
            return ids
        print(f"  [알림] 카테고리 필터로 0개 → 키워드 검색으로 재시도")
    except Exception as e:
        print(f"  [검색 오류] 카테고리 {category_id}, {region_code}: {e}")

    # 2차 우회: 카테고리 이름을 검색어로 사용
    keyword = CATEGORIES.get(category_id, "")
    try:
        ids = _do_search({**base, "q": keyword})
        if not ids:
            print(f"  [알림] 키워드 '{keyword}' 검색도 0개")
        return ids
    except Exception as e:
        print(f"  [검색 오류/우회] '{keyword}', {region_code}: {e}")
        return []


def chunked(lst, n=50):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def fetch_video_details(video_ids):
    """영상 상세 정보 (1유닛/호출, 50개씩)"""
    videos = {}
    for chunk in chunked(list(video_ids)):
        data = api_get("videos", {
            "part": "snippet,statistics",
            "id": ",".join(chunk),
            "maxResults": 50,
        })
        for item in data.get("items", []):
            stats = item.get("statistics", {})
            snip = item.get("snippet", {})
            videos[item["id"]] = {
                "video_id": item["id"],
                "title": snip.get("title", ""),
                "channel_id": snip.get("channelId", ""),
                "channel_title": snip.get("channelTitle", ""),
                "published_at": snip.get("publishedAt", ""),
                "category_id": snip.get("categoryId", ""),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0) or 0),
            }
    return videos


def fetch_channel_stats(channel_ids):
    """채널 구독자 수 (1유닛/호출, 50개씩)"""
    channels = {}
    for chunk in chunked(list(channel_ids)):
        data = api_get("channels", {
            "part": "statistics,snippet",
            "id": ",".join(chunk),
            "maxResults": 50,
        })
        for item in data.get("items", []):
            stats = item.get("statistics", {})
            hidden = stats.get("hiddenSubscriberCount", False)
            channels[item["id"]] = {
                "title": item.get("snippet", {}).get("title", ""),
                "subs": None if hidden else int(stats.get("subscriberCount", 0)),
                "total_views": int(stats.get("viewCount", 0)),
            }
    return channels


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(history):
    for cid in history:
        history[cid]["snapshots"] = history[cid]["snapshots"][-30:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)


def update_history_and_detect_growth(history, channels):
    """오늘 스냅샷을 저장하고, 이전 스냅샷 대비 급증 채널을 반환"""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    growth_alerts = []

    for cid, ch in channels.items():
        if ch["subs"] is None:
            continue
        entry = history.setdefault(cid, {"title": ch["title"], "snapshots": []})
        entry["title"] = ch["title"]
        snaps = entry["snapshots"]

        prev = None
        for s in reversed(snaps):
            if s["date"] != today:
                prev = s
                break

        if prev and prev["subs"] >= 100:
            growth = (ch["subs"] - prev["subs"]) / prev["subs"]
            if growth >= SUB_GROWTH_ALERT:
                growth_alerts.append({
                    "channel_id": cid,
                    "title": ch["title"],
                    "prev_subs": prev["subs"],
                    "now_subs": ch["subs"],
                    "growth": growth,
                    "since": prev["date"],
                })

        if not snaps or snaps[-1]["date"] != today:
            snaps.append({"date": today, "subs": ch["subs"]})
        else:
            snaps[-1]["subs"] = ch["subs"]

    growth_alerts.sort(key=lambda x: x["growth"], reverse=True)
    return growth_alerts


def score_video(video, subs):
    published = datetime.fromisoformat(video["published_at"].replace("Z", "+00:00"))
    hours = max((datetime.now(timezone.utc) - published).total_seconds() / 3600, 1.0)
    vph = video["views"] / hours
    multiplier = video["views"] / max(subs, 1)
    score = vph * math.log10(multiplier + 1)
    return {"vph": vph, "multiplier": multiplier, "hours": hours, "score": score}


def fmt(n):
    if n >= 10000:
        return f"{n/10000:.1f}만"
    return f"{n:,}"


def build_email_html(results_by_region, growth_alerts):
    today = datetime.now(KST).strftime("%Y년 %m월 %d일")
    parts = [f"""
    <div style="font-family:'Apple SD Gothic Neo',sans-serif;max-width:640px;margin:auto;color:#222">
    <h2 style="border-bottom:3px solid #ff0000;padding-bottom:8px">🚀 유튜브 떡상 리포트 <span style="font-size:14px;color:#888">{today}</span></h2>
    <p style="color:#666;font-size:13px">구독자 {fmt(MAX_SUBS)} 이하 채널 · 최근 {LOOKBACK_HOURS}시간 내 업로드 기준</p>
    """]

    for region, categories in results_by_region.items():
        parts.append(f'<h3 style="background:#f5f5f5;padding:8px 12px;border-radius:6px">🌍 {region}</h3>')
        has_any = False
        for cat_name, videos in categories.items():
            if not videos:
                continue
            has_any = True
            parts.append(f'<h4 style="margin:16px 0 8px;color:#cc0000">▶ {cat_name}</h4>')
            for v in videos:
                url = f"https://www.youtube.com/watch?v={v['video_id']}"
                parts.append(f"""
                <div style="border:1px solid #e5e5e5;border-radius:8px;padding:12px;margin-bottom:10px">
                  <a href="{url}" style="font-weight:bold;color:#1a0dab;text-decoration:none;font-size:15px">{v['title']}</a>
                  <div style="font-size:13px;color:#555;margin-top:6px">
                    채널: {v['channel_title']} (구독자 {fmt(v['subs'])})<br>
                    조회수 <b>{fmt(v['views'])}</b> · 시간당 {fmt(int(v['vph']))}회 ·
                    구독자 대비 <b style="color:#cc0000">{v['multiplier']:.1f}배</b> ·
                    업로드 {v['hours']:.0f}시간 전
                  </div>
                </div>""")
        if not has_any:
            parts.append('<p style="color:#999;font-size:13px">오늘은 기준을 넘는 영상이 없었습니다.</p>')

    if growth_alerts:
        parts.append('<h3 style="background:#fff3e0;padding:8px 12px;border-radius:6px">📈 구독자 급증 채널</h3>')
        for g in growth_alerts[:10]:
            url = f"https://www.youtube.com/channel/{g['channel_id']}"
            parts.append(f"""
            <div style="border:1px solid #ffe0b2;border-radius:8px;padding:10px;margin-bottom:8px;font-size:13px">
              <a href="{url}" style="font-weight:bold;color:#1a0dab;text-decoration:none">{g['title']}</a><br>
              {g['since']} 이후: {fmt(g['prev_subs'])} → <b>{fmt(g['now_subs'])}</b>
              (<b style="color:#e65100">+{g['growth']*100:.0f}%</b>)
            </div>""")
    else:
        parts.append('<p style="color:#999;font-size:13px">📈 구독자 급증 채널: 아직 비교할 이전 데이터가 없거나 해당 없음 (데이터가 쌓이면 자동으로 탐지됩니다)</p>')

    parts.append('<p style="color:#bbb;font-size:11px;margin-top:24px">자동 발송 · yt-rising-detector</p></div>')
    return "".join(parts)


def send_email(html):
    sender = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipients = [e.strip() for e in os.environ["TO_EMAIL"].split(",")]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚀 유튜브 떡상 리포트 {datetime.now(KST).strftime('%m/%d')}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())
    print(f"이메일 발송 완료 → {recipients}")


def main():
    published_after = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")

    quota_estimate = len(CATEGORIES) * len(REGION_CODES) * 100
    print(f"국가: {REGION_CODES} | 카테고리 {len(CATEGORIES)}개 | 예상 검색 쿼터: 약 {quota_estimate}유닛")
    if quota_estimate > 9000:
        print("⚠️ 경고: 하루 기본 쿼터(10,000)에 근접합니다. 카테고리나 국가 수를 줄이세요.")

    all_video_ids = set()
    region_cat_videos = {}
    for region in REGION_CODES:
        region_cat_videos[region] = {}
        for cat_id, cat_name in CATEGORIES.items():
            print(f"검색 중: [{region}] {cat_name}")
            ids = search_videos(cat_id, region, published_after)
            region_cat_videos[region][cat_id] = ids
            all_video_ids.update(ids)

    if not all_video_ids:
        print("검색 결과 없음. 종료합니다.")
        return

    print(f"영상 {len(all_video_ids)}개 상세 조회 중...")
    videos = fetch_video_details(all_video_ids)
    channel_ids = {v["channel_id"] for v in videos.values() if v["channel_id"]}
    print(f"채널 {len(channel_ids)}개 조회 중...")
    channels = fetch_channel_stats(channel_ids)

    results_by_region = {}
    small_channel_ids = set()
    for region, cats in region_cat_videos.items():
        results_by_region[region] = {}
        for cat_id, vid_ids in cats.items():
            cat_name = CATEGORIES[cat_id]
            candidates = []
            for vid in vid_ids:
                v = videos.get(vid)
                if not v:
                    continue
                ch = channels.get(v["channel_id"])
                if not ch or ch["subs"] is None or ch["subs"] > MAX_SUBS:
                    continue
                m = score_video(v, ch["subs"])
                if v["views"] >= MIN_VIEWS or m["multiplier"] >= MIN_MULTIPLIER:
                    small_channel_ids.add(v["channel_id"])
                    candidates.append({**v, "subs": ch["subs"], **m})
            candidates.sort(key=lambda x: x["score"], reverse=True)
            results_by_region[region][cat_name] = candidates[:TOP_N]

    history = load_history()
    tracked = {cid: channels[cid] for cid in small_channel_ids if cid in channels}
    old_tracked = [cid for cid in history if cid not in tracked]
    if old_tracked:
        print(f"기존 추적 채널 {len(old_tracked)}개 재조회 중...")
        tracked.update(fetch_channel_stats(old_tracked[:500]))
    growth_alerts = update_history_and_detect_growth(history, tracked)
    save_history(history)

    total = sum(len(v) for cats in results_by_region.values() for v in cats.values())
    print(f"최종 선정: 영상 {total}개, 급증 채널 {len(growth_alerts)}개")
    html = build_email_html(results_by_region, growth_alerts)
    send_email(html)


if __name__ == "__main__":
    main()
