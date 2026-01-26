import os
import json
import random
import datetime
import re
import csv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix 

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'lord_of_blanks_key')
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# --- 구글 OAuth ---
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

oauth = OAuth(app)
google_auth = oauth.register(
    name='google',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

ACTIVE_GAMES = {}

class GoogleSheetManager:
    def __init__(self):
        self.users_ws = None
        self.quests_ws = None
        self.collections_ws = None
        self.USER_HEADERS = ["user_id", "password", "level", "xp", "title", "last_idx", "points", "nickname"]
        self.connect_db() 

    def connect_db(self):
        try:
            json_creds = os.environ.get('GCP_CREDENTIALS')
            if not json_creds: return False
            creds_dict = json.loads(json_creds)
            scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open("memory_game_db")
            
            try: self.users_ws = self.sheet.worksheet("users")
            except: self.users_ws = self.sheet.add_worksheet("users", 100, 10)
            if not self.users_ws.get_all_values(): self.users_ws.append_row(self.USER_HEADERS)

            try: self.collections_ws = self.sheet.worksheet("collections")
            except: self.collections_ws = self.sheet.add_worksheet("collections", 100, 10)
            # collections 헤더: user_id, card_text, grade, date, quest_name, level, type(BLANK/ABBREV)
            if not self.collections_ws.get_all_values(): 
                self.collections_ws.append_row(["user_id", "card_text", "grade", "date", "quest_name", "level", "type"])

            try: self.quests_ws = self.sheet.worksheet("quests")
            except: self.quests_ws = self.sheet.add_worksheet("quests", 100, 5)

            return True
        except Exception as e:
            print(f"DB Error: {e}")
            return False

    def check_connection(self):
        if self.users_ws is None: return self.connect_db()
        return True

    def get_safe_records(self, worksheet):
        try:
            rows = worksheet.get_all_values()
            if len(rows) < 2: return []
            headers = rows[0]
            records = []
            for row in rows[1:]:
                padded = row + [""] * (len(headers) - len(row))
                records.append(dict(zip(headers, padded)))
            return records
        except: return []

    # --- 유저 관리 ---
    def get_user_by_id(self, user_id):
        if not self.check_connection(): return None, None
        try:
            records = self.get_safe_records(self.users_ws)
            for i, row in enumerate(records):
                if str(row['user_id']) == str(user_id):
                    row['points'] = int(row.get('points') or 0)
                    row['level'] = int(row.get('level') or 1)
                    row['xp'] = int(row.get('xp') or 0)
                    if not row.get('nickname'): row['nickname'] = str(user_id).split('@')[0]
                    return row, i + 2
        except: pass
        return None, None

    def register_social(self, user_id):
        if not self.check_connection(): return False
        try:
            if self.get_user_by_id(user_id)[0]: return True
            nick = user_id.split('@')[0]
            self.users_ws.append_row([user_id, "SOCIAL", 1, 0, "빈칸 견습생", 0, 0, nick])
            return True
        except: return False

    def update_nickname(self, row_idx, new_nick):
        if self.check_connection():
            try: self.users_ws.update_cell(row_idx, 8, new_nick); return True
            except: return False
        return False

    # --- 퀘스트 관리 ---
    def save_split_quests(self, title_prefix, lines, creator):
        if not self.check_connection(): return False, 0
        try:
            today = str(datetime.date.today())
            existing = [str(r.get('quest_name')) for r in self.quests_ws.get_all_records()]
            rows = []
            
            full_text = "\n".join(lines)
            processed_data = []
            
            # 앙키 파일 처리
            if '\t' in full_text:
                reader = csv.reader([l for l in lines if not l.startswith('#')], delimiter='\t')
                for row in reader:
                    if len(row) < 2: continue
                    front = re.sub(r'<[^>]+>', '', row[0].replace('<br>', '\n'))
                    back = re.sub(r'<[^>]+>', '', row[1].replace('<br>', ','))
                    answers = [a.strip() for a in re.split(r'[,،、]', back) if a.strip()]
                    text = front
                    for ans in answers: text = re.sub(r'_{2,}', f'{{{ans}}}', text, count=1)
                    processed_data.append(text)
            else:
                processed_data = lines

            last_title = "서문"
            for i, line in enumerate(processed_data):
                if not line.strip(): continue
                
                # 조항 인식
                art_match = re.match(r'^((?:령)?제\s*\d+(?:의\d+)?\s*조(?:\s*\(.*?\))?)', line.strip())
                cir_match = re.match(r'^([①-⑮])', line.strip())
                
                if art_match: last_title = art_match.group(1); q_name = f"{title_prefix}-{last_title}"
                elif cir_match: q_name = f"{title_prefix}-{last_title}-{cir_match.group(1)}"
                else: q_name = f"{title_prefix}-{last_title}-{i+1}"
                
                if q_name not in existing:
                    rows.append([q_name, line[:45000], creator, today])
            
            if rows: self.quests_ws.append_rows(rows); return True, len(rows)
            return False, 0
        except: return False, 0

    def delete_quest_group(self, prefix):
        if not self.check_connection(): return False
        try:
            records = self.quests_ws.get_all_records()
            to_del = [i+2 for i, r in enumerate(records) if str(r.get('quest_name')).startswith(prefix+"-")]
            for idx in sorted(to_del, reverse=True): self.quests_ws.delete_rows(idx)
            return True
        except: return False

    # --- 학습 진행 및 보상 ---
    def get_my_progress(self, user_id):
        # 유저가 획득한(클리어한) 퀘스트 목록 가져오기
        if not self.check_connection(): return []
        try:
            col_records = self.get_safe_records(self.collections_ws)
            return [r for r in col_records if str(r['user_id']) == str(user_id)]
        except: return []

    def get_available_quests(self, user_id, mode):
        # mode: 'acquire'(안 푼거), 'review'(푼 거), 'abbrev'(푼 거 중 약어)
        if not self.check_connection(): return []
        try:
            all_quests = self.quests_ws.get_all_records()
            my_cards = self.get_my_progress(user_id)
            my_quest_names = [c['quest_name'] for c in my_cards if c['type'] == 'BLANK']
            
            if mode == 'acquire':
                # 안 푼 것만
                return [q for q in all_quests if q['quest_name'] not in my_quest_names]
            elif mode == 'review':
                # 푼 것만 (카드 정보 포함)
                return my_cards # collections 데이터 반환
            elif mode == 'abbrev':
                # 푼 것 중 레벨 1 이상인 것
                return [c for c in my_cards if int(c.get('level', 0)) >= 1]
        except: return []

    def process_result(self, user_id, row_idx, quest_name, content, mode):
        if not self.check_connection(): return 0, 0
        
        # 1. 컬렉션 찾기/생성
        records = self.collections_ws.get_all_records()
        found_idx = -1
        card_level = 0
        
        target_type = 'BLANK' # 기본은 빈칸
        
        for i, row in enumerate(records):
            if str(row['user_id']) == str(user_id) and row['quest_name'] == quest_name and row['type'] == 'BLANK':
                found_idx = i + 2
                card_level = int(row.get('level') or 0)
                break
        
        xp_gain = 0
        
        if mode == 'acquire':
            if found_idx == -1: # 신규 획득
                self.collections_ws.append_row([user_id, content, "NORMAL", str(datetime.date.today()), quest_name, 1, "BLANK"])
                xp_gain = 50
            else: # 이미 있는데 획득모드? (에러 방지)
                xp_gain = 10

        elif mode == 'review':
            if found_idx != -1:
                new_level = card_level + 1
                self.collections_ws.update_cell(found_idx, 6, new_level) # 레벨 업
                xp_gain = 20 + (new_level * 5)
        
        elif mode == 'abbrev':
            # 약어는 별도 카드로 저장하지 않고, 기존 카드의 숙련도로 칠 수도 있지만
            # 요청사항: "약어 카드를 획득하며" -> 별도 타입으로 저장
            abbrev_idx = -1
            for i, row in enumerate(records):
                if str(row['user_id']) == str(user_id) and row['quest_name'] == quest_name and row['type'] == 'ABBREV':
                    abbrev_idx = i + 2
                    break
            
            if abbrev_idx == -1: # 약어 카드 최초 획득
                self.collections_ws.append_row([user_id, content, "RARE", str(datetime.date.today()), quest_name, 1, "ABBREV"])
                xp_gain = 100
            else: # 약어 복습
                curr_lv = int(records[abbrev_idx-2].get('level') or 1)
                self.collections_ws.update_cell(abbrev_idx, 6, curr_lv + 1)
                xp_gain = 30

        # 유저 XP 업데이트
        user_data, _ = self.get_user_by_id(user_id)
        if user_data:
            current_xp = int(user_data['xp'])
            current_level = int(user_data['level'])
            
            new_xp = current_xp + xp_gain
            req_xp = current_level * 100
            
            if new_xp >= req_xp:
                current_level += 1
                new_xp -= req_xp
            
            self.users_ws.update_cell(row_idx, 3, current_level)
            self.users_ws.update_cell(row_idx, 4, new_xp)
            
            return current_level, new_xp

        return 0, 0

gm = GoogleSheetManager()

def natural_sort_key(q):
    name = q.get('quest_name', '')
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', name)]

# --- 라우트 ---

@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('lobby'))
    return render_template('login.html')

