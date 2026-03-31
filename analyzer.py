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
    정답 PDF에서 '답 n번 (키워드)' 형태를 추출
    제공된 정답 파일 형식에 맞게 정규식 설계
    """
    # 예: "답 2번 (6개월)", "1) 답: 대통령령" 등의 패턴 매칭
    patterns = [
        r"답\s*\d*(?:번)?\s*[:\s]*([가-힣0-9\s%]+?)(?=\n|$|\d\s*번|답)",
        r"\d\)\s*([가-힣0-9\s%]+?)(?=\n|$)"
    ]
    
    extracted_answers = []
    for pattern in patterns:
        matches = re.findall(pattern, answer_text)
        extracted_answers.extend([m.strip() for m in matches if len(m.strip()) > 1])
    
    return extracted_answers

def run_analysis():
    all_keywords = []
    files = os.listdir(BASE_DIR)
    
    # 정답 파일만 먼저 골라내기 (파일명에 '정답' 포함 기준)
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
    
    print("\n📊 [기출 키워드 랭킹 TOP 50]")
    print(df)
    
    # 결과를 JSON으로 저장 (프로젝트에서 불러오기 위함)
    df.to_json(os.path.join(BASE_DIR, "keyword_ranking.json"), orient="records", force_ascii=False)
    print(f"\n✅ 분석 완료: {BASE_DIR}/keyword_ranking.json 저장됨")

if __name__ == "__main__":
    run_analysis()
