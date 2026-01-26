import os
import json
import random
import datetime
import re
import csv
from io import StringIO
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
        self.abbrev_ws = None
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
            except: self.users_ws = self.sheet.add_worksheet("users", 100, 10); self.users_ws.append_row(self.USER_HEADERS)
            try: self.collections_ws = self.sheet.worksheet("collections")
            except: self.collections_ws = self.sheet.add_worksheet("collections", 100, 10); self.collections_ws.append_row(["user_id", "card_text", "grade", "date", "quest_name", "level", "type"])
            try: self.quests_ws = self.sheet.worksheet("quests")
            except: self.quests_ws = self.sheet.add_worksheet("quests", 100, 5)
            try: self.abbrev_ws = self.sheet.worksheet("abbreviations")
            except: self.abbrev_ws = self.sheet.add_worksheet("abbreviations", 100, 10); self.abbrev_ws.append_row(["user_id", "term", "meaning", "date"])
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

    def save_manual_quest(self, title, content, creator):
        if not self.check_connection(): return False
        try:
            today = str(datetime.date.today())
            existing = [str(r.get('quest_name')) for r in self.quests_ws.get_all_records()]
            final_title = title
            dup_cnt = 0
            while final_title in existing:
                dup_cnt += 1
                final_title = f"{title}_{dup_cnt}"
            self.quests_ws.append_row([final_title, content, creator, today])
            return True
        except: return False

    # [수정됨] 파일을 빈 줄 기준으로 나누는 로직
    def save_split_quests(self, title_prefix, file_obj, creator):
        if not self.check_connection(): return False, 0
        try:
            today = str(datetime.date.today())
            existing = [str(r.get('quest_name')) for r in self.quests_ws.get_all_records()]
            rows_to_add = []
            
            # 파일 읽기
            file_obj.seek(0)
            try: raw_text = file_obj.read().decode('utf-8')
            except: file_obj.seek(0); raw_text = file_obj.read().decode('cp949')

            f_stream = StringIO(raw_text)
            
            # 1. 앙키 파일(탭 구분)인 경우
            if '\t' in raw_text:
                reader = csv.reader(f_stream, delimiter='\t')
                for row in reader:
                    if not row or row[0].startswith('#') or len(row) < 2: continue
                    front = re.sub(r'<br\s*/?>', '\n', row[0])
                    front = re.sub(r'<[^>]+>', '', front).replace('&nbsp;', ' ')
                    back = re.sub(r'<br\s*/?>', ',', row[1])
                    back = re.sub(r'<[^>]+>', '', back).replace('&nbsp;', ' ')
                    answers = [a.strip() for a in re.split(r'[,،、]', back) if a.strip()]
                    text = front
                    for ans in answers: text = re.sub(r'_{2,}', f'{{{ans}}}', text, count=1)
                    
                    # 앙키 카드는 한 줄이 한 카드 (제목은 내용 앞부분으로 자동 생성)
                    snippet = text.split('\n')[0][:15].replace(" ", "")
                    q_name = f"{title_prefix}-{snippet}"
                    
                    dup_count = 0; temp_name = q_name
                    while any(r[0] == temp_name for r in rows_to_add) or temp_name in existing:
                        dup_count += 1; temp_name = f"{q_name}_{dup_count}"
                    
                    rows_to_add.append([temp_name, text, creator, today])

            # 2. 일반 텍스트 파일 (빈 줄로 구분)
            else:
                # 윈도우 줄바꿈(\r\n)을 리눅스(\n)로 통일
                normalized_text = raw_text.replace('\r\n', '\n')
                
                # 빈 줄(\n\n 이상)을 기준으로 블록 나누기
                # 정규식: 엔터가 2번 이상 연속으로 나오면 자름
                blocks = re.split(r'\n\s*\n', normalized_text)

                for block in blocks:
                    clean_block = block.strip()
                    if not clean_block: continue
                    
                    # 제목 정하기 (우선순위: 법령번호 -> 첫줄내용)
                    art_match = re.search(r'^\s*(?:[^\s]+\s+)?((?:령)?제\s*\d+(?:의\d+)?\s*조(?:\s*\(.*?\))?)', clean_block)
                    cir_match = re.match(r'^([①-⑮])', clean_block)

                    if art_match:
                        # "제1조" 형식이 있으면 그걸 제목으로
                        snippet = art_match.group(1).replace(" ", "")
                    elif cir_match:
                        snippet = f"항목-{cir_match.group(1)}"
                    else:
                        # 형식이 없으면 첫 줄의 앞 15글자를 제목으로 사용
                        first_line = clean_block.split('\n')[0]
                        snippet = first_line[:15].replace(" ", "")
                        # 특수문자 제거 (파일 저장 등을 위해)
                        snippet = re.sub(r'[\\/*?:"<>|]', '', snippet)
                    
                    q_name = f"{title_prefix}-{snippet}"

                    # 중복 이름 처리
                    dup_count = 0; temp_name = q_name
                    while any(r[0] == temp_name for r in rows_to_add) or temp_name in existing:
                        dup_count += 1; temp_name = f"{q_name}_{dup_count}"
                    
                    rows_to_add.append([temp_name, clean_block[:45000], creator, today])
            
            if rows_to_add: 
                self.quests_ws.append_rows(rows_to_add)
                return True, len(rows_to_add)
            return False, 0

        except Exception as e: print(f"Error: {e}"); return False, 0

    def delete_quest_group(self, prefix):
        if not self.check_connection(): return False
        try:
            records = self.quests_ws.get_all_records()
            to_del = [i+2 for i, r in enumerate(records) if str(r.get('quest_name')).startswith(prefix+"-") or str(r.get('quest_name')) == prefix]
            for idx in sorted(to_del, reverse=True): self.quests_ws.delete_rows(idx)
            return True
        except: return False

    def get_quest_list(self):
        if not self.check_connection(): return []
        try: return self.quests_ws.get_all_records()
        except: return []

    def get_my_progress(self, user_id):
        if not self.check_connection(): return []
        try:
            col_records = self.get_safe_records(self.collections_ws)
            return [r for r in col_records if str(r['user_id']) == str(user_id)]
        except: return []

    def get_available_quests(self, user_id, mode):
        if not self.check_connection(): return []
        try:
            all_quests = self.quests_ws.get_all_records()
            my_cards = self.get_my_progress(user_id)
            my_quest_names = [c['quest_name'] for c in my_cards if c['type'] == 'BLANK']
            
            if mode == 'acquire': return [q for q in all_quests if q['quest_name'] not in my_quest_names]
            elif mode == 'review': return my_cards 
            elif mode == 'abbrev': return [c for c in my_cards if int(c.get('level', 0)) >= 1]
        except: return []

    def process_result(self, user_id, row_idx, quest_name, content, mode):
        if not self.check_connection(): return 0, 0
        records = self.collections_ws.get_all_records()
        target_type = 'ABBREV' if mode == 'abbrev' else 'BLANK'
        found_idx = -1; current_level = 0
        
        for i, row in enumerate(records):
            if str(row['user_id']) == str(user_id) and row['quest_name'] == quest_name and row['type'] == target_type:
                found_idx = i + 2; current_level = int(row.get('level') or 0); break
        
        xp_gain = 0
        if found_idx == -1: 
            grade = "RARE" if mode == 'abbrev' else "NORMAL"
            self.collections_ws.append_row([user_id, content, grade, str(datetime.date.today()), quest_name, 1, target_type])
            xp_gain = 100 if mode == 'abbrev' else 50
        else: 
            self.collections_ws.update_cell(found_idx, 6, current_level + 1)
            xp_gain = 30 if mode == 'abbrev' else (20 + current_level * 5)

        user, _ = self.get_user_by_id(user_id)
        if user:
            u_xp, u_lv = int(user['xp']), int(user['level'])
            new_xp = u_xp + xp_gain
            req = u_lv * 100
            if new_xp >= req: u_lv += 1; new_xp -= req
            self.users_ws.update_cell(row_idx, 3, u_lv); self.users_ws.update_cell(row_idx, 4, new_xp)
            return u_lv, new_xp
        return 0, 0

    def update_quest_content(self, quest_name, new_content):
        if not self.check_connection(): return False
        try:
            cell = self.quests_ws.find(quest_name, in_column=1) 
            if cell: self.quests_ws.update_cell(cell.row, 2, new_content); return True
        except: return False

    def get_abbreviations(self, user_id):
        if not self.check_connection(): return []
        records = self.get_safe_records(self.abbrev_ws)
        return [r for r in records if str(r['user_id']) == str(user_id)]

    def add_abbreviation(self, user_id, term, meaning):
        if not self.check_connection(): return False
        self.abbrev_ws.append_row([user_id, term, meaning, str(datetime.date.today())])
        return True

    def delete_abbreviation(self, user_id, term):
        if not self.check_connection(): return False
        records = self.get_safe_records(self.abbrev_ws)
        for i, r in enumerate(records):
            if str(r['user_id']) == str(user_id) and r['term'] == term:
                self.abbrev_ws.delete_rows(i + 2); return True
        return False

