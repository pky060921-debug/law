import os
import json
import random
import datetime
import re
from flask import Flask, render_template, request, redirect, url_for, session, flash
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_magic_key')

# [핵심 수정 1] 텍스트가 너무 길어서 쿠키에 못 담으므로, 서버 메모리에 임시 저장합니다.
# 구조: { 'user_id': { 'sents': [...], 'q_idx': 0, 'quest_name': '...' } }
ACTIVE_GAMES = {}

# ---------------------------------------------------------
# 1. 구글 시트 매니저
# ---------------------------------------------------------
class GoogleSheetManager:
    def __init__(self):
        self.users_ws = None
        self.collections_ws = None
        self.quests_ws = None
        
        try:
            json_creds = os.environ.get('GCP_CREDENTIALS')
            if not json_creds:
                print("⚠️ [경고] GCP 자격 증명(환경변수)이 없습니다.")
                return
            
            creds_dict = json.loads(json_creds)
            scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open("memory_game_db")
            
            try: self.users_ws = self.sheet.worksheet("users")
            except: self.users_ws = self.sheet.add_worksheet("users", 100, 10); self.users_ws.append_row(["user_id", "password", "level", "xp", "title"])
            
            try: self.collections_ws = self.sheet.worksheet("collections")
            except: self.collections_ws = self.sheet.add_worksheet("collections", 100, 10); self.collections_ws.append_row(["user_id", "card_text", "grade", "collected_at", "quest_name", "count"])
            
            try: self.quests_ws = self.sheet.worksheet("quests")
            except: self.quests_ws = self.sheet.add_worksheet("quests", 100, 5); self.quests_ws.append_row(["quest_name", "content", "created_by", "created_at"])
            
            print("✅ 구글 시트 연결 성공!")
            
        except Exception as e:
            print(f"🔥🔥 [치명적 에러] 구글 시트 연결 실패: {e}")

    def get_quest_list(self):
        if self.quests_ws is None: return []
        try: return self.quests_ws.get_all_records()
        except: return []

    def login(self, user_id, password):
        if self.users_ws is None: return None, None
        try:
            records = self.users_ws.get_all_records()
            for i, row in enumerate(records):
                if str(row['user_id']) == str(user_id) and str(row['password']) == str(password):
                    return row, i + 2
        except: pass
        return None, None

    def register(self, user_id, password):
        if self.users_ws is None: return False
        try:
            records = self.users_ws.get_all_records()
            for row in records:
                if str(row['user_id']) == str(user_id): return False
            self.users_ws.append_row([user_id, password, 1, 0, "견습 마법사"])
            return True
        except: return False

    def save_quest(self, name, content, creator):
        if self.quests_ws is None: return False
        try:
            records = self.quests_ws.get_all_records()
            for row in records:
                if str(row.get('quest_name')) == str(name): return False
            self.quests_ws.append_row([name, content[:45000], creator, str(datetime.date.today())])
            return True
        except: return False

    def process_reward(self, user_id, card_text, current_level, current_xp, row_idx, quest_name):
        if self.collections_ws is None: return "ERROR", 0, current_level, current_xp, "ERROR", 0
        
        records = self.collections_ws.get_all_records()
        found_idx = -1; current_count = 0; current_grade = "NORMAL"
        for i, row in enumerate(records):
            if str(row['user_id']) == str(user_id) and row['card_text'] == card_text and row.get('quest_name') == quest_name:
                found_idx = i + 2; current_count = row.get('count', 1); current_grade = row.get('grade', 'NORMAL'); break
        
        status = ""; final_grade = current_grade
        if found_idx != -1:
            new_count = current_count + 1
            if new_count >= 7: new_grade = "LEGEND"
            elif new_count >= 3: new_grade = "RARE"
            else: new_grade = current_grade
            self.collections_ws.update_cell(found_idx, 6, new_count)
            self.collections_ws.update_cell(found_idx, 3, new_grade)
            self.collections_ws.update_cell(found_idx, 4, str(datetime.date.today()))
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
        if self.collections_ws is None: return []
        try: return [c for c in self.collections_ws.get_all_records() if str(c['user_id']) == str(user_id)]
        except: return []

gm = GoogleSheetManager()

# ---------------------------------------------------------
# 2. 헬퍼 함수
# ---------------------------------------------------------
def split_text_basic(text):
    if not text: return []
    text = text.replace('\r\n', '\n').replace('\n', '.')
    sents = re.split(r'[.?!]', text)
    return [s.strip() for s in sents if len(s.strip()) > 2]

def extract_blank_words(text):
    words = text.split()
    candidates = [w.strip(".,?!'\"") for w in words if len(w) >= 2]
    return list(set(candidates))

