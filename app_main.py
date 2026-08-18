"""
Main 대시보드.
전체 고객사 수 / 모니터링 중인 고객사 수 / 미확인 부정 리뷰 수를 보여주고,
팀 선택 필터로 특정 팀 기준으로도 볼 수 있다.
"""
import streamlit as st
import pandas as pd
from sheets_schema import ensure_schema, TEAMS, REVIEW_SHEET, open_spreadsheet
from style import inject_css, page_header

inject_css()
page_header("고객사 리뷰 모니터링", "카카오맵 원문 수집 + 네이버 리뷰수/별점 변화 추적")


@st.cache_data(ttl=30)
def _load_clients_df():
    client_ws, _, _ = ensure_schema()
    return pd.DataFrame(client_ws.get_all_records())


@st.cache_data(ttl=30)
def _load_reviews_df():
    sh = open_spreadsheet()
    ws = sh.worksheet(REVIEW_SHEET)
    return pd.DataFrame(ws.get_all_records())


clients_df = _load_clients_df()
reviews_df = _load_reviews_df()

team_options = ["전체"] + TEAMS
selected_team = st.selectbox("팀 선택", team_options)

if not clients_df.empty:
    filtered_clients = clients_df if selected_team == "전체" else clients_df[clients_df["담당부서"] == selected_team]
else:
    filtered_clients = clients_df

# 미확인 부정 리뷰 계산 (고객사명 기준으로 팀 매핑)
if not reviews_df.empty and not clients_df.empty:
    team_map = dict(zip(clients_df["고객사명"], clients_df.get("담당부서", "")))
    reviews_df["담당부서"] = reviews_df["고객사명"].map(team_map)
    unconfirmed_negative = reviews_df[
        (reviews_df["status"] == "신규") & (reviews_df["is_negative"].astype(str).str.upper() == "TRUE")
    ]
    if selected_team != "전체":
        unconfirmed_negative = unconfirmed_negative[unconfirmed_negative["담당부서"] == selected_team]
else:
    unconfirmed_negative = pd.DataFrame()

col1, col2, col3 = st.columns(3)
col1.metric("전체 고객사 수", len(filtered_clients))
col2.metric(
    "모니터링 중인 고객사",
    len(filtered_clients[filtered_clients["활성여부"].astype(str).str.upper() == "TRUE"]) if not filtered_clients.empty else 0,
)
col3.metric("미확인 부정 리뷰", len(unconfirmed_negative))

st.divider()

st.subheader("팀별 미확인 부정 리뷰")
if not reviews_df.empty and not clients_df.empty:
    all_unconfirmed = reviews_df[
        (reviews_df["status"] == "신규") & (reviews_df["is_negative"].astype(str).str.upper() == "TRUE")
    ]
    if all_unconfirmed.empty:
        st.info("현재 미확인 부정 리뷰가 없습니다.")
    else:
        team_counts = all_unconfirmed.groupby("담당부서").size().reset_index(name="건수")
        for team in TEAMS:
            count = team_counts[team_counts["담당부서"] == team]["건수"].sum() if not team_counts.empty else 0
            if count > 0:
                st.markdown(f"**{team}** — {count}건")
else:
    st.info("아직 데이터가 없습니다. '고객사 등록' 메뉴에서 먼저 고객사를 등록해주세요.")