gm = GoogleSheetManager()

def natural_sort_key(q):
    name = str(q.get('quest_name', ''))
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', name)]

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
        session['user_id'] = email; session['user_row_idx'] = idx
        return redirect(url_for('lobby'))
    except: return redirect(url_for('index'))

@app.route('/lobby')
def lobby():
    if 'user_id' not in session: return redirect(url_for('index'))
    user, _ = gm.get_user_by_id(session['user_id'])
    if user: session['level'] = user['level']; session['xp'] = user['xp']; session['nickname'] = user['nickname']; session['points'] = user['points']
    return render_template('lobby.html', level=session['level'], xp=session['xp'], points=session['points'], nickname=session['nickname'], req_xp=session['level']*100)

@app.route('/zone/generate', methods=['GET', 'POST'])
def zone_generate():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        if 'delete_group' in request.form:
            gm.delete_quest_group(request.form['delete_group'])
            flash("삭제되었습니다.")
        elif 'new_q_file' in request.files:
            f = request.files['new_q_file']
            ok, cnt = gm.save_split_quests(request.form['new_q_name'], f, session['user_id'])
            if ok: flash(f"{cnt}개 생성 완료!")
            else: flash("생성 실패: 파일 형식을 확인해주세요.")
    quests = gm.get_quest_list()
    quests.sort(key=natural_sort_key)
    return render_template('zone_generate.html', quests=quests)

