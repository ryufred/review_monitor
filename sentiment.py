"""
부정 리뷰 판단 로직.

1단계: 별점 3점 이하 → 바로 부정으로 확정 (AI 호출 없음)
2단계: 별점 4~5점(또는 별점 없음)이면서 리뷰 내용이 있는 경우 → 무조건 AI(Claude Haiku)가
       내용을 읽고 뉘앙스까지 판단
3단계: 리뷰 내용이 아예 없으면 → AI 호출 없이 정상 처리

파싱 안정성:
  - AI가 가끔 ```json 코드블록 표시를 붙이거나 부가 설명을 덧붙이는 경우가 있어,
    정규식으로 JSON 부분만 추출해서 파싱한다 (단순 json.loads보다 훨씬 안정적).
  - 그래도 파싱에 실패하면, 한 번 더 재시도한다.
  - 재시도까지 실패하면 '부정'이 아니라 '정상'으로 처리한다 (예전엔 무조건 부정으로
    처리해서, "좋아요"처럼 명백히 긍정적인 리뷰도 파싱 실패 시 부정으로 잘못 뜨는
    문제가 있었음. 파싱 실패는 AI 판단 자체의 문제가 아니라 응답 형식 문제일 뿐이므로,
    안전하게 '정상'으로 처리해 불필요한 확인 작업을 늘리지 않는다).
"""
import os
import re
import json
from anthropic import Anthropic

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _extract_json(raw_text: str):
    """AI 응답에서 JSON 객체 부분만 정규식으로 추출해서 파싱. 실패하면 None."""
    if not raw_text:
        return None
    # ```json ... ``` 코드블록 표시 제거
    cleaned = re.sub(r"```json|```", "", raw_text).strip()
    # 응답 안에 {...} 형태가 있으면 그 부분만 추출 (앞뒤 설명 텍스트 무시)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    candidate = match.group(0) if match else cleaned
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _call_ai(content: str):
    client = _get_client()
    prompt = (
        "다음은 병원 리뷰입니다. 이 리뷰가 병원 입장에서 '부정적인 리뷰'인지 판단해주세요.\n"
        "특정 부정 단어가 없어도, 전체적인 어조나 뉘앙스가 미묘하게 부정적이거나 애매하면"
        "(예: 재방문 의사가 불확실하거나, 기대에 못 미쳤다는 느낌 등) 부정으로 판단하세요.\n"
        "단순 사실 서술이나 명확히 긍정적인 내용은 부정으로 보지 마세요.\n\n"
        f"리뷰: {content}\n\n"
        '다른 설명 없이 오직 JSON 한 줄로만 답하세요: {"is_negative": true} 또는 {"is_negative": false}'
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    return _extract_json(raw_text)


def _ai_judge(content: str) -> bool:
    """Claude Haiku로 리뷰 내용의 부정 여부(뉘앙스 포함)를 판단. True/False 반환."""
    for attempt in range(2):  # 파싱 실패 시 한 번 더 재시도
        try:
            parsed = _call_ai(content)
        except Exception as e:
            print(f"[sentiment] AI 호출 자체 실패 (시도 {attempt + 1}): {e}")
            parsed = None

        if parsed is not None and "is_negative" in parsed:
            return bool(parsed["is_negative"])

    # 재시도까지 실패하면 '정상'으로 처리 (파싱 실패는 판단 문제가 아니라 형식 문제이므로,
    # 무조건 부정 처리해서 불필요한 확인 작업을 늘리지 않음)
    print(f"[sentiment] JSON 파싱 2번 다 실패, '정상'으로 처리: {content[:50]}")
    return False


def judge_review(rating, content: str) -> tuple[bool, str]:
    """
    Returns: (is_negative: bool, 판단근거: str)
    """
    try:
        rating_num = float(rating)
    except (TypeError, ValueError):
        rating_num = None

    if rating_num is not None and rating_num <= 3:
        return True, "별점기준"

    if not content or not content.strip():
        return False, "정상(내용없음)"

    is_neg = _ai_judge(content)
    return is_neg, "AI판단" if is_neg else "AI판단(긍정)"
