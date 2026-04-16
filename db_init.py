import sqlite3
import os

def create_integrated_database():
    # 맥미니 로컬 환경에 맞춘 통합 DB 파일 경로
    db_path = os.path.expanduser("~/workspace/law/a1_universe_core.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. 유저 (Players & Investors)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_address TEXT UNIQUE,  -- Sui 지갑 주소 (소셜 로그인과 연동)
        email TEXT UNIQUE,
        nickname TEXT,
        level INTEGER DEFAULT 1,
        xp INTEGER DEFAULT 0,
        goal_balance REAL DEFAULT 0.0, -- 오프체인 가계부 (추후 온체인 동기화용)
        role TEXT DEFAULT 'player',  -- 'player', 'investor', 'creator'
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 2. 챕터/구역 (Planets/Zones)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS zones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        law_type TEXT,
        required_level INTEGER DEFAULT 1
    )
    ''')

    # 3. 빈칸 카드 (NFT의 모체가 되는 메타데이터)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zone_id INTEGER,
        creator_id INTEGER,          -- 카드를 직접 만든 유저 (보상 대상자)
        mnemonic_title TEXT,         -- 두문자 (예: "대보공승")
        content_html TEXT,           -- 본문 및 빈칸 HTML
        grade TEXT DEFAULT 'C',      -- 시스템이 평가한 오답 매력도 등급 (C ~ SS)
        image_url TEXT,              -- 기괴/자극적 암기용 이미지 경로 (추후 IPFS URI)
        is_minted BOOLEAN DEFAULT 0, -- 온체인 NFT 발행 여부
        nft_object_id TEXT,          -- Sui 네트워크 상의 Object ID
        FOREIGN KEY(zone_id) REFERENCES zones(id),
        FOREIGN KEY(creator_id) REFERENCES users(id)
    )
    ''')

    # 4. 카드 옵션 (함정/오답 보기)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS card_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id INTEGER,
        blank_index INTEGER,
        option_text TEXT,
        is_correct BOOLEAN,
        FOREIGN KEY(card_id) REFERENCES cards(id)
    )
    ''')

    # 5. 망각 곡선 및 소유권 (Memory Palace & Defense System)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_collection (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        card_id INTEGER,
        acquired_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_reviewed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        review_stage INTEGER DEFAULT 1, -- 에빙하우스 복습 주기 단계
        defense_status TEXT DEFAULT 'SAFE', -- 'SAFE', 'WARNING', 'INVADED'
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(card_id) REFERENCES cards(id)
    )
    ''')

    # 6. 보상 트랜잭션 로그 (Dual-Minting 기록)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS reward_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id INTEGER,
        solver_id INTEGER,           -- 문제를 푼 유저 (+1 GOAL)
        creator_id INTEGER,          -- 카드 설계자/투자자 (+1 GOAL)
        tx_digest TEXT,              -- Sui 네트워크 트랜잭션 해시
        reward_date DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()
    print(f"✅ 맥미니 로컬 코어 DB({db_path})가 성공적으로 구축되었습니다.")

if __name__ == "__main__":
    create_integrated_database()
