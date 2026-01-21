import os
import json
import random
import datetime
import re
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
        # [수정] 닉네임(nickname) 필드 추가
        self.USER_HEADERS = ["user_id", "password", "level", "xp", "title", "last_idx", "points", "nickname"]
        self.connect_db() 

    def connect_db(self):
        try:
            json_creds = os.environ.get('GCP_CREDENTIALS')
            if not json_creds:
                print("🚫 오류: Render 환경변수 확인 필요")
                return False
            
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

            try: self.quests_ws = self.sheet.worksheet("quests")
            except: self.quests_ws = self.sheet.add_worksheet("quests", 100, 5)

            print("✅ 구글 시트 연결 성공")
            return True
        except Exception as e:
            print(f"❌ 시트 연결 에러: {e}")
            return False

    def check_connection(self):
        if self.users_ws is None: return self.connect_db()
        return True

    def get_safe_records(self, worksheet, headers_list):
        try:
            rows = worksheet.get_all_values()
            if len(rows) < 2: return [] 
            records = []
            for row in rows[1:]: 
                padded_row = row + [""] * (len(headers_list) - len(row))
                record = dict(zip(headers_list, padded_row))
                records.append(record)
            return records
        except Exception as e: return []

    def get_user_by_id(self, user_id):
        if not self.check_connection(): return None, None
        try:
            records = self.get_safe_records(self.users_ws, self.USER_HEADERS)
            for i, row in enumerate(records):
                if str(row['user_id']) == str(user_id):
                    # 숫자 안전 변환
                    try: row['points'] = int(row.get('points', 0) or 0)
                    except: row['points'] = 0
                    try: row['level'] = int(row.get('level', 1) or 1)
                    except: row['level'] = 1
                    try: row['xp'] = int(row.get('xp', 0) or 0)
                    except: row['xp'] = 0
                    # 닉네임이 없으면 이메일 앞부분 사용
                    if not row.get('nickname'):
                        row['nickname'] = str(user_id).split('@')[0]
                    
                    return row, i + 2 
        except: pass
        return None, None

    def register_social(self, user_id):
        if not self.check_connection(): return False, "DB 연결 끊김"
        try:
            user_data, _ = self.get_user_by_id(user_id)
            if user_data: return True, "이미 존재함"
            
            # 기본 닉네임은 이메일 아이디
            default_nick = user_id.split('@')[0]
            self.users_ws.append_row([user_id, "SOCIAL_LOGIN", 1, 0, "빈칸 견습생", 0, 0, default_nick])
            return True, "가입 성공"
        except Exception as e: return False, str(e)

    # [신규] 닉네임 변경
    def update_nickname(self, user_row_idx, new_nickname):
        if not self.check_connection(): return False
        try:
            # H열(8번째)이 nickname이라고 가정
            self.users_ws.update_cell(user_row_idx, 8, new_nickname)
            return True
        except: return False

    # [신규] 퀘스트 그룹 삭제
    def delete_quest_group(self, title_prefix):
        if not self.check_connection(): return False
        try:
            records = self.quests_ws.get_all_records()
            # 삭제할 행 번호들을 찾음 (뒤에서부터 지워야 인덱스가 안 꼬임)
            rows_to_delete = []
            for i, row in enumerate(records):
                q_name = str(row.get('quest_name', ''))
                # "민법-1", "민법-제2조" 등에서 앞부분이 일치하면 삭제 대상
                if q_name.startswith(title_prefix + "-"):
                    rows_to_delete.append(i + 2) # 헤더 고려 +2
            
            rows_to_delete.sort(reverse=True) # 뒤에서부터 삭제
            for row_idx in rows_to_delete:
                self.quests_ws.delete_rows(row_idx)
            return True
        except: return False

    def get_quest_list(self):
        if not self.check_connection(): return []
        try: return self.quests_ws.get_all_records()
        except: return []

    def update_quest_content(self, quest_name, new_content):
        if not self.check_connection(): return False
        try:
            cell = self.quests_ws.find(quest_name, in_column=1) 
            if cell:
                self.quests_ws.update_cell(cell.row, 2, new_content)
                return True
        except: return False
        
    def save_split_quests(self, title_prefix, lines, creator):
        if not self.check_connection(): return False
        try:
            today = str(datetime.date.today())
            rows_to_add = []
            existing = [str(r.get('quest_name')) for r in self.quests_ws.get_all_records()]
            
            for i, line in enumerate(lines):
                if not line.strip(): continue 
                match = re.match(r'^((?:령)?제\s*\d+\s*조(?:\(.*?\))?)', line.strip())
                if match: suffix = match.group(1) 
                else: suffix = str(i + 1) 
                
                quest_name = f"{title_prefix}-{suffix}"
                if quest_name in existing: continue
                rows_to_add.append([quest_name, line[:45000], creator, today])
                
            if rows_to_add:
                self.quests_ws.append_rows(rows_to_add)
                return True, len(rows_to_add)
            return False, 0
        except: return False, 0

    def process_reward(self, user_id, card_text, current_level, current_xp, row_idx, quest_name):
        if not self.check_connection(): return "ERROR", 0, current_level, current_xp, "ERROR", 0
        records = self.collections_ws.get_all_records()
        found_idx = -1
        current_count = 0
        current_grade = "NORMAL"

        for i, row in enumerate(records):
            if str(row['user_id']) == str(user_id) and row['card_text'] == card_text and row.get('quest_name') == quest_name:
                found_idx = i + 2
                current_count = row.get('count', 1)
                current_grade = row.get('grade', 'NORMAL')
                break
        
        status = ""; final_grade = current_grade
        if found_idx != -1:
            new_count = current_count + 1
            if new_count >= 10: new_grade = "LEGEND"
            elif new_count >= 5: new_grade = "RARE"
            else: new_grade = current_grade
            self.collections_ws.update_cell(found_idx, 6, new_count)
            self.collections_ws.update_cell(found_idx, 3, new_grade)
            status = "UPGRADE"; final_grade = new_grade; xp_gain = 10 + (new_count * 2)
        else:
            rand = random.random()
            if rand < 0.05: final_grade = "LEGEND"
            elif rand < 0.20: final_grade = "RARE"
            else: final_grade = "NORMAL"
            self.collections_ws.append_row([user_id, card_text, final_grade, str(datetime.date.today()), quest_name, 1])
            status = "NEW"; xp_gain = 50 if final_grade == "LEGEND" else 30 if final_grade == "RARE" else 20

        new_xp = current_xp + xp_gain
        new_level, req_xp = current_level, current_level * 100
        if new_xp >= req_xp: new_level += 1; new_xp -= req_xp
            
        self.users_ws.update_cell(row_idx, 3, new_level)
        self.users_ws.update_cell(row_idx, 4, new_xp)
        return final_grade, xp_gain, new_level, new_xp, status, current_count + 1 if found_idx != -1 else 1

    def get_collections(self, user_id):
        if not self.check_connection(): return []
        try: return [c for c in self.collections_ws.get_all_records() if str(c['user_id']) == str(user_id)]
        except: return []

    def exchange_card(self, user_id, user_row_idx, card_text, current_points, current_level):
        if not self.check_connection(): return False, 0, 0
        records = self.collections_ws.get_all_records()
        found_idx = -1
        target_row = None
        for i, row in enumerate(records):
            if str(row['user_id']) == str(user_id) and row['card_text'] == card_text:
                found_idx = i + 2; target_row = row; break
        
        if found_idx != -1 and target_row['count'] > 0:
            grade = target_row.get('grade', 'NORMAL')
            base_point = 10
            if grade == 'RARE': base_point = 50
            elif grade == 'LEGEND': base_point = 200
            
            earned_points = base_point * current_level
            new_count = target_row['count'] - 1
            if new_count <= 0: self.collections_ws.delete_rows(found_idx)
            else: self.collections_ws.update_cell(found_idx, 6, new_count)
            new_total_points = current_points + earned_points
            self.users_ws.update_cell(user_row_idx, 7, new_total_points)
            return True, earned_points, new_total_points
        return False, 0, current_points

