import sqlite3
import os

def create_database():
    # 데이터베이스 파일 생성 및 연결
    db_path = os.path.expanduser("~/workspace/law/problem_bank.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. 모의고사 출처 테이블
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS exams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,         -- 예: '2020년 3월 21일 제1차 부산지역본부'
        exam_date DATE               -- 출제 일자
    )
    ''')

    # 2. 문제 본문 테이블
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id INTEGER,
        type TEXT NOT NULL,          -- 예: 'MULTIPLE_CHOICE'(객관식), 'BLANK'(빈칸)
        content TEXT NOT NULL,       -- 문제 지문 (예: '국민건강보험종합계획에 포함되어야 하는 사항으로 맞는 것은?')
        answer_text TEXT,            -- 정답 관련 텍스트 (주관식/빈칸용)
        explanation TEXT,            -- 해설 내용 (유저가 틀렸을 때 노출)
        keyword TEXT,                -- 앞서 추출한 핵심 키워드 (예: '대통령령', '14일 이내')
        difficulty INTEGER DEFAULT 1,-- 난이도 (1~5)
        FOREIGN KEY(exam_id) REFERENCES exams(id)
    )
    ''')

    # 3. 객관식 선택지 테이블 (보기 1~4번)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS choices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question_id INTEGER,
        choice_num INTEGER,          -- 1, 2, 3, 4
        content TEXT NOT NULL,       -- 보기 내용
        is_correct BOOLEAN,          -- 정답 여부 (0: 오답, 1: 정답)
        FOREIGN KEY(question_id) REFERENCES questions(id)
    )
    ''')

    # 4. 사용자 풀이 이력 테이블 (오답 노트 및 방어전 시스템용)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,       -- 유저 식별자 (Google 이메일 등)
        question_id INTEGER,
        is_correct BOOLEAN,          -- 1: 맞춤, 0: 틀림
        attempt_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(question_id) REFERENCES questions(id)
    )
    ''')

    conn.commit()
    conn.close()
    print(f"✅ 성공: 문제은행 데이터베이스({db_path})가 구축되었습니다.")

if __name__ == "__main__":
    create_database()