@app.route('/maker', methods=['GET', 'POST'])
def maker():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'GET':
        q_name = request.args.get('quest_name')
        if not q_name: return redirect(url_for('zone_generate'))
        quests = gm.get_quest_list()
        quest = next((q for q in quests if q['quest_name'] == q_name), None)
        if not quest: return redirect(url_for('zone_generate'))
        return render_template('maker.html', raw_text=quest['content'], title=q_name)
    elif request.method == 'POST':
        q_name = request.form['title']
        content = request.form['final_content']
        if gm.update_quest_content(q_name, content):
            flash("수정되었습니다!")
            return redirect(url_for('zone_generate'))
        else:
            flash("저장 실패")
            return redirect(url_for('maker', quest_name=q_name))

@app.route('/zone/acquire', methods=['GET', 'POST'])
def zone_acquire():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        q_name = request.form['quest_name']
        all_q = gm.get_quest_list()
        quest = next((q for q in all_q if q['quest_name'] == q_name), None)
        if quest:
            ACTIVE_GAMES[session['user_id']] = { 'mode': 'acquire', 'quest_name': q_name, 'content': quest['content'] }
            return redirect(url_for('play_game'))
    quests = gm.get_available_quests(session['user_id'], 'acquire')
    quests.sort(key=natural_sort_key)
    return render_template('zone_list.html', title="획득 구역", quests=quests, mode='acquire')

