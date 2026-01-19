from flask import Flask, render_template, request, redirect, url_for, session, flash
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from kiwipiepy import Kiwi
import random
import datetime
import re
import os
import json

app = Flask(__name__)
app.secret_key = 'magic_secret_key'  # 보안을 위한 키 (아무거나 복잡하게)

# -------------------------------------------------------------------
# [Backend] 구글 시트 매니저 (기존 로직 유지)
# -------------------------------------------------------------------
class GoogleSheetManager:
    def __init__(self):
        try:
            # Render 등 서버 환경변수에서 JSON 키를 가져오거나, 로컬 파일 사용
            # [주의] 실제 배포 시에는 환경변수 사용 권장. 로컬 테스트용 로직 포함.
            if os.environ.get('GCP_CREDENTIALS'):
                creds_dict = json.loads(os.environ.get('GCP_CREDENTIALS'))
            else:
                # 로컬 테스트용 (secrets.toml 대신 직접 파일 경로 지정 필요할 수 있음)
                # 여기서는 에러 방지를 위해 예외처리
                print("GCP 자격 증명이 없습니다.")
                return

            scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open("memory_game_db")
            
            # 시트 연결 (없으면 생성)
            try: self.users_ws = self.sheet.worksheet("users")
            except: self.users_ws = self.sheet.add_worksheet("users", 100, 10); self.users_ws.append_row(["user_id", "password", "level", "xp", "title"])
            
            try: self.collections_ws = self.sheet.worksheet("collections")
            except: self.collections_ws = self.sheet.add_worksheet("collections", 100, 10); self.collections_ws.append_row(["user_id", "card_text", "grade", "collected_at", "quest_name", "count"])
            
            try: self.quests_ws = self.sheet.worksheet("quests")
            except: self.quests_ws = self.sheet.add_worksheet("quests", 100, 5); self.quests_ws.append_row(["quest_name", "content", "created_by", "created_at"])

        except Exception as e:
            print(f"구글 시트 연결 오류: {e}")

    def login(self, user_id, password):
        records = self.users_ws.get_all_records()
        for i, row in enumerate(records):
            if str(row['user_id']) == str(user_id) and str(row['password']) == str(password):
                return row, i + 2
        return None, None

    def register(self, user_id, password):
        records = self.users_ws.get_all_records()
        for row in records:
            if str(row['user_id']) == str(user_id): return False
        self.users_ws.append_row([user_id, password, 1, 0, "견습 마법사"])
        return True
        
    def get_quest_list(self):
        return self.quests_ws.get_all_records()
        
    def save_quest(self, name, content, creator):
        records = self.quests_ws.get_all_records()
        for row in records:
            if row['quest_name'] == name: return False
        self.quests_ws.append_row([name, content[:45000], creator, str(datetime.date.today())])
        return True

    def process_reward(self, user_id, card_text, current_level, current_xp, row_idx, quest_name):
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
        return [c for c in self.collections_ws.get_all_records() if str(c['user_id']) == str(user_id)]

# 전역 객체
gm = GoogleSheetManager()
kiwi = Kiwi()

