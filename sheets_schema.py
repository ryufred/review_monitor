"""
Google Sheets 연결 및 스키마(탭/헤더) 관리.
기존 프로젝트의 GCP 서비스 계정 인증 방식을 그대로 사용합니다.
"""
import os
import json
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

CLIENT_SHEET = "고객사설정"
CLIENT_HEADERS = ["고객사명", "활성여부", "구글_PlaceID", "카카오_장소ID", "네이버_플레이스ID", "담당부서", "담당자"]

TEAMS = ["본사1팀", "본사2팀", "마포지사", "브마팀", "경영지원팀", "중부지사", "부산지사", "서초지사"]


def normalize_for_search(text: str) -> str:
    """검색/비교용 정규화. 앞뒤/중간 공백을 다 무시해서 '프레드'와 '프레드 '를
    같은 사람으로 취급한다."""
    return (text or "").replace(" ", "").strip()

SUMMARY_HISTORY_SHEET = "리뷰통계이력"
SUMMARY_HISTORY_HEADERS = ["날짜", "고객사명", "플랫폼", "리뷰총개수", "평균별점"]

REVIEW_SHEET = "리뷰마스터"
REVIEW_HEADERS = [
    "리뷰ID", "고객사명", "플랫폼", "별점", "리뷰내용", "작성자",
    "작성일", "수집일시", "is_negative", "판단근거", "status",
]


def _get_secret(key, default=None):
    """GitHub Actions(환경변수)와 Streamlit Cloud(st.secrets) 양쪽 다 지원."""
    value = os.environ.get(key)
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(key, default)
    except Exception:
        return default


def get_client():
    """서비스 계정 인증. 환경변수 GCP_SERVICE_ACCOUNT_JSON(문자열) 우선,
    없으면 로컬 파일 경로 GCP_SERVICE_ACCOUNT_FILE을 사용."""
    raw = _get_secret("GCP_SERVICE_ACCOUNT_JSON")
    if raw:
        info = json.loads(raw)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        path = _get_secret("GCP_SERVICE_ACCOUNT_FILE", "service_account.json")
        creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    return gspread.authorize(creds)


def open_spreadsheet():
    gc = get_client()
    spreadsheet_id = _get_secret("SPREADSHEET_ID")
    return gc.open_by_key(spreadsheet_id)


def _ensure_sheet(sh, title, headers):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers) + 2)
        ws.append_row(headers)
        return ws

    existing_headers = ws.row_values(1)
    if not existing_headers:
        ws.append_row(headers)
        return ws

    # 이미 있는 시트인데 새로 추가된 헤더(컬럼)가 빠져있으면 뒤에 이어붙임
    missing = [h for h in headers if h not in existing_headers]
    if missing:
        new_headers = existing_headers + missing
        ws.update("A1", [new_headers])

    return ws


def ensure_schema(sh=None):
    """고객사설정 / 리뷰마스터 / 리뷰통계이력 탭이 없으면 생성."""
    sh = sh or open_spreadsheet()
    client_ws = _ensure_sheet(sh, CLIENT_SHEET, CLIENT_HEADERS)
    review_ws = _ensure_sheet(sh, REVIEW_SHEET, REVIEW_HEADERS)
    summary_history_ws = _ensure_sheet(sh, SUMMARY_HISTORY_SHEET, SUMMARY_HISTORY_HEADERS)
    return client_ws, review_ws, summary_history_ws


def load_active_clients(client_ws):
    """활성여부=TRUE인 고객사 목록을 dict 리스트로 반환."""
    rows = client_ws.get_all_records()
    active = []
    for row in rows:
        flag = str(row.get("활성여부", "")).strip().upper()
        if flag in ("TRUE", "1", "Y", "YES"):
            active.append(row)
    return active


def append_summary_history(history_ws, date_str: str, client_name: str, platform: str, review_count: int, avg_rating: float):
    """오늘자 통계 스냅샷을 이력 시트에 한 줄 추가 (구글/네이버 공통)."""
    history_ws.append_row(
        [date_str, client_name, platform, review_count, avg_rating],
        value_input_option="USER_ENTERED",
    )


def get_summary_history(history_ws, client_name: str, platform: str) -> list[dict]:
    """특정 고객사+플랫폼의 전체 이력을 날짜순으로 정렬해서 반환.
    각 항목: {"날짜": "2026-07-28", "리뷰총개수": 863, "평균별점": 4.08}
    """
    rows = history_ws.get_all_records()
    client_rows = [
        r for r in rows
        if r.get("고객사명", "").strip() == client_name.strip()
        and r.get("플랫폼", "").strip() == platform
    ]
    client_rows.sort(key=lambda r: r.get("날짜", ""))
    return client_rows


