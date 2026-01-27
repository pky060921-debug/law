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
import traceback

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
        self.quest_log_ws = None
        
        self.USER_HEADERS = ["user_id", "password", "level", "xp", "title", "last_idx", "points", "nickname"]
        self.QUEST_HEADERS = ["quest_name", "content", "creator", "date"]
        self.COLLECTION_HEADERS = ["user_id", "card_text", "grade", "date", "quest_name", "level", "type"]
        self.ABBREV_HEADERS = ["user_id", "quest_name", "mnemonic", "date"]
        self.QUEST_LOG_HEADERS = ["user_id", "last_daily_login"]
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
            
            self.users_ws = self._get_or_create_sheet("users", self.USER_HEADERS)
            self.collections_ws = self._get_or_create_sheet("collections", self.COLLECTION_HEADERS)
            self.quests_ws = self._get_or_create_sheet("quests", self.QUEST_HEADERS)
            self.abbrev_ws = self._get_or_create_sheet("abbreviations", self.ABBREV_HEADERS)
            self.quest_log_ws = self._get_or_create_sheet("quest_log", self.QUEST_LOG_HEADERS)
            return True
        except Exception as e:
            print(f"DB Error: {e}")
            return False

    def _get_or_create_sheet(self, title, headers):
        try:
            ws = self.sheet.worksheet(title)
            if not ws.get_all_values(): ws.append_row(headers)
            return ws
        except:
            ws = self.sheet.add_worksheet(title, 100, 10)
            ws.append_row(headers)
            return ws

    def ensure_connection(self):
        try:
            self.users_ws.acell('A1')
            return True
        except:
            return self.connect_db()

    def get_safe_records(self, worksheet):
        try:
            self.ensure_connection()
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
        if not self.ensure_connection(): return None, None
        try:
            records = self.get_safe_records(self.users_ws)
            for i, row in enumerate(records):
                if str(row.get('user_id')) == str(user_id):
                    row['points'] = int(row.get('points') or 0)
                    row['level'] = int(row.get('level') or 1)
                    row['xp'] = int(row.get('xp') or 0)
                    if not row.get('nickname'): row['nickname'] = str(user_id).split('@')[0]
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

    def save_split_quests(self, title_prefix, file_obj, creator):
        if not self.ensure_connection(): return False, 0
        try:
            today = str(datetime.date.today())
            existing = [str(r.get('quest_name')) for r in self.get_safe_records(self.quests_ws)]
            rows_to_add = []
            
            file_obj.seek(0)
            try: raw_text = file_obj.read().decode('utf-8')
            except: file_obj.seek(0); raw_text = file_obj.read().decode('cp949')

            normalized_text = raw_text.replace('\r\n', '\n')
            blocks = re.split(r'\n\s*\n', normalized_text)

            for block in blocks:
                clean_block = block.strip()
                if not clean_block: continue
                
                art_match = re.search(r'^\s*(?:[^\s]+\s+)?((?:령)?제\s*\d+(?:의\d+)?\s*조(?:\s*\(.*?\))?)', clean_block)
                cir_match = re.match(r'^([①-⑮])', clean_block)

                if art_match: snippet = art_match.group(1).replace(" ", "")
                elif cir_match: snippet = f"항목-{cir_match.group(1)}"
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

    # [신규] 카드 합치기 (Merge)
    def merge_quests(self, quest_names, creator):
        if not self.ensure_connection() or not quest_names: return False
        try:
            records = self.get_safe_records(self.quests_ws)
            to_merge = []
            to_del_indices = []
            
            # 합칠 카드 찾기
            for i, r in enumerate(records):
                if r.get('quest_name') in quest_names:
                    to_merge.append(r)
                    to_del_indices.append(i + 2)
            
            if not to_merge: return False

            # 내용 합치기 (줄바꿈 두 번으로 구분)
            combined_content = "\n\n".join([q.get('content', '') for q in to_merge])
            
            # 새 제목 생성 (첫 번째 카드 제목 + _합본)
            base_title = to_merge[0].get('quest_name')
            if '-' in base_title: base_prefix = base_title.split('-')[0]
            else: base_prefix = "합본"
            
            new_title = f"{base_prefix}-합본_{datetime.datetime.now().strftime('%H%M%S')}"
            
            # 새 카드 추가
            self.quests_ws.append_row([new_title, combined_content, creator, str(datetime.date.today())])
            
            # 기존 카드 삭제 (뒤에서부터 삭제해야 인덱스 안 꼬임)
            for idx in sorted(to_del_indices, reverse=True):
                self.quests_ws.delete_rows(idx)
                
            return True
        except Exception as e:
            print(f"Merge Error: {e}")
            return False

    # [신규] 카드 나누기 (Split)
    def split_quest_by_paragraph(self, quest_name, creator):
        if not self.ensure_connection(): return False
        try:
            # 1. 원본 찾기
            cell = self.quests_ws.find(quest_name, in_column=1)
            if not cell: return False
            
            row_val = self.quests_ws.row_values(cell.row)
            content = row_val[1] # content column
            
            # 2. 내용 쪼개기 (\n\n 기준)
            blocks = re.split(r'\n\s*\n', content)
            blocks = [b.strip() for b in blocks if b.strip()]
            
            if len(blocks) < 2: return False # 쪼갤게 없으면 중단

            # 3. 새 카드들 추가
            rows_to_add = []
            today = str(datetime.date.today())
            base_name = quest_name.split('_')[0] # 기존 번호 제거 시도
            
            for idx, block in enumerate(blocks):
                new_name = f"{base_name}_part{idx+1}"
                rows_to_add.append([new_name, block, creator, today])
            
            self.quests_ws.append_rows(rows_to_add)
            
            # 4. 원본 삭제
            self.quests_ws.delete_rows(cell.row)
            return True
        except Exception as e:
            print(f"Split Error: {e}")
            return False

    def rename_quest(self, old_name, new_name):
        if not self.ensure_connection(): return False
        try:
            q_cell = self.quests_ws.find(old_name, in_column=1)
            if q_cell: self.quests_ws.update_cell(q_cell.row, 1, new_name)
            else: return False

            try:
                col_cells = self.collections_ws.findall(old_name, in_column=5) 
                for cell in col_cells: self.collections_ws.update_cell(cell.row, 5, new_name)
            except: pass

            try:
                abb_cells = self.abbrev_ws.findall(old_name, in_column=2) 
                for cell in abb_cells: self.abbrev_ws.update_cell(cell.row, 2, new_name)
            except: pass
            
            return True
        except Exception as e:
            print(f"Rename Error: {e}")
            return False

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
            try:
                if found_idx == -1: 
                    grade = "RARE" if mode == 'abbrev' else "NORMAL"
                    self.collections_ws.append_row([user_id, content, grade, str(datetime.date.today()), quest_name, 1, target_type])
                    xp_gain = 100 if mode == 'abbrev' else 50
                else: 
                    self.collections_ws.update_cell(found_idx, 6, current_level + 1)
                    xp_gain = 30 if mode == 'abbrev' else (20 + current_level * 5)
            except gspread.exceptions.APIError:
                self.connect_db() 
                if found_idx == -1:
                    self.collections_ws.append_row([user_id, content, grade, str(datetime.date.today()), quest_name, 1, target_type])
                else:
                    self.collections_ws.update_cell(found_idx, 6, current_level + 1)

            u_lv, new_xp = self.add_xp(user_id, xp_gain, user_data, fresh_row_idx)
            return u_lv, new_xp
        except Exception as e:
            print(f"Process Result Error: {e}")
            raise e

    def add_xp(self, user_id, amount, user_data=None, row_idx=None):
        if not self.ensure_connection(): return 1, 0
        if not user_data or not row_idx:
            user_data, row_idx = self.get_user_by_id(user_id)
            if not user_data: return 1, 0

        try:
            u_xp = int(user_data.get('xp', 0))
            u_lv = int(user_data.get('level', 1))
            new_xp = u_xp + amount
            req = u_lv * 100
            
            while new_xp >= req:
                u_lv += 1
                new_xp -= req
                req = u_lv * 100
                
            self.users_ws.update_cell(row_idx, 3, u_lv)
            self.users_ws.update_cell(row_idx, 4, new_xp)
            return u_lv, new_xp
        except gspread.exceptions.APIError:
            self.connect_db()
            self.users_ws.update_cell(row_idx, 3, u_lv)
            self.users_ws.update_cell(row_idx, 4, new_xp)
            return u_lv, new_xp

    def update_quest_content(self, quest_name, new_content):
        if not self.ensure_connection(): return False
        try:
            cell = self.quests_ws.find(quest_name, in_column=1) 
            if cell: self.quests_ws.update_cell(cell.row, 2, new_content); return True
        except: return False

    def save_mnemonic(self, user_id, quest_name, mnemonic):
        if not self.ensure_connection(): return False
        try:
            records = self.get_safe_records(self.abbrev_ws)
            for i, r in enumerate(records):
                if str(r.get('user_id')) == str(user_id) and r.get('quest_name') == quest_name:
                    self.abbrev_ws.update_cell(i + 2, 3, mnemonic)
                    return True
            self.abbrev_ws.append_row([user_id, quest_name, mnemonic, str(datetime.date.today())])
            return True
        except: return False

    def get_mnemonic(self, user_id, quest_name):
        if not self.ensure_connection(): return None
        records = self.get_safe_records(self.abbrev_ws)
        for r in records:
            if str(r.get('user_id')) == str(user_id) and r.get('quest_name') == quest_name:
                return r.get('mnemonic')
        return None

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

    def reset_user_data(self, user_id):
        if not self.ensure_connection(): return False
        try:
            col_rows = self.collections_ws.get_all_values()
            to_del_col = [i + 1 for i, row in enumerate(col_rows) if i > 0 and str(row[0]) == str(user_id)]
            for r in sorted(to_del_col, reverse=True): self.collections_ws.delete_rows(r)

            abb_rows = self.abbrev_ws.get_all_values()
            to_del_abb = [i + 1 for i, row in enumerate(abb_rows) if i > 0 and str(row[0]) == str(user_id)]
            for r in sorted(to_del_abb, reverse=True): self.abbrev_ws.delete_rows(r)
            
            ql_rows = self.quest_log_ws.get_all_values()
            to_del_ql = [i + 1 for i, row in enumerate(ql_rows) if i > 0 and str(row[0]) == str(user_id)]
            for r in sorted(to_del_ql, reverse=True): self.quest_log_ws.delete_rows(r)

            cell = self.users_ws.find(user_id, in_column=1)
            if cell:
                self.users_ws.update_cell(cell.row, 3, 1) 
                self.users_ws.update_cell(cell.row, 4, 0)
            
            return True
        except Exception as e:
            print(f"Reset Error: {e}")
            return False

    def check_daily_login(self, user_id):
        if not self.ensure_connection(): return False
        today = str(datetime.date.today())
        records = self.get_safe_records(self.quest_log_ws)
        for r in records:
            if str(r.get('user_id')) == str(user_id):
                return r.get('last_daily_login') == today
        return False

    def claim_daily_login(self, user_id):
        if not self.ensure_connection(): return False, 0, 0
        today = str(datetime.date.today())
        records = self.get_safe_records(self.quest_log_ws)
        found = False
        for i, r in enumerate(records):
            if str(r.get('user_id')) == str(user_id):
                if r.get('last_daily_login') == today: return False, 0, 0
                self.quest_log_ws.update_cell(i + 2, 2, today)
                found = True
                break
        if not found: self.quest_log_ws.append_row([user_id, today])
        lv, xp = self.add_xp(user_id, 50)
        return True, lv, xp

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
    if user: 
        session['level'] = user['level']; session['xp'] = user['xp']
        session['nickname'] = user['nickname']; session['points'] = user['points']
    daily_checked = gm.check_daily_login(session['user_id'])
    return render_template('lobby.html', level=session['level'], xp=session['xp'], 
                           points=session['points'], nickname=session['nickname'], 
                           req_xp=session['level']*100, daily_checked=daily_checked)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/reset_progress', methods=['POST'])
