import fitz  # PyMuPDF
import os
import re
import pandas as pd
from collections import Counter

# 설정: 파일이 들어있는 폴더 경로
BASE_DIR = os.path.expanduser("~/workspace/law/test")

def extract_text_from_pdf(file_path):
    """PDF에서 텍스트 추출"""
    text = ""
    try:
        doc = fitz.open(file_path)
        for page in doc:
            text += page.get_text()
        doc.close()
    except Exception as e:
        print(f"파일 읽기 오류 ({file_path}): {e}")
    return text

def parse_answers(answer_text):
    """
    정답 PDF에서 핵심 키워드만 정밀하게 추출 (노이즈 제거)
    """
    keywords = []

    # 1. 괄호 안의 핵심 단어 추출 (예: 답 14번 (1개월과 6회) -> 1개월, 6회)
    bracket_patterns = re.findall(r'답\s*\d*\s*(?:번)?\s*\((.*?)\)', answer_text)
    for bp in bracket_patterns:
        # 쉼표나 '과/와', 띄어쓰기로 분리
        words = re.split(r'[,와과\s]+', bp)
        keywords.extend(words)

    # 2. 단답형 정답 추출 (91번~100번대 주관식 쉼표 분리)
    short_answer_patterns = re.findall(r'^\s*(?:9\d|100)\s+([가-힣0-9,\s]+)$', answer_text, re.MULTILINE)
    for sap in short_answer_patterns:
        words = [w.strip() for w in sap.split(',')]
        keywords.extend(words)

    # 3. 법령/규정 특화 패턴 추출 (어디에 있든 무조건 키워드로 수집)
    # 빈출되는 기간, 비율, 직책, 법령 등을 강제로 잡아냅니다.
    law_patterns = re.findall(r'(\d+일|\d+개월|\d+년|\d+회|\d+명|100분의 \d+|[가-힣]+장관|[가-힣]+령|[가-힣]+위원회|[가-힣]+장)', answer_text)
    keywords.extend(law_patterns)

    # 4. 불용어(Noise) 필터링
    stopwords = ['해설', '및', '답', '번', 'X', '누락됨', '제외', '아님', '맞음', '틀림', '다음', '중', '것은', '이하', '이상', '해당', '포함']
    cleaned_keywords = []

    for kw in keywords:
        kw = kw.strip()
        kw = re.sub(r'[^\w\s]', '', kw) # 특수문자 제거

        # 숫자만 있는 경우 (예: "46", "74") 제외
        if kw.isdigit():
            continue
        # 1글자 단어 제외 (단, '1일', '1년' 등 숫자+단위는 통과)
        if len(kw) < 2 and not re.match(r'\d+[일월년회명배]', kw):
            continue
        # 불용어 리스트에 있거나 '해설'이라는 단어가 포함된 경우 제외
        if kw in stopwords or "해설" in kw:
            continue

        if kw:
            cleaned_keywords.append(kw)

    return cleaned_keywords

def run_analysis():
    all_keywords = []
    files = os.listdir(BASE_DIR)
    
    # 정답 파일만 먼저 골라내기
    answer_files = [f for f in files if "정답" in f and f.endswith(".pdf")]
    
    print(f"총 {len(answer_files)}개의 정답 파일을 분석합니다...")

    for f in answer_files:
        path = os.path.join(BASE_DIR, f)
        raw_text = extract_text_from_pdf(path)
        answers = parse_answers(raw_text)
        all_keywords.extend(answers)

    # 빈도수 계산
    counts = Counter(all_keywords)
    
    # 결과 정리 (상위 50개)
    df = pd.DataFrame(counts.most_common(50), columns=['키워드', '출제 빈도'])
    
    print("\n📊 [정제된 기출 키워드 랭킹 TOP 50]")
    print(df)
    
    # 결과를 JSON으로 저장
    df.to_json(os.path.join(BASE_DIR, "keyword_ranking.json"), orient="records", force_ascii=False)
    print(f"\n✅ 분석 완료: {BASE_DIR}/keyword_ranking.json 저장됨")

if __name__ == "__main__":
    run_analysis()