def get_latest_summary_entry_before(history_ws, client_name: str, platform: str, today_str: str) -> dict | None:
    """오늘(today_str)보다 이전 날짜 중 가장 최근 기록을 반환. 없으면 None.
    (주말 등으로 며칠 비어도 '가장 최근' 기준이라 정상 비교됨)
    """
    history = get_summary_history(history_ws, client_name, platform)
    prior = [r for r in history if r.get("날짜", "") < today_str]
    return prior[-1] if prior else None


def has_summary_entry_for_today(history_ws, client_name: str, platform: str, today_str: str) -> bool:
    """오늘 이미 기록을 남겼는지 확인 (같은 날 중복 실행 방지용)."""
    history = get_summary_history(history_ws, client_name, platform)
    return any(r.get("날짜", "") == today_str for r in history)


def load_existing_review_ids(review_ws):
    """중복 체크용으로 기존에 저장된 리뷰ID 집합을 반환."""
    ids = review_ws.col_values(1)  # 첫 컬럼 = 리뷰ID
    return set(ids[1:])  # 헤더 제외


# ── 고객사 CRUD (앱에서 직접 추가/수정/삭제할 때 사용) ──────────────────

def add_client(client_ws, name, google_id="", kakao_id="", naver_id="", active=True, team="", manager=""):
    """고객사를 새로 추가. 이미 같은 이름이 있으면 False 반환."""
    existing_names = [n.strip() for n in client_ws.col_values(1)[1:]]
    if name.strip() in existing_names:
        return False
    client_ws.append_row(
        [name.strip(), "TRUE" if active else "FALSE", google_id.strip(), kakao_id.strip(), naver_id.strip(),
         team.strip(), manager.strip()],
        value_input_option="USER_ENTERED",
    )
    return True


def _find_client_row(client_ws, name: str):
    names = client_ws.col_values(1)  # 첫 컬럼 = 고객사명
    for row_idx, n in enumerate(names[1:], start=2):
        if n.strip() == name.strip():
            return row_idx
    return None


def update_client(client_ws, original_name, name=None, google_id=None, kakao_id=None, naver_id=None, active=None, team=None, manager=None):
    """기존 고객사 정보를 수정. 값이 None인 필드는 변경하지 않음."""
    row_idx = _find_client_row(client_ws, original_name)
    if row_idx is None:
        return False

    header = client_ws.row_values(1)
    updates = {}
    if name is not None:
        updates["고객사명"] = name.strip()
    if active is not None:
        updates["활성여부"] = "TRUE" if active else "FALSE"
    if google_id is not None:
        updates["구글_PlaceID"] = google_id.strip()
    if kakao_id is not None:
        updates["카카오_장소ID"] = kakao_id.strip()
    if naver_id is not None:
        updates["네이버_플레이스ID"] = naver_id.strip()
    if team is not None:
        updates["담당부서"] = team.strip()
    if manager is not None:
        updates["담당자"] = manager.strip()

    for col_name, value in updates.items():
        col_idx = header.index(col_name) + 1
        client_ws.update_cell(row_idx, col_idx, value)
    return True


def delete_client(client_ws, name: str):
    """고객사 행을 삭제."""
    row_idx = _find_client_row(client_ws, name)
    if row_idx is None:
        return False
    client_ws.delete_rows(row_idx)
    return True


def delete_client_data(review_ws, history_ws, client_name: str):
    """특정 고객사와 관련된 리뷰마스터/리뷰통계이력 행을 전부 삭제.
    행 번호가 뒤에서부터 삭제되어야 인덱스가 안 꼬이므로, 높은 행번호부터 지운다."""
    deleted_reviews = 0
    deleted_history = 0

    for ws, name_col_header in [(review_ws, "고객사명"), (history_ws, "고객사명")]:
        all_values = ws.get_all_values()
        if not all_values:
            continue
        header = all_values[0]
        if name_col_header not in header:
            continue
        name_idx = header.index(name_col_header)

        rows_to_delete = [
            row_idx for row_idx, row in enumerate(all_values[1:], start=2)
            if len(row) > name_idx and row[name_idx].strip() == client_name.strip()
        ]

        for row_idx in sorted(rows_to_delete, reverse=True):
            ws.delete_rows(row_idx)
            if ws is review_ws:
                deleted_reviews += 1
            else:
                deleted_history += 1

    return deleted_reviews, deleted_history
