"""
이미 시트에 저장된 리뷰들을 새 판단 기준으로 다시 검토해서 수정하는 일회성 스크립트.

프롬프트를 고치기 전에 잘못 '부정'으로 저장된 리뷰들을 되돌리기 위한 용도입니다.
GitHub Actions에서 workflow_dispatch(수동 실행)로 돌리거나, 로컬에서 환경변수를
설정하고 실행하면 됩니다.

동작:
  - 리뷰마스터 시트에서 status='신규' 이면서 판단근거가 'AI판단'인 행만 다시 검토
    (별점기준으로 확정된 부정 리뷰나, 이미 확인 처리한 건은 건드리지 않음)
  - 새 기준으로 판단이 '긍정'으로 바뀌면 is_negative를 FALSE로, status를 '확인됨'으로 변경
"""
import os
from sheets_schema import ensure_schema, REVIEW_SHEET, open_spreadsheet
from sentiment import judge_review


def run():
    sh = open_spreadsheet()
    ws = sh.worksheet(REVIEW_SHEET)
    all_values = ws.get_all_values()
    header = all_values[0]

    idx_content = header.index("리뷰내용")
    idx_rating = header.index("별점")
    idx_negative = header.index("is_negative")
    idx_reason = header.index("판단근거")
    idx_status = header.index("status")

    updates = []
    changed = 0

    for row_idx, row in enumerate(all_values[1:], start=2):
        status = row[idx_status].strip()
        reason = row[idx_reason].strip()
        is_negative = row[idx_negative].strip().upper() == "TRUE"

        # AI가 판단했던 '신규 부정' 건만 재검토 대상
        if status != "신규" or not is_negative or not reason.startswith("AI판단"):
            continue

        content = row[idx_content]
        rating = row[idx_rating]

        new_negative, new_reason = judge_review(rating, content)

        if not new_negative:
            # 긍정으로 바뀜 → 수정
            updates.append({"range": f"{chr(65 + idx_negative)}{row_idx}", "values": [["FALSE"]]})
            updates.append({"range": f"{chr(65 + idx_reason)}{row_idx}", "values": [[new_reason]]})
            updates.append({"range": f"{chr(65 + idx_status)}{row_idx}", "values": [["확인됨"]]})
            changed += 1
            print(f"긍정으로 수정: {content[:40]}")

    if updates:
        ws.batch_update(updates)

    print(f"\n총 {changed}건이 긍정으로 수정되었습니다.")


if __name__ == "__main__":
    run()
