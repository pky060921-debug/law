import os
import json
import random
import datetime
import re
import html 
import time
import sqlite3
from datetime import timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix 
from werkzeug.exceptions import HTTPException 
import traceback 

app = Flask(__name__)
# 세션 보안키 및 영구 세션 설정 (31일)
app.secret_key = os.environ.get('SECRET_KEY', 'law_game_ultimate_fixed_v4_local')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=31)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    return f"<pre>{traceback.format_exc()}</pre>", 500

# OAuth 설정
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

oauth = OAuth(app)
google_auth = oauth.register(
    name='google',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

ACTIVE_GAMES = {}

# ==========================================
# [코어 DB 매니저] 구글 시트를 대체하는 로컬 통합 DB 클래스
# ==========================================
class LocalDBManager:
    def __init__(self):
        # 맥미니 로컬 환경 통합 DB 경로
        self.db_path = os.path.expanduser("~/workspace/law/a1_universe_core.db")

    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_user_by_email(self, email):
        conn = self.get_conn()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        
        if user:
            # 기존 템플릿과 호환되도록 딕셔너리로 변환
            return dict(user)
        return None

    def register_social(self, email):
        conn = self.get_conn()
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            nickname = email.split('@')[0]
            conn.execute('''
                INSERT INTO users (email, nickname, level, xp, goal_balance) 
                VALUES (?, ?, 1, 0, 0.0)
            ''', (email, nickname))
            conn.commit()
        conn.close()
        return True

    def get_all_cards(self):
        """카드 제련소에서 만든 모든 카드를 가져옵니다."""
        conn = self.get_conn()
        cards = conn.execute('''
            SELECT c.*, z.title as zone_title 
            FROM cards c
            LEFT JOIN zones z ON c.zone_id = z.id
        ''').fetchall()
        conn.close()
        return [dict(c) for c in cards]

    def get_card_with_options(self, card_id):
        conn = self.get_conn()
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        options = conn.execute("SELECT * FROM card_options WHERE card_id = ?", (card_id,)).fetchall()
        conn.close()
        return dict(card), [dict(o) for o in options]

    def get_my_progress(self, user_id):
        """유저의 카드 수집(정복) 기록 및 망각 곡선 상태를 가져옵니다."""
        conn = self.get_conn()
        user = self.get_user_by_email(user_id)
        if not user: return []
        
        records = conn.execute('''
            SELECT uc.*, c.mnemonic_title, c.grade 
            FROM user_collection uc
            JOIN cards c ON uc.card_id = c.id
            WHERE uc.user_id = ?
        ''', (user['id'],)).fetchall()
        conn.close()
        return [dict(r) for r in records]

    def process_result(self, email, card_id, mode, duration):
        """정답 제출 시 경험치 및 복습 주기(망각 곡선) 업데이트"""
        conn = self.get_conn()
        user = self.get_user_by_email(email)
        if not user: return 0, 0
        
        uid = user['id']
        record = conn.execute("SELECT * FROM user_collection WHERE user_id = ? AND card_id = ?", (uid, card_id)).fetchone()
        
        today = datetime.datetime.now()
        
        try:
            if not record:
                # 최초 획득 (테라포밍)
                conn.execute('''
                    INSERT INTO user_collection (user_id, card_id, review_stage, defense_status)
                    VALUES (?, ?, 1, 'SAFE')
                ''', (uid, card_id))
                self._add_xp_and_goal(conn, uid, xp_amount=100, goal_amount=1.0)
            else:
                # 방어전 성공 (복습 단계 상승)
                new_stage = record['review_stage'] + 1
                conn.execute('''
                    UPDATE user_collection 
                    SET review_stage = ?, last_reviewed_at = ?, defense_status = 'SAFE'
                    WHERE id = ?
                ''', (new_stage, today, record['id']))
                self._add_xp_and_goal(conn, uid, xp_amount=50, goal_amount=0.5)
            
            conn.commit()
        except Exception as e:
            print("DB Error during process_result:", e)
            conn.rollback()
            
        # 갱신된 유저 레벨 반환
        updated_user = conn.execute("SELECT level, xp FROM users WHERE id = ?", (uid,)).fetchone()
        conn.close()
        return updated_user['level'], updated_user['xp']

    def _add_xp_and_goal(self, conn, user_id, xp_amount, goal_amount):
        """경험치 추가 및 레벨업 계산 (오프체인 GOAL 보상)"""
        user = conn.execute("SELECT level, xp, goal_balance FROM users WHERE id = ?", (user_id,)).fetchone()
        
        u_lv = user['level']
        new_xp = user['xp'] + xp_amount
        new_goal = user['goal_balance'] + goal_amount
        
        req = u_lv * 100
        while new_xp >= req:
            u_lv += 1
            new_xp -= req
            req = u_lv * 100
            
        conn.execute('''
            UPDATE users SET level = ?, xp = ?, goal_balance = ? WHERE id = ?
        ''', (u_lv, new_xp, new_goal, user_id))

gm = LocalDBManager()

# ==========================================
# 기본 라우팅 및 로그인 (기존과 동일)
# ==========================================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('lobby'))
    return render_template('login.html')

@app.route('/google/login')
def google_login():
    session.permanent = True
    return google_auth.authorize_redirect(url_for('google_callback', _external=True))

@app.route('/google/callback')
def google_callback():
    try:
        token = google_auth.authorize_access_token()
        email = token.get('userinfo')['email']
        session['user_id'] = email
        gm.register_social(email)
        return redirect(url_for('lobby'))
    except Exception as e:
        return f"Login Failed: {e}", 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/mypage')
def mypage(): 
    user = gm.get_user_by_email(session['user_id'])
    return render_template('mypage.html', user=user)

# ==========================================
# 핵심: 로비 및 카드 플레이 시스템
# ==========================================

@app.route('/lobby')
def lobby():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    user = gm.get_user_by_email(session['user_id'])
    all_cards = gm.get_all_cards()
    my_progress = gm.get_my_progress(session['user_id'])
    
    progress_map = {p['card_id']: p for p in my_progress}
    
    # 챕터(구역)별로 카드 그룹화
    zones = {}
    for card in all_cards:
        z_id = card['zone_id']
        z_title = card['zone_title'] or "미분류 구역"
        
        if z_id not in zones:
            zones[z_id] = {'id': z_id, 'title': z_title, 'cards': []}
            
        card_data = {
            'id': card['id'],
            'title': card['mnemonic_title'],
            'grade': card['grade'],
            'defense_status': progress_map.get(card['id'], {}).get('defense_status', 'UNEXPLORED'),
            'level': progress_map.get(card['id'], {}).get('review_stage', 0)
        }
        zones[z_id]['cards'].append(card_data)
        
    return render_template('lobby.html', user=user, zones=sorted(zones.values(), key=lambda x: x['id']))

@app.route('/game_start', methods=['POST'])
def game_start():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    card_id = request.form.get('card_id')
    card, options = gm.get_card_with_options(card_id)
    
    if not card:
        flash("카드를 찾을 수 없습니다.")
        return redirect(url_for('lobby'))
    
    # Forge에서 만든 데이터를 플레이 화면에 맞게 재구성
    options_map = {}
    targets = []
    
    # options 테이블 데이터를 바탕으로 빈칸 인덱스별로 분류
    for opt in options:
        idx = str(opt['blank_index'])
        if idx not in options_map:
            options_map[idx] = []
        
        options_map[idx].append(opt['option_text'])
        if opt['is_correct']:
            # targets 리스트의 길이를 인덱스에 맞게 확장
            while len(targets) <= opt['blank_index']:
                targets.append("")
            targets[opt['blank_index']] = opt['option_text']
            
    # 보기 배열을 무작위로 섞음
    for k in options_map:
        random.shuffle(options_map[k])

    stages = [{
        'html': card['content_html'],  # 이미 Forge에서 뚫은 <span> 태그가 포함되어 있음
        'targets': targets,
        'options_map': options_map,
        'stage_name': card['mnemonic_title'],
        'q_type': f"[{card['grade']}등급] 카드"
    }]

    ACTIVE_GAMES[session['user_id']] = {
        'mode': request.form.get('action', 'terraform'),
        'card_id': card['id'],
        'title': card['mnemonic_title'],
        'stages': stages
    }
    
    return redirect(url_for('play_game'))

@app.route('/play', methods=['GET', 'POST'])
def play_game():
    if 'user_id' not in session:
        return redirect(url_for('index'))
        
    game = ACTIVE_GAMES.get(session['user_id'])
    if not game:
        return redirect(url_for('lobby'))
        
    user = gm.get_user_by_email(session['user_id'])
    
    if request.method == 'POST':
        duration = request.form.get('duration')
        lv, xp = gm.process_result(session['user_id'], game['card_id'], game['mode'], duration)
        
        return render_template('play.html', 
                             stages=game['stages'], 
                             title=game['title'], 
                             chapter="방어 완료",
                             user_items=user,
                             show_result=True, 
                             reward_lv=lv)
    
    time_per_blank = max(3.0, 12.0 - (user['level'] * 0.2))
    return render_template('play.html', 
                         stages=game['stages'], 
                         title=game['title'], 
                         chapter="전투 진입",
                         user_items=user,
                         time_per_blank=time_per_blank)

# ==========================================
# 빈칸 공작소 (Card Forge) API
# ==========================================

@app.route('/forge')
def forge_ui():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    return render_template('forge.html')

@app.route('/api/forge/create_card', methods=['POST'])
def create_card_api():
    if 'user_id' not in session:
        return jsonify({"success": False, "msg": "로그인이 필요합니다."}), 401
        
    data = request.json
    mnemonic_title = data.get('mnemonic_title')
    content_html = data.get('content_html')
    zone_id = data.get('zone_id', 1) 
    blanks_data = data.get('blanks') 
    
    # 오답 매력도 기반 등급 자동 평가 (추후 고도화 가능)
    grade = 'C'
    if len(blanks_data) >= 2: grade = 'B'
    if len(blanks_data) >= 4: grade = 'A'
    if any(len(b['options']) == 3 for b in blanks_data): grade = 'S' 
    
    conn = gm.get_conn()
    try:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (session['user_id'],)).fetchone()
        creator_id = user['id'] if user else 1
        
        # 1. 카드 데이터 주조 (NFT 메타데이터 모체)
        conn.execute('''
            INSERT INTO cards (zone_id, creator_id, mnemonic_title, content_html, grade)
            VALUES (?, ?, ?, ?, ?)
        ''', (zone_id, creator_id, mnemonic_title, content_html, grade))
        
        card_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        # 2. 오답 보기 주조
        for i, blank in enumerate(blanks_data):
            conn.execute('''
                INSERT INTO card_options (card_id, blank_index, option_text, is_correct)
                VALUES (?, ?, ?, 1)
            ''', (card_id, i, blank['answer']))
            
            for opt in blank['options']:
                if opt.strip(): 
                    conn.execute('''
                        INSERT INTO card_options (card_id, blank_index, option_text, is_correct)
                        VALUES (?, ?, ?, 0)
                    ''', (card_id, i, opt.strip()))
                    
        conn.commit()
        return jsonify({"success": True, "msg": f"[{grade}등급] 카드가 성공적으로 제련되었습니다!", "card_id": card_id})
    except Exception as e:
        conn.rollback()
        print("Forge Error:", traceback.format_exc())
        return jsonify({"success": False, "msg": str(e)}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    # 디버그 모드로 실행하여 콘솔에서 에러 추적 용이하게 함
    app.run(host='0.0.0.0', port=10000, debug=True)