gm = GoogleSheetManager()

def parse_manual_blanks(text):
    pattern = r'\{([^}]+)\}'
    parts = []
    targets = []
    last_idx = 0
    input_id = 0
    for match in re.finditer(pattern, text):
        start, end = match.span()
        answer = match.group(1).strip()
        if start > last_idx: parts.append({'type': 'text', 'val': text[last_idx:start]})
        if answer: parts.append({'type': 'input', 'id': input_id}); targets.append(answer); input_id += 1
        last_idx = end
    if last_idx < len(text): parts.append({'type': 'text', 'val': text[last_idx:]})
    return parts, targets

def natural_sort_key(q):
    name = q.get('quest_name', '')
    if '-' in name:
        prefix, suffix = name.rsplit('-', 1)
        nums = re.findall(r'\d+', suffix)
        if nums:
            return (prefix, int(nums[0]), suffix) 
        return (prefix, 0, suffix)
    return (name, 0, "")

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
        user_email = user_info['email']
        
        if not gm.check_connection():
            flash("🚨 서버 오류: DB 연결 실패")
            return redirect(url_for('index'))

        user_data, row_idx = gm.get_user_by_id(user_email)
        
        if not user_data:
            success, msg = gm.register_social(user_email)
            if not success:
                flash(f"🚫 가입 실패: {msg}") 
                return redirect(url_for('index'))
            user_data, row_idx = gm.get_user_by_id(user_email)
            
        if user_data:
            session['user_id'] = user_email
            session['user_row_idx'] = row_idx
            # 세션에 최신 정보 저장
            session['level'] = user_data.get('level', 1)
            session['xp'] = user_data.get('xp', 0)
            session['points'] = user_data.get('points', 0)
            session['nickname'] = user_data.get('nickname', user_email.split('@')[0])
            
            flash(f"환영합니다, {session['nickname']}님!")
            return redirect(url_for('lobby'))
        else:
            flash("🚨 정보 로드 실패.")
            
    except Exception as e:
        flash(f"구글 로그인 오류: {e}")
    return redirect(url_for('index'))

