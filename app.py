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
import traceback # 에러 추적용

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
        self.client = None
        self.sheet = None
        self.users_ws = None
        self.quests_ws = None
        self.collections_ws = None
        self.abbrev_ws = None
        self.USER_HEADERS = ["user_id", "password", "level", "xp", "title", "last_idx", "points", "nickname"]
        self.QUEST_HEADERS = ["quest_name", "content", "creator", "date"]
        self.COLLECTION_HEADERS = ["user_id", "card_text", "grade", "date", "quest_name", "level", "type"]
        self.ABBREV_HEADERS = ["user_id", "term", "meaning", "date"]
        self.connect_db() 

    def connect_db(self):
        try:
            json_creds = os.environ.get('GCP_CREDENTIALS')
            if not json_creds: 
                print("❌ GCP_CREDENTIALS 환경변수 없음")
                return False
            
            creds_dict = json.loads(json_creds)
            scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open("memory_game_db")
            
            # [핵심] 시트가 없거나 비어있으면 헤더(제목줄) 강제 주입
            self.users_ws = self._get_or_create_sheet("users", self.USER_HEADERS)
            self.collections_ws = self._get_or_create_sheet("collections", self.COLLECTION_HEADERS)
            self.quests_ws = self._get_or_create_sheet("quests", self.QUEST_HEADERS)
            self.abbrev_ws = self._get_or_create_sheet("abbreviations", self.ABBREV_HEADERS)

            print("✅ DB 연결 및 헤더 확인 완료")
            return True
        except Exception as e:
            print(f"❌ DB 연결 실패: {e}")
            return False

    def _get_or_create_sheet(self, title, headers):
        try:
            ws = self.sheet.worksheet(title)
            # 내용이 아예 없으면 헤더 추가
            if not ws.get_all_values():
                ws.append_row(headers)
            return ws
        except:
            # 시트가 없으면 생성 후 헤더 추가
            ws = self.sheet.add_worksheet(title, 100, 10)
            ws.append_row(headers)
            return ws

    def ensure_connection(self):
        try:
            self.users_ws.acell('A1')
            return True
        except:
            print("🔄 DB 재연결 시도...")
            return self.connect_db()

    def get_safe_records(self, worksheet):
        try:
            self.ensure_connection()
            rows = worksheet.get_all_values()
            if len(rows) < 2: return [] # 헤더만 있거나 비어있으면 빈 리스트
            headers = rows[0]
            records = []
            for row in rows[1:]:
                # 행 길이가 헤더보다 짧으면 빈카드로 채움 (인덱스 에러 방지)
                padded = row + [""] * (len(headers) - len(row))
                records.append(dict(zip(headers, padded)))
            return records
        except: return []

    def get_user_by_id(self, user_id):
        if not self.ensure_connection(): return None, None
        try:
            records = self.get_safe_records(self.users_ws)
            for i, row in enumerate(records):
                if str(row.get('user_id')) == str(user_id):
                    row['points'] = int(row.get('points') or 0)
                    row['level'] = int(row.get('level') or 1)
                    row['xp'] = int(row.get('xp') or 0)
                    if not row.get('nickname'): row['nickname'] = str(user_id).split('@')[0]
                    # i는 데이터 인덱스(0부터). 실제 시트 행은 헤더(1) + 0-based(i) + 1 = i + 2
                    return row, i + 2
        except: pass
        return None, None

    def register_social(self, user_id):
        if not self.ensure_connection(): return False
        try:
            if self.get_user_by_id(user_id)[0]: return True
            nick = user_id.split('@')[0]
            self.users_ws.append_row([user_id, "SOCIAL", 1, 0, "빈칸 견습생", 0, 0, nick])
            return True
        except: return False

    def update_nickname(self, row_idx, new_nick):
        if self.ensure_connection():
            try: self.users_ws.update_cell(row_idx, 8, new_nick); return True
            except: return False
        return False

    def save_manual_quest(self, title, content, creator):
        if not self.ensure_connection(): return False
        try:
            today = str(datetime.date.today())
            existing = [str(r.get('quest_name')) for r in self.get_safe_records(self.quests_ws)]
            final_title = title
            dup_cnt = 0
            while final_title in existing:
                dup_cnt += 1
                final_title = f"{title}_{dup_cnt}"
            self.quests_ws.append_row([final_title, content, creator, today])
            return True
        except: return False

    def save_split_quests(self, title_prefix, file_obj, creator):
        if not self.ensure_connection(): return False, 0
        try:
            today = str(datetime.date.today())
            existing = [str(r.get('quest_name')) for r in self.get_safe_records(self.quests_ws)]
            rows_to_add = []
            
            file_obj.seek(0)
            try: raw_text = file_obj.read().decode('utf-8')
            except: file_obj.seek(0); raw_text = file_obj.read().decode('cp949')

            f_stream = StringIO(raw_text)
            
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
                    
                    snippet = text.split('\n')[0][:15].replace(" ", "")
                    q_name = f"{title_prefix}-{snippet}"
                    
                    dup_count = 0; temp_name = q_name
                    while any(r[0] == temp_name for r in rows_to_add) or temp_name in existing:
                        dup_count += 1; temp_name = f"{q_name}_{dup_count}"
                    
                    rows_to_add.append([temp_name, text, creator, today])
            else:
                normalized_text = raw_text.replace('\r\n', '\n')
                blocks = re.split(r'\n\s*\n', normalized_text)

                for block in blocks:
                    clean_block = block.strip()
                    if not clean_block: continue
                    
                    art_match = re.search(r'^\s*(?:[^\s]+\s+)?((?:령)?제\s*\d+(?:의\d+)?\s*조(?:\s*\(.*?\))?)', clean_block)
                    cir_match = re.match(r'^([①-⑮])', clean_block)

                    if art_match:
                        snippet = art_match.group(1).replace(" ", "")
                    elif cir_match:
                        snippet = f"항목-{cir_match.group(1)}"
                    else:
                        first_line = clean_block.split('\n')[0]
                        snippet = first_line[:15].replace(" ", "")
                        snippet = re.sub(r'[\\/*?:"<>|]', '', snippet)
                    
                    q_name = f"{title_prefix}-{snippet}"
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
        if not self.ensure_connection(): return False
        try:
            records = self.get_safe_records(self.quests_ws)
            to_del = []
            for i, r in enumerate(records):
                if str(r.get('quest_name')).startswith(prefix+"-") or str(r.get('quest_name')) == prefix:
                    to_del.append(i + 2)
            for idx in sorted(to_del, reverse=True): self.quests_ws.delete_rows(idx)
            return True
        except: return False

    def get_quest_list(self):
        if not self.ensure_connection(): return []
        return self.get_safe_records(self.quests_ws)

    def get_my_progress(self, user_id):
        if not self.ensure_connection(): return []
        try:
            col_records = self.get_safe_records(self.collections_ws)
            return [r for r in col_records if str(r.get('user_id')) == str(user_id)]
        except: return []

    def get_available_quests(self, user_id, mode):
        if not self.ensure_connection(): return []
        try:
            all_quests = self.get_safe_records(self.quests_ws)
            my_cards = self.get_my_progress(user_id)
            my_quest_names = [c.get('quest_name') for c in my_cards if c.get('type') == 'BLANK']
            
            if mode == 'acquire': return [q for q in all_quests if q.get('quest_name') not in my_quest_names]
            elif mode == 'review': return my_cards 
            elif mode == 'abbrev': return [c for c in my_cards if int(c.get('level', 0)) >= 1]
        except: return []

    def process_result(self, user_id, row_idx, quest_name, content, mode):
        if not self.ensure_connection(): return 0, 0
        
        try:
            # 1. 유저 데이터 확인 (없으면 복구)
            user_data, fresh_row_idx = self.get_user_by_id(user_id)
            if not user_data:
                self.register_social(user_id)
                user_data, fresh_row_idx = self.get_user_by_id(user_id)
            
            if not user_data: return 1, 0

            records = self.get_safe_records(self.collections_ws)
            target_type = 'ABBREV' if mode == 'abbrev' else 'BLANK'
            found_idx = -1; current_level = 0
            
            for i, row in enumerate(records):
                if str(row.get('user_id')) == str(user_id) and row.get('quest_name') == quest_name and row.get('type') == target_type:
                    found_idx = i + 2; current_level = int(row.get('level') or 0); break
            
            xp_gain = 0
            if found_idx == -1: 
                grade = "RARE" if mode == 'abbrev' else "NORMAL"
                self.collections_ws.append_row([user_id, content, grade, str(datetime.date.today()), quest_name, 1, target_type])
                xp_gain = 100 if mode == 'abbrev' else 50
            else: 
                self.collections_ws.update_cell(found_idx, 6, current_level + 1)
                xp_gain = 30 if mode == 'abbrev' else (20 + current_level * 5)

            u_xp = int(user_data.get('xp', 0))
            u_lv = int(user_data.get('level', 1))
            new_xp = u_xp + xp_gain
            req = u_lv * 100
            if new_xp >= req: u_lv += 1; new_xp -= req
            
            self.users_ws.update_cell(fresh_row_idx, 3, u_lv)
            self.users_ws.update_cell(fresh_row_idx, 4, new_xp)
            return u_lv, new_xp
            
        except Exception as e:
            print(f"Process Result Error: {e}")
            raise e # 에러를 상위로 던져서 화면에 표시

    def update_quest_content(self, quest_name, new_content):
        if not self.ensure_connection(): return False
        try:
            cell = self.quests_ws.find(quest_name, in_column=1) 
            if cell: self.quests_ws.update_cell(cell.row, 2, new_content); return True
        except: return False

    def get_abbreviations(self, user_id):
        if not self.ensure_connection(): return []
        records = self.get_safe_records(self.abbrev_ws)
        return [r for r in records if str(r.get('user_id')) == str(user_id)]

    def add_abbreviation(self, user_id, term, meaning):
        if not self.ensure_connection(): return False
        self.abbrev_ws.append_row([user_id, term, meaning, str(datetime.date.today())])
        return True

    def delete_abbreviation(self, user_id, term):
        if not self.ensure_connection(): return False
        records = self.get_safe_records(self.abbrev_ws)
        for i, r in enumerate(records):
            if str(r.get('user_id')) == str(user_id) and r.get('term') == term:
                self.abbrev_ws.delete_rows(i + 2); return True
        return False