@app.route('/google/login')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google_auth.authorize_redirect(redirect_uri)

@app.route('/google/callback')
def google_callback():
    try:
        token = google_auth.authorize_access_token()
        user_info = token.get('userinfo')
        email = user_info['email']
        if not gm.check_connection(): return "DB Error", 500
        
        gm.register_social(email)
        user, idx = gm.get_user_by_id(email)
        
        session['user_id'] = email
        session['user_row_idx'] = idx
        return redirect(url_for('lobby'))
    except: return redirect(url_for('index'))

@app.route('/lobby')
def lobby():
    if 'user_id' not in session: return redirect(url_for('index'))
    # 항상 최신 데이터 로드
    user, _ = gm.get_user_by_id(session['user_id'])
    if user:
        session['level'] = user['level']
        session['xp'] = user['xp']
        session['nickname'] = user['nickname']
        session['points'] = user['points']
    
    return render_template('lobby.html', 
                           level=session['level'], xp=session['xp'], 
                           points=session['points'], nickname=session['nickname'],
                           req_xp=session['level']*100)

# [1] 생성 구역 (업로드)
@app.route('/zone/generate', methods=['GET', 'POST'])
def zone_generate():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        if 'delete_group' in request.form:
            gm.delete_quest_group(request.form['delete_group'])
            flash("삭제되었습니다.")
        elif 'new_q_file' in request.files:
            f = request.files['new_q_file']
            raw = f.read().decode('utf-8')
            lines = raw.replace('\r\n','\n').split('\n')
            ok, cnt = gm.save_split_quests(request.form['new_q_name'], lines, session['user_id'])
            if ok: flash(f"{cnt}개 생성 완료!")
    
    # 생성된 목록 보여주기
    quests = gm.get_quest_list()
    quests.sort(key=natural_sort_key)
    return render_template('zone_generate.html', quests=quests)

