import os
import sqlite3
import fitz  # PyMuPDF
import re

BASE_DIR = os.path.expanduser("~/workspace/law/test")
DB_PATH = os.path.expanduser("~/workspace/law/problem_bank.db")

def extract_text(file_path):
    text = ""
    try:
        doc = fitz.open(file_path)
        for page in doc:
            text += page.get_text()
        doc.close()
    except Exception as e:
        print(f"파일 읽기 에러 ({file_path}): {e}")
    return text

def process_and_insert():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    files = os.listdir(BASE_DIR)
    # '문제' 파일과 '정답' 파일을 짝지어 찾기 위한 로직
    question_files = [f for f in files if "문제" in f and f.endswith(".pdf")]
    
    print(f"🚀 총 {len(question_files)}개의 모의고사 세트를 DB에 연결합니다...")

    for q_file in question_files:
        # 1. 파일명 매칭 (예: '200321 부산 문제.pdf' -> '200321 부산 정답.pdf')
        base_name = q_file.replace("문제.pdf", "").strip()
        a_file = f"{base_name} 정답.pdf"
        
        q_path = os.path.join(BASE_DIR, q_file)
        a_path = os.path.join(BASE_DIR, a_file)
        
        if not os.path.exists(a_path):
            print(f"⚠️ 짝이 되는 정답 파일을 찾을 수 없습니다: {a_file}")
            continue

        # 2. 시험 출처(Exam) 정보 DB에 저장
        cursor.execute("INSERT INTO exams (title) VALUES (?)", (base_name,))
        exam_id = cursor.lastrowid
        
        # 3. 텍스트 추출
        q_text = extract_text(q_path)
        a_text = extract_text(a_path)
        
        # 4. 정규식(Regex)을 이용한 문제 분리 (예: "1) 문제내용..." 패턴 찾기)
        # 주의: 실제 PDF의 텍스트 줄바꿈 상태에 따라 정규식 튜닝이 필요할 수 있습니다.
        questions = re.split(r'\n(?=\d+\))', q_text)
        
        for q in questions:
            q = q.strip()
            if not q: continue
            
            # 문제 번호 추출
            match = re.match(r'^(\d+)\)', q)
            if not match: continue
            q_num = match.group(1)
            
            # (임시) 정답 파일에서 해당 번호의 정답 찾기 로직
            # 예: "1 \n 답 2번" 형태에서 번호 추출
            ans_match = re.search(rf'{q_num}\s*\n*답\s*(\d+)번?(.*)', a_text, re.MULTILINE)
            answer_text = ans_match.group(1).strip() if ans_match else ""
            explanation = ans_match.group(2).strip() if ans_match else ""
            
            # 5. Question DB 저장
            cursor.execute('''
                INSERT INTO questions (exam_id, type, content, answer_text, explanation)
                VALUES (?, ?, ?, ?, ?)
            ''', (exam_id, 'MULTIPLE_CHOICE', q, answer_text, explanation))
            
            # ※ 추후 보기(①, ②, ③, ④)를 정밀하게 분리하여 choices 테이블에 넣는 로직 추가 가능
            
    conn.commit()
    conn.close()
    print("✅ 데이터베이스 연결 및 저장 완료!")

if __name__ == "__main__":
    process_and_insert()
