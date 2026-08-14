"""
부정 리뷰 판단 로직.

SENTIMENT_VERSION 값이 로그에 찍힙니다. GitHub Actions 로그에서 이 버전 번호가
안 보이거나 예전 번호로 보이면, 업로드가 제대로 반영이 안 된 것입니다.
"""
import os
import re
import json
from anthropic import Anthropic

SENTIMENT_VERSION = "v3-2026-08-14"

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _extract_json(raw_text: str):
    if not raw_text:
        return None
    cleaned = re.sub(r"```json|```", "", raw_text).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    candidate = match.group(0) if match else cleaned
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _call_ai(content: str):
    client = _get_client()
    prompt = (
        "당신은 병원 리뷰를 검토해서, 병원 담당자가 직접 확인해야 할 '부정적인 리뷰'만 골라내는 역할입니다.\n\n"
        "기본 전제: 대부분의 리뷰는 긍정적입니다. 확실한 불만 신호가 없으면 긍정으로 판단하세요.\n\n"
        "다음 중 하나라도 해당될 때만 부정(true)으로 판단하세요:\n"
        "- 서비스, 의료 품질, 직원 응대 등에 대한 구체적인 불만이나 비판이 있음\n"
        "- 재방문하지 않겠다는 의사를 밝힘\n"
        "- 기대에 못 미쳤다는 실망감을 표현함\n"
        "- 다른 사람에게 추천하지 않겠다는 뜻을 내비침\n\n"
        "다음은 부정이 아닙니다 (반드시 false로 판단하세요):\n"
        "- 짧은 칭찬 (예: '좋아요', '친절해요', '만족합니다')\n"
        "- 재방문 의사를 밝힌 리뷰 (예: '다음에 또 올게요')\n"
        "- 단순 사실 서술이나 감상 (예: '여기 새로 생겼네요')\n"
        "- 이모티콘이나 감탄사 위주의 가벼운 리뷰\n"
        "- 판단이 애매한 경우 → 긍정(false)으로 처리하세요\n\n"
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
    print(f"[sentiment {SENTIMENT_VERSION}] 리뷰='{content[:30]}' → AI 원본응답='{raw_text.strip()}'")
    return _extract_json(raw_text)


def _ai_judge(content: str) -> bool:
    for attempt in range(2):
        try:
            parsed = _call_ai(content)
        except Exception as e:
            print(f"[sentiment {SENTIMENT_VERSION}] AI 호출 자체 실패 (시도 {attempt + 1}): {e}")
            parsed = None

        if parsed is not None and "is_negative" in parsed:
            return bool(parsed["is_negative"])

    print(f"[sentiment {SENTIMENT_VERSION}] JSON 파싱 2번 다 실패, '정상'으로 처리: {content[:50]}")
    return False


def judge_review(rating, content: str) -> tuple[bool, str]:
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