# [2] 획득 구역 (새로운 문제 풀기)
@app.route('/zone/acquire', methods=['GET', 'POST'])
def zone_acquire():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    # 퀘스트 선택 시 플레이 화면으로
    if request.method == 'POST':
        q_name = request.form['quest_name']
        all_q = gm.get_quest_list()
        quest = next((q for q in all_q if q['quest_name'] == q_name), None)
        if quest:
            user_id = session['user_id']
            ACTIVE_GAMES[user_id] = {
                'mode': 'acquire',
                'quest_name': q_name,
                'content': quest['content']
            }
            return redirect(url_for('play_game'))

    quests = gm.get_available_quests(session['user_id'], 'acquire')
    quests.sort(key=natural_sort_key)
    return render_template('zone_list.html', title="획득 구역", quests=quests, mode='acquire')

# [3] 복습 구역 (무제한 반복)
@app.route('/zone/review', methods=['GET', 'POST'])
def zone_review():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    if request.method == 'POST':
        q_name = request.form['quest_name']
        cards = gm.get_available_quests(session['user_id'], 'review')
        card = next((c for c in cards if c['quest_name'] == q_name), None)
        if card:
            ACTIVE_GAMES[session['user_id']] = {
                'mode': 'review',
                'quest_name': q_name,
                'content': card['card_text']
            }
            return redirect(url_for('play_game'))

    cards = gm.get_available_quests(session['user_id'], 'review')
    cards.sort(key=natural_sort_key)
    return render_template('zone_list.html', title="복습 구역", quests=cards, mode='review')