@app.route('/logout')
def logout(): 
    session.clear()
    return redirect(url_for('index'))

@app.route('/lobby')
def lobby():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    # [수정] 로비 진입 시 DB에서 최신 정보 강제 로드 (XP 동기화 문제 해결)
    user_data, _ = gm.get_user_by_id(session['user_id'])
    if user_data:
        session['level'] = user_data.get('level', 1)
        session['xp'] = user_data.get('xp', 0)
        session['points'] = user_data.get('points', 0)
        session['nickname'] = user_data.get('nickname', session['user_id'].split('@')[0])

    return render_template('lobby.html', 
                           nickname=session.get('nickname'), 
                           level=session['level'], 
                           xp=session['xp'], 
                           points=session.get('points', 0),
                           req_xp=session['level']*100)

# [신규] 닉네임 변경 API
@app.route('/update_nickname', methods=['POST'])
def update_nickname():
    if 'user_id' not in session: return redirect(url_for('index'))
    new_nick = request.form.get('new_nickname')
    if new_nick:
        if gm.update_nickname(session['user_row_idx'], new_nick):
            session['nickname'] = new_nick
            flash("닉네임이 변경되었습니다!")
        else:
            flash("변경 실패")
    return redirect(url_for('lobby'))

@app.route('/dungeon', methods=['GET', 'POST'])
def dungeon():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    if request.method == 'POST':
        # [신규] 법령 삭제 요청 처리
        if 'delete_group' in request.form:
            target_group = request.form['delete_group']
            if gm.delete_quest_group(target_group):
                flash(f"🗑️ '{target_group}' 법령이 삭제되었습니다.")
            else:
                flash("❌ 삭제 실패")
            return redirect(url_for('dungeon'))

        elif 'quest_select' in request.form:
            q_name = request.form['quest_select']
            quests = gm.get_quest_list()
            selected_quest = next((q for q in quests if str(q.get('quest_name', '')).strip() == str(q_name).strip()), None)
            
            if not selected_quest: return redirect(url_for('dungeon'))

            content = selected_quest.get('content', "")
            user_id = session['user_id']
            ACTIVE_GAMES[user_id] = {
                'content': content, 
                'quest_name': q_name,
                'curr_targets': [],
                'edit_mode': False 
            }
            if '{' not in content:
                ACTIVE_GAMES[user_id]['edit_mode'] = True
                flash("빈칸을 뚫어주세요!")
            return redirect(url_for('dungeon_play'))
            
        elif 'new_q_name' in request.form:
            title_prefix = request.form['new_q_name']
            f = request.files.get('new_q_file')
            if title_prefix and f:
                raw_text = f.read().decode('utf-8')
                lines = raw_text.replace('\r\n', '\n').split('\n')
                success, count = gm.save_split_quests(title_prefix, lines, session['user_id'])
                if success: flash(f"{count}개의 스테이지 생성 완료!")
                else: flash("저장 실패")
            return redirect(url_for('dungeon'))

    quests = gm.get_quest_list()
    quests.sort(key=natural_sort_key)
    return render_template('dungeon.html', quests=quests)