def reset_progress():
    if 'user_id' not in session: return redirect(url_for('index'))
    if gm.reset_user_data(session['user_id']):
        session['level'] = 1; session['xp'] = 0
        flash("모든 학습 내용이 초기화되었습니다.")
    else: flash("초기화 실패. 잠시 후 다시 시도해주세요.")
    return redirect(url_for('lobby'))

@app.route('/claim_daily_login', methods=['POST'])
def claim_daily_login():
    if 'user_id' not in session: return redirect(url_for('index'))
    ok, lv, xp = gm.claim_daily_login(session['user_id'])
    if ok:
        session['level'] = lv; session['xp'] = xp
        flash("출석 보상 50 XP 획득!")
    else: flash("이미 출석 보상을 받았습니다.")
    return redirect(url_for('lobby'))

@app.route('/zone/generate', methods=['GET', 'POST'])
def zone_generate():
    if 'user_id' not in session: return redirect(url_for('index'))
    try:
        if request.method == 'POST':
            if 'delete_group' in request.form:
                gm.delete_quest_group(request.form['delete_group'])
                flash("삭제되었습니다.")
            elif 'rename_old' in request.form:
                old = request.form['rename_old']
                new = request.form['rename_new']
                if gm.rename_quest(old, new): flash("제목 수정 완료!")
                else: flash("수정 실패 (존재하지 않거나 DB 오류)")
            elif 'new_q_file' in request.files:
                f = request.files['new_q_file']
                ok, cnt = gm.save_split_quests(request.form['new_q_name'], f, session['user_id'])
                if ok: flash(f"{cnt}개 생성 완료!")
                else: flash("생성 실패: 파일 형식을 확인해주세요.")
            # [신규] 카드 합치기 처리
            elif 'merge_targets' in request.form:
                targets = request.form.getlist('merge_targets')
                if len(targets) > 1:
                    if gm.merge_quests(targets, session['user_id']):
                        flash(f"{len(targets)}개의 카드가 합쳐졌습니다!")
                    else: flash("합치기 실패.")
                else: flash("합칠 카드를 2개 이상 선택하세요.")
        
        quests = gm.get_quest_list()
        my_progress = gm.get_my_progress(session['user_id'])
        my_completed = [c.get('quest_name') for c in my_progress if c.get('type') == 'BLANK']
        return render_template('zone_generate.html', quests=quests, my_completed=my_completed)
    except Exception as e:
        return f"<h3>⚠️ 생성 구역 오류</h3><pre>{traceback.format_exc()}</pre><br><a href='/lobby'>로비로</a>"

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
        # [신규] 카드 나누기 처리
        if 'split_action' in request.form:
            q_name = request.form['title']
            if gm.split_quest_by_paragraph(q_name, session['user_id']):
                flash("문단별 나누기 완료!")
                return redirect(url_for('zone_generate'))
            else:
                flash("나누기 실패 (빈 줄로 구분된 문단이 없거나 DB 오류)")
                return redirect(url_for('maker', quest_name=q_name))
        
        # 기존 저장 로직
        old_title = request.form.get('old_title')
        new_title = request.form.get('title')
        content = request.form['final_content']
        
        if old_title and new_title and old_title != new_title:
            gm.rename_quest(old_title, new_title)
            current_title = new_title
        else:
            current_title = old_title if old_title else new_title

        if gm.update_quest_content(current_title, content):
            flash("생성완료.")
            return redirect(url_for('zone_generate'))
        else:
            flash("저장 실패")
            return redirect(url_for('maker', quest_name=current_title))