gm = GoogleSheetManager()

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
        if not gm.ensure_connection(): return "DB Error", 500
        gm.register_social(email)
        user, idx = gm.get_user_by_id(email)
        session['user_id'] = email; session['user_row_idx'] = idx
        return redirect(url_for('lobby'))
    except Exception as e: return f"Login Error: {e}", 500

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
    my_progress = gm.get_my_progress(session['user_id'])
    my_completed = [c['quest_name'] for c in my_progress if c['type'] == 'BLANK']
    return render_template('zone_generate.html', quests=quests, my_completed=my_completed)

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
    return render_template('zone_list.html', title="약어 훈련소", quests=cards, mode='abbrev')

@app.route('/play', methods=['GET', 'POST'])
def play_game():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    # [에러 추적] 게임 데이터가 없는 경우 로비로
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
        # [핵심] 500 에러 추적을 위한 try-except 블록
        try:
            clean = game['content']
            if game['mode'] != 'abbrev': clean = re.sub(r'\{([^}]+)\}', r'\1', game['content'])
            
            lv, xp = gm.process_result(session['user_id'], session.get('user_row_idx'), game['quest_name'], clean, game['mode'])
            
            session['level'] = lv; session['xp'] = xp
            flash(f"학습 완료! (현재 Lv.{lv})")
            return_zone = 'review' if game['mode'] == 'review' else ('abbrev' if game['mode'] == 'abbrev' else 'acquire')
            return redirect(url_for(f"zone_{return_zone}"))
        except Exception as e:
            # 에러 발생 시, 500 페이 대신 에러 내용을 화면에 출력
            error_msg = traceback.format_exc()
            return f"<h3>⚠️ 오류가 발생했습니다.</h3><pre>{error_msg}</pre><br><a href='/lobby'>로비로 돌아가기</a>"

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