@app.route('/dungeon/play', methods=['GET', 'POST'])
def dungeon_play():
    if 'user_id' not in session: return redirect(url_for('index'))
    user_id = session['user_id']
    game_data = ACTIVE_GAMES.get(user_id)
    if not game_data: return redirect(url_for('dungeon'))
    
    content = game_data['content']
    
    if game_data['edit_mode']:
        if request.method == 'GET':
            return render_template('dungeon_play.html', edit_mode=True, raw_content=content)
        elif request.method == 'POST':
            new_content = request.form.get('edited_content')
            if new_content:
                gm.update_quest_content(game_data['quest_name'], new_content)
                game_data['content'] = new_content
                game_data['edit_mode'] = False
                return redirect(url_for('dungeon_play'))
    else:
        if request.method == 'GET':
            parts, targets = parse_manual_blanks(content)
            game_data['curr_targets'] = targets
            return render_template('dungeon_play.html', edit_mode=False, parts=parts, targets=targets, has_blanks=len(targets) > 0)

        elif request.method == 'POST':
            penalty_count = int(request.form.get('penalty_count', 0))
            clean_text = re.sub(r'\{([^}]+)\}', r'\1', content)
            
            g, base_gain, nl, nx, stat, cnt = gm.process_reward(
                session['user_id'], clean_text, session['level'], session['xp'], 
                session['user_row_idx'], game_data['quest_name']
            )
            final_gain = max(1, base_gain - (penalty_count * 2))
            
            # [수정] 세션 즉시 업데이트 (여기서도 하지만 로비 갈때 한번 더 체크함)
            session['level'] = nl
            session['xp'] = nx - (base_gain - final_gain)
            
            flash(f"🎉 클리어! (+{final_gain} XP)")
            return redirect(url_for('dungeon'))

@app.route('/collection')
def collection():
    if 'user_id' not in session: return redirect(url_for('index'))
    cards = gm.get_collections(session['user_id'])
    return render_template('collection.html', cards=cards, points=session.get('points', 0))

@app.route('/exchange', methods=['POST'])
def exchange():
    if 'user_id' not in session: return jsonify({'success': False, 'msg': '로그인 필요'})
    data = request.get_json()
    card_text = data.get('card_text')
    success, earned, total = gm.exchange_card(
        session['user_id'], session['user_row_idx'], card_text, session.get('points', 0), session['level']
    )
    if success:
        session['points'] = total
        return jsonify({'success': True, 'earned': earned, 'new_total': total})
    return jsonify({'success': False, 'msg': '교환 실패'})

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(host='0.0.0.0', port=10000)