@app.route('/zone/acquire', methods=['GET', 'POST'])
def zone_acquire():
    if 'user_id' not in session: return redirect(url_for('index'))
    try:
        if request.method == 'POST':
            q_name = request.form['quest_name']
            all_q = gm.get_quest_list()
            quest = next((q for q in all_q if q['quest_name'] == q_name), None)
            if quest:
                ACTIVE_GAMES[session['user_id']] = { 'mode': 'acquire', 'quest_name': q_name, 'content': quest['content'] }
                return redirect(url_for('play_game'))
        quests = gm.get_available_quests(session['user_id'], 'acquire')
        return render_template('zone_list.html', title="획득 구역", quests=quests, mode='acquire')
    except Exception as e:
        return f"<h3>⚠️ 획득 구역 오류</h3><pre>{traceback.format_exc()}</pre><br><a href='/lobby'>로비로</a>"

@app.route('/zone/review', methods=['GET', 'POST'])
def zone_review():
    if 'user_id' not in session: return redirect(url_for('index'))
    try:
        if request.method == 'POST':
            q_name = request.form['quest_name']
            q_type = request.form.get('quest_type', 'BLANK')
            cards = gm.get_available_quests(session['user_id'], 'review')
            card = next((c for c in cards if c.get('quest_name') == q_name and c.get('type') == q_type), None)
            
            if card:
                mode = 'abbrev' if q_type == 'ABBREV' else 'review'
                level = int(card.get('level', 1))
                if level == 5:
                    mode = 'register_mnemonic'

                ACTIVE_GAMES[session['user_id']] = { 
                    'mode': mode, 
                    'quest_name': q_name, 
                    'content': card['card_text'],
                    'level': level
                }
                return redirect(url_for('play_game'))
                
        cards = gm.get_available_quests(session['user_id'], 'review')
        return render_template('zone_list.html', title="복습 구역", quests=cards, mode='review')
    except Exception as e:
        return f"<h3>⚠️ 복습 구역 오류 발생</h3><pre>{traceback.format_exc()}</pre><br><a href='/lobby'>로비로 돌아가기</a>"

