"""
모니터링 현황 화면.
팀 선택 + 고객사 검색으로, 고객사별 모니터링 on/off 상태와
플랫폼별(카카오/네이버) 등록 여부, 평점/후기수를 한눈에 본다.

주의: 카카오/네이버 평점·후기수는 그 자리에서 실시간으로 가져오기 때문에,
고객사 수가 많으면 느릴 수 있다. 그래서 요청을 하나씩 순서대로 보내지 않고
동시에 병렬로 보내서 (ThreadPoolExecutor) 시간을 크게 줄였다. 결과는 세션 내
5분간 캐시된다.
"""
import streamlit as st
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from sheets_schema import ensure_schema, TEAMS, normalize_for_search
from collectors.kakao_summary import fetch_kakao_summary
from collectors.naver_summary import fetch_naver_summary
from style import inject_css, page_header

inject_css()
page_header("모니터링 현황")


@st.cache_data(ttl=30)
def _load_clients():
    client_ws, _, _ = ensure_schema()
    return client_ws.get_all_records()


@st.cache_data(ttl=300)  # 외부 API 호출이라 5분 캐시
def _get_kakao_stat(kakao_id):
    if not kakao_id:
        return None
    return fetch_kakao_summary(kakao_id)


@st.cache_data(ttl=300)
def _get_naver_stat(naver_id):
    if not naver_id:
        return None
    return fetch_naver_summary(naver_id)


def _fetch_one(client):
    """고객사 한 곳의 카카오+네이버 정보를 가져온다 (스레드에서 실행됨)."""
    kakao_id = str(client.get("카카오_장소ID", "")).strip()
    naver_id = str(client.get("네이버_플레이스ID", "")).strip()
    is_active = str(client.get("활성여부", "")).strip().upper() == "TRUE"

    kakao_stat = _get_kakao_stat(kakao_id) if kakao_id else None
    naver_stat = _get_naver_stat(naver_id) if naver_id else None

    if kakao_stat and kakao_stat.get("후기미제공"):
        kakao_rating_display = "후기 미제공"
        kakao_count_display = "후기 미제공"
    elif kakao_stat:
        kakao_rating_display = kakao_stat["평균별점"] if kakao_stat.get("평균별점") is not None else "-"
        kakao_count_display = kakao_stat["리뷰총개수"]
    else:
        kakao_rating_display = "-"
        kakao_count_display = "-"

    return {
        "모니터링": "🔵 ON" if is_active else "⚪ OFF",
        "고객사명": client.get("고객사명", ""),
        "담당부서": client.get("담당부서", ""),
        "담당자": client.get("담당자", ""),
        "카카오": "🟡 등록" if kakao_id else "⚫ 미등록",
        "카카오평점": kakao_rating_display,
        "카카오후기수": kakao_count_display,
        "네이버": "🟢 등록" if naver_id else "⚫ 미등록",
        "네이버평점": naver_stat["평균별점"] if naver_stat and naver_stat.get("평균별점") is not None else "-",
        "네이버후기수": naver_stat["리뷰총개수"] if naver_stat else "-",
    }


col_team, col_search = st.columns([1, 2])
with col_team:
    selected_team = st.selectbox("팀 선택", ["전체"] + TEAMS)
with col_search:
    search_text = st.text_input("고객사 / 담당자 검색", placeholder="고객사명 또는 담당자 이름 입력")

clients = _load_clients()

if not clients:
    st.info("등록된 고객사가 없습니다.")
else:
    filtered = clients
    if selected_team != "전체":
        filtered = [c for c in filtered if c.get("담당부서") == selected_team]
    if search_text.strip():
        q = normalize_for_search(search_text)
        filtered = [
            c for c in filtered
            if q in normalize_for_search(c.get("고객사명", "")) or q in normalize_for_search(c.get("담당자", ""))
        ]

    if not filtered:
        st.info("조건에 맞는 고객사가 없습니다.")
    else:
        rows = []
        progress = st.progress(0, text=f"평점/후기수 불러오는 중... (0/{len(filtered)})")

        # 동시에 최대 15개씩 병렬로 요청 (순서대로 하나씩 기다리지 않음)
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(_fetch_one, c): c for c in filtered}
            done_count = 0
            for future in as_completed(futures):
                rows.append(future.result())
                done_count += 1
                progress.progress(
                    done_count / len(filtered),
                    text=f"평점/후기수 불러오는 중... ({done_count}/{len(filtered)})",
                )

        progress.empty()

        df = pd.DataFrame(rows).sort_values("고객사명").reset_index(drop=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