# ---------------------------------------------------------
# 3. 라우트
# ---------------------------------------------------------
@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('lobby'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    uid = request.form.get('id')
    upw = request.form.get('pw')
    user_data, row_idx = gm.login(uid, upw)
    if user_data:
        session['user_id'] = uid
        session['user_row_idx'] = row_idx
        session['level'] = user_data['level']
        session['xp'] = user_data['xp']
        return redirect(url_for('lobby'))
    else:
        flash("로그인 실패")
        return redirect(url_for('index'))

@app.route('/register', methods=['POST'])
def register():
    uid = request.form.get('new_id')
    upw = request.form.get('new_pw')
    if gm.register(uid, upw): flash("등록 완료")
    else: flash("이미 존재하는 ID")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/lobby')
def lobby():
    if 'user_id' not in session: return redirect(url_for('index'))
    return render_template('lobby.html', user_id=session['user_id'], level=session['level'], xp=session['xp'], req_xp=session['level']*100)

@app.route('/dungeon', methods=['GET', 'POST'])
def dungeon():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    if request.method == 'POST':
        # --- 퀘스트 선택 ---
        if 'quest_select' in request.form:
            q_name = request.form['quest_select']
            print(f"\n=== [DEBUG] 선택: {q_name} ===")

            if q_name == "선택 안함": return redirect(url_for('dungeon'))
            
            quests = gm.get_quest_list()
            selected_quest = None
            for q in quests:
                if str(q.get('quest_name', '')).strip() == str(q_name).strip():
                    selected_quest = q
                    break
            
            if not selected_quest:
                flash("데이터를 찾을 수 없습니다.")
                return redirect(url_for('dungeon'))

            content = selected_quest.get('content', "")
            sents = split_text_basic(content)
            
            if not sents: 
                flash("내용이 너무 짧습니다.")
                return redirect(url_for('dungeon'))
            
            # [핵심 수정 2] 쿠키(Session) 대신 서버 메모리(ACTIVE_GAMES)에 저장
            user_id = session['user_id']
            ACTIVE_GAMES[user_id] = {
                'sents': sents,
                'q_idx': 0,
                'quest_name': q_name,
                'curr_targets': [],
                'curr_sent_text': ""
            }
            
            print("✅ 서버 메모리에 게임 데이터 저장 완료")
            return redirect(url_for('dungeon_play'))
            
        # --- 퀘스트 생성 ---
        elif 'new_q_name' in request.form:
            name = request.form['new_q_name']
            f = request.files['new_q_file']
            if name and f:
                content = f.read().decode('utf-8')
                if gm.save_quest(name, content, session['user_id']): flash("저장 완료")
                else: flash("저장 실패")
            return redirect(url_for('dungeon'))

    quests = gm.get_quest_list()
    return render_template('dungeon.html', quests=quests)

@app.route('/dungeon/play', methods=['GET', 'POST'])
def dungeon_play():
    if 'user_id' not in session: return redirect(url_for('index'))
    user_id = session['user_id']
    
    # [핵심 수정 3] 서버 메모리에서 데이터 가져오기
    game_data = ACTIVE_GAMES.get(user_id)
    if not game_data:
        print("❌ 진행 중인 게임 데이터가 없습니다.")
        flash("게임 정보가 만료되었습니다. 다시 선택해주세요.")
        return redirect(url_for('dungeon'))
    
    sents = game_data['sents']
    
    if request.method == 'GET':
        curr_sent = sents[game_data['q_idx'] % len(sents)]
        candidates = extract_blank_words(curr_sent)
        
        if not candidates:
            game_data['q_idx'] += 1
            return redirect(url_for('dungeon_play'))
            
        k = max(1, int(len(candidates) * 0.2)) 
        target_words = random.sample(candidates, k)
        
        matches = []
        for t in target_words:
            for m in re.finditer(re.escape(t), curr_sent):
                matches.append((m.start(), m.end(), m.group()))
        matches.sort(key=lambda x: x[0])
        
        parts = []
        last_idx = 0
        targets = []
        for i, (start, end, word) in enumerate(matches):
            if start > last_idx: parts.append({'type': 'text', 'val': curr_sent[last_idx:start]})
            parts.append({'type': 'input', 'id': i})
            targets.append(word)
            last_idx = end
        if last_idx < len(curr_sent): parts.append({'type': 'text', 'val': curr_sent[last_idx:]})
            
        # 서버 메모리에 정답 업데이트
        game_data['curr_targets'] = targets
        game_data['curr_sent_text'] = curr_sent
        
        return render_template('dungeon_play.html', parts=parts)

    elif request.method == 'POST':
        user_inputs = request.form.getlist('answers')
        targets = game_data.get('curr_targets', [])
        
        all_correct = True
        for u, t in zip(user_inputs, targets):
            if u.strip() != t: all_correct = False; break
            
        if all_correct:
            g, gain, nl, nx, stat, cnt = gm.process_reward(
                session['user_id'], 
                game_data['curr_sent_text'], 
                session['level'], 
                session['xp'], 
                session['user_row_idx'], 
                game_data['quest_name']
            )
            session['level'] = nl; session['xp'] = nx
            flash(f"정답! +{gain} XP")
            game_data['q_idx'] += 1
        else:
            flash("오답입니다.")
        return redirect(url_for('dungeon_play'))

@app.route('/collection')
def collection():
    if 'user_id' not in session: return redirect(url_for('index'))
    cards = gm.get_collections(session['user_id'])
    return render_template('collection.html', cards=cards)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