@app.route('/zone/abbrev', methods=['GET', 'POST'])
def zone_abbrev():
    if 'user_id' not in session: return redirect(url_for('index'))
    try:
        if request.method == 'POST':
            q_name = request.form['quest_name']
            cards = gm.get_available_quests(session['user_id'], 'abbrev')
            card = next((c for c in cards if c.get('quest_name') == q_name), None)
            
            if card:
                mnemonic = gm.get_mnemonic(session['user_id'], q_name)
                ACTIVE_GAMES[session['user_id']] = { 
                    'mode': 'abbrev', 
                    'quest_name': q_name, 
                    'content': card['card_text'],
                    'level': int(card.get('level', 1)),
                    'mnemonic': mnemonic
                }
                return redirect(url_for('play_game'))
                
        cards = gm.get_available_quests(session['user_id'], 'abbrev')
        return render_template('zone_list.html', title="약어 훈련소", quests=cards, mode='abbrev')
    except Exception as e:
        return f"<h3>⚠️ 약어 구역 오류</h3><pre>{traceback.format_exc()}</pre><br><a href='/lobby'>로비로</a>"

@app.route('/play', methods=['GET', 'POST'])
def play_game():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    game = ACTIVE_GAMES.get(session['user_id'])
    if not game: return redirect(url_for('lobby'))

    current_level = game.get('level', 1)

    if request.method == 'GET':
        content = game['content']
        parts = []
        targets = []
        
        if game['mode'] == 'register_mnemonic':
            clean_text = re.sub(r'\{([^}]+)\}', r'\1', content)
            parts = [
                {'type':'text', 'val': '이 카드의 약어(두문자)를 만드세요.<br>예: 예방 진단 치료 재활 -> 예단치재'},
                {'type':'box_content', 'val': clean_text}
            ]
            targets = []

        elif game['mode'] == 'abbrev':
            clean = re.sub(r'\{([^}]+)\}', r'\1', content)
            mnemonic_target = game.get('mnemonic', '약어없음')
            parts = [
                {'type':'text', 'val': '1단계: 이 카드의 약어(두문자)를 입력하세요.'}, 
                {'type':'input_abbrev', 'id':0, 'mnemonic_ans': mnemonic_target}
            ]
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
        
        return render_template('play.html', parts=parts, targets=targets, mode=game['mode'], title=game['quest_name'], level=current_level)

    elif request.method == 'POST':
        try:
            if game['mode'] == 'register_mnemonic':
                user_mnemonic = request.form.get('user_mnemonic', '').strip()
                if user_mnemonic:
                    gm.save_mnemonic(session['user_id'], game['quest_name'], user_mnemonic)
                    lv, xp = gm.process_result(session['user_id'], session.get('user_row_idx'), game['quest_name'], game['content'], 'review')
                    session['level'] = lv; session['xp'] = xp
                    flash(f"약어 '{user_mnemonic}' 저장 완료! (약어 구역에서 테스트하세요)")
                    return redirect(url_for('zone_review'))
                else:
                    flash("약어를 입력해주세요.")
                    return redirect(url_for('play_game'))

            clean = game['content']
            if game['mode'] != 'abbrev': clean = re.sub(r'\{([^}]+)\}', r'\1', game['content'])
            
            lv, xp = gm.process_result(session['user_id'], session.get('user_row_idx'), game['quest_name'], clean, game['mode'])
            
            session['level'] = lv; session['xp'] = xp
            
            if game['mode'] == 'acquire':
                flash("획득완료")
            else:
                flash(f"학습 완료! (현재 Lv.{lv})")
            
            return_zone = 'review' if game['mode'] == 'review' else ('abbrev' if game['mode'] == 'abbrev' else 'acquire')
            return redirect(url_for(f"zone_{return_zone}"))
        except Exception as e:
            return f"<h3>⚠️ 오류 발생</h3><pre>{traceback.format_exc()}</pre><br><a href='/lobby'>로비로 돌아가기</a>"

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