# [4] 약어 구역 (1회독 이상 진입)
@app.route('/zone/abbrev', methods=['GET', 'POST'])
def zone_abbrev():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    if request.method == 'POST':
        q_name = request.form['quest_name']
        cards = gm.get_available_quests(session['user_id'], 'abbrev')
        card = next((c for c in cards if c['quest_name'] == q_name), None)
        if card:
            # 약어 모드는 내용을 다 지우고 제목만 힌트로 줌 (플레이 화면에서 처리)
            ACTIVE_GAMES[session['user_id']] = {
                'mode': 'abbrev',
                'quest_name': q_name,
                'content': card['card_text'] # 원문
            }
            return redirect(url_for('play_game'))

    cards = gm.get_available_quests(session['user_id'], 'abbrev')
    cards.sort(key=natural_sort_key)
    return render_template('zone_list.html', title="약어 훈련소", quests=cards, mode='abbrev')

# [공통] 게임 플레이 화면
@app.route('/play', methods=['GET', 'POST'])
def play_game():
    if 'user_id' not in session: return redirect(url_for('index'))
    game = ACTIVE_GAMES.get(session['user_id'])
    if not game: return redirect(url_for('lobby'))

    if request.method == 'GET':
        # 빈칸 파싱
        import re
        content = game['content']
        parts = []
        targets = []
        
        if game['mode'] == 'abbrev':
            # 약어 모드: 전체가 빈칸 (제목만 힌트로)
            # {내용} 형식으로 강제 변환
            clean_text = re.sub(r'\{([^}]+)\}', r'\1', content) # 기존 빈칸 제거
            parts = [{'type':'text', 'val': '내용을 입력하세요: '}, {'type':'input', 'id':0}]
            targets = [clean_text.strip()]
        else:
            # 일반/복습 모드: {빈칸} 파싱
            last_idx = 0
            idx = 0
            for m in re.finditer(r'\{([^}]+)\}', content):
                s, e = m.span()
                if s > last_idx: parts.append({'type':'text', 'val': content[last_idx:s]})
                parts.append({'type':'input', 'id': idx})
                targets.append(m.group(1))
                idx += 1
                last_idx = e
            if last_idx < len(content): parts.append({'type':'text', 'val': content[last_idx:]})

        return render_template('play.html', parts=parts, targets=targets, mode=game['mode'], title=game['quest_name'])

    elif request.method == 'POST':
        # 결과 처리
        # 약어 모드는 내용 전체가 정답
        clean_text = game['content']
        if game['mode'] != 'abbrev':
            clean_text = re.sub(r'\{([^}]+)\}', r'\1', game['content'])
            
        lv, xp = gm.process_result(session['user_id'], session['user_row_idx'], game['quest_name'], clean_text, game['mode'])
        
        session['level'] = lv
        session['xp'] = xp
        
        flash("학습 완료! 성장했습니다.")
        # 해당 구역 목록으로 복귀
        return redirect(url_for(f"zone_{game['mode']}"))

@app.route('/sw.js')
def sw(): return app.send_static_file('sw.js')

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(host='0.0.0.0', port=10000)