# -------------------------------------------------------------------
# [Routes] 페이지 연결
# -------------------------------------------------------------------
@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('lobby'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    uid = request.form['id']
    upw = request.form['pw']
    user_data, row_idx = gm.login(uid, upw)
    if user_data:
        session['user_id'] = uid
        session['user_row_idx'] = row_idx
        session['level'] = user_data['level']
        session['xp'] = user_data['xp']
        return redirect(url_for('lobby'))
    flash("주문이 틀렸습니다.")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/lobby')
def lobby():
    if 'user_id' not in session: return redirect(url_for('index'))
    return render_template('lobby.html', 
                           user_id=session['user_id'], 
                           level=session['level'], 
                           xp=session['xp'], 
                           req_xp=session['level']*100)

@app.route('/dungeon', methods=['GET', 'POST'])
def dungeon():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    if request.method == 'POST':
        # 1. 퀘스트 로드 요청
        if 'load_quest' in request.form:
            q_name = request.form['quest_select']
            quests = gm.get_quest_list()
            content = next((q['content'] for q in quests if q['quest_name'] == q_name), "")
            
            # Kiwi 분석 및 빈칸 생성 로직 (여기가 핵심!)
            sents = [s.text for s in kiwi.split_into_sents(content) if len(s.text)>5]
            if not sents: return redirect(url_for('dungeon'))
            
            session['quest_sents'] = sents
            session['q_idx'] = 0
            session['quest_name'] = q_name
            return redirect(url_for('dungeon_play'))
            
        # 2. 새 퀘스트 저장 요청
        elif 'save_quest' in request.form:
            name = request.form['new_q_name']
            file = request.files['new_q_file']
            content = file.read().decode('utf-8')
            if gm.save_quest(name, content, session['user_id']):
                flash("기록 완료!")
            else:
                flash("이미 있는 이름입니다.")
            return redirect(url_for('dungeon'))

    quests = gm.get_quest_list()
    return render_template('dungeon.html', quests=quests)

@app.route('/dungeon/play', methods=['GET', 'POST'])
def dungeon_play():
    if 'quest_sents' not in session: return redirect(url_for('dungeon'))
    
    # 문제 생성 로직
    curr_sent = session['quest_sents'][session['q_idx'] % len(session['quest_sents'])]
    
    if request.method == 'GET':
        tokens = kiwi.tokenize(curr_sent)
        STOPWORDS = {'다음','사항','경우','포함','관련','해당','각','호','목','조','항','위','아래','전','후','및','등','이','그','저','것','수','때','중','가지','누구','무엇','따름','의','를','가'}
        nouns = [t.form for t in tokens if t.tag in ['NNG', 'NNP'] and len(t.form)>1 and t.form not in STOPWORDS]
        
        target_nouns = list(set(nouns)) # 난이도 조절 없이 전부 (원하면 로직 추가 가능)
        if not target_nouns:
             session['q_idx'] += 1
             return redirect(url_for('dungeon_play'))

        # [핵심] 문장 쪼개기 (Flask라서 가능한 "진짜 인라인" 구현)
        # 문장을 (텍스트, 입력창여부, 정답) 리스트로 변환
        matches = []
        for t in target_nouns:
            for m in re.finditer(re.escape(t), curr_sent):
                matches.append((m.start(), m.end(), m.group()))
        matches.sort(key=lambda x: x[0])
        
        parts = []
        last_idx = 0
        targets = [] # 정답 검증용
        
        for i, (start, end, word) in enumerate(matches):
            if start > last_idx:
                parts.append({'type': 'text', 'val': curr_sent[last_idx:start]})
            parts.append({'type': 'input', 'id': i})
            targets.append(word)
            last_idx = end
        if last_idx < len(curr_sent):
            parts.append({'type': 'text', 'val': curr_sent[last_idx:]})
            
        session['curr_targets'] = targets
        session['curr_sent_text'] = curr_sent
        
        return render_template('dungeon_play.html', parts=parts)

    # 정답 확인 (POST)
    elif request.method == 'POST':
        user_inputs = request.form.getlist('answers')
        targets = session.get('curr_targets', [])
        
        all_correct = True
        for u, t in zip(user_inputs, targets):
            if u.strip() != t: all_correct = False; break
            
        if all_correct:
            g, gain, nl, nx, stat, cnt = gm.process_reward(
                session['user_id'], session['curr_sent_text'], 
                session['level'], session['xp'], session['user_row_idx'],
                session['quest_name']
            )
            session['level'] = nl
            session['xp'] = nx
            flash(f"성공! +{gain} EXP ({g})")
            session['q_idx'] += 1
        else:
            flash("주문 실패! 다시 시도하세요.")
            
        return redirect(url_for('dungeon_play'))

@app.route('/collection')
def collection():
    cards = gm.get_collections(session['user_id'])
    return render_template('collection.html', cards=cards)

if __name__ == '__main__':
    app.run(debug=True)