@app.route('/zone/review', methods=['GET', 'POST'])
def zone_review():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        q_name = request.form['quest_name']
        q_type = request.form.get('quest_type', 'BLANK')
        cards = gm.get_available_quests(session['user_id'], 'review')
        card = next((c for c in cards if c['quest_name'] == q_name and c['type'] == q_type), None)
        if card:
            mode = 'abbrev' if q_type == 'ABBREV' else 'review'
            ACTIVE_GAMES[session['user_id']] = { 'mode': mode, 'quest_name': q_name, 'content': card['card_text'] }
            return redirect(url_for('play_game'))
    cards = gm.get_available_quests(session['user_id'], 'review')
    cards.sort(key=natural_sort_key)
    return render_template('zone_list.html', title="복습 구역", quests=cards, mode='review')

@app.route('/zone/abbrev', methods=['GET', 'POST'])
def zone_abbrev():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        q_name = request.form['quest_name']
        cards = gm.get_available_quests(session['user_id'], 'abbrev')
        card = next((c for c in cards if c['quest_name'] == q_name), None)
        if card:
            ACTIVE_GAMES[session['user_id']] = { 'mode': 'abbrev', 'quest_name': q_name, 'content': card['card_text'] }
            return redirect(url_for('play_game'))
    cards = gm.get_available_quests(session['user_id'], 'abbrev')
    cards.sort(key=natural_sort_key)
    return render_template('zone_list.html', title="약어 훈련소", quests=cards, mode='abbrev')

@app.route('/play', methods=['GET', 'POST'])
def play_game():
    if 'user_id' not in session: return redirect(url_for('index'))
    game = ACTIVE_GAMES.get(session['user_id'])
    if not game: return redirect(url_for('lobby'))

    if request.method == 'GET':
        import re
        content = game['content']
        parts = []
        targets = []
        if game['mode'] == 'abbrev':
            clean = re.sub(r'\{([^}]+)\}', r'\1', content)
            parts = [{'type':'text', 'val': '전체 내용을 입력하세요:'}, {'type':'input', 'id':0}]
            targets = [clean.strip()]
        else:
            last = 0; idx = 0
            for m in re.finditer(r'\{([^}]+)\}', content):
                s, e = m.span()
                if s > last: parts.append({'type':'text', 'val': content[last:s]})
                parts.append({'type':'input', 'id': idx})
                targets.append(m.group(1))
                idx += 1; last = e
            if last < len(content): parts.append({'type':'text', 'val': content[last:]})
        
        return render_template('play.html', parts=parts, targets=targets, mode=game['mode'], title=game['quest_name'])

    elif request.method == 'POST':
        clean = game['content']
        if game['mode'] != 'abbrev': clean = re.sub(r'\{([^}]+)\}', r'\1', game['content'])
        lv, xp = gm.process_result(session['user_id'], session['user_row_idx'], game['quest_name'], clean, game['mode'])
        session['level'] = lv; session['xp'] = xp
        flash(f"학습 완료! (현재 Lv.{lv})")
        return_zone = 'review' if game['mode'] == 'review' else ('abbrev' if game['mode'] == 'abbrev' else 'acquire')
        return redirect(url_for(f"zone_{return_zone}"))

@app.route('/update_nickname', methods=['POST'])
def update_nickname():
    if 'user_id' in session:
        gm.update_nickname(session['user_row_idx'], request.form.get('new_nickname'))
    return redirect(url_for('lobby'))

@app.route('/dungeon/edit_text', methods=['GET', 'POST'])
def edit_quest_text(): return redirect(url_for('lobby')) 

@app.route('/abbreviations', methods=['GET', 'POST'])
def abbreviations():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        if 'delete_term' in request.form:
            gm.delete_abbreviation(session['user_id'], request.form['delete_term'])
        else: gm.add_abbreviation(session['user_id'], request.form.get('term'), request.form.get('meaning'))
        return redirect(url_for('abbreviations'))
    return render_template('abbreviations.html', abbrevs=gm.get_abbreviations(session['user_id']))

@app.route('/sw.js')
def sw(): return app.send_static_file('sw.js')

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(host='0.0.0.0', port=10000)
