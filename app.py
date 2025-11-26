import os
import base64
import json
import datetime
import io
from flask import Flask, render_template, request, jsonify
from openai import OpenAI
import firebase_admin
from firebase_admin import credentials, firestore
from PIL import Image

# --- OpenAI APIキーの設定 ---
try:
    client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    print("OpenAI APIキーの読み込み成功")
except KeyError:
    print("エラー: OPENAI_API_KEY が設定されていません")
    client = None

# --- Firebase の設定 ---
try:
    service_account_json_string = os.environ['FIREBASE_SERVICE_ACCOUNT']
    service_account_dict = json.loads(service_account_json_string)
    cred = credentials.Certificate(service_account_dict)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebaseの初期化に成功")
except Exception as e:
    print(f"Firebase初期化エラー: {e}")
    db = None

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

# --- 1. 食事画像の分析と記録 ---
@app.route('/analyze', methods=['POST'])
def analyze_image():
    if not client or not db: return jsonify({'error': 'サーバー設定エラー'}), 500

    if 'image' not in request.files: return jsonify({'error': 'ファイルなし'}), 400
    file = request.files['image']
    if file.filename == '': return jsonify({'error': 'ファイル未選択'}), 400

    user_name = request.form.get('user_name', '名無しさん')
    # ★追加: メモを受け取る
    memo = request.form.get('memo', '')

    try:
        # 画像縮小処理
        img = Image.open(file)
        img.thumbnail((800, 800))
        buffer = io.BytesIO()
        fmt = img.format if img.format == 'PNG' else 'JPEG'
        img.save(buffer, format=fmt)
        image_bytes = buffer.getvalue()
        base64_image = base64.b64encode(image_bytes).decode('utf-8')

    except Exception as e:
        return jsonify({'error': f'画像処理エラー: {str(e)}'}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": """
                            この食事の画像のカロリー、PFCバランスを推定してください。
                            回答は必ず以下のJSON形式でお願いします。
                            { "calories": "約 XXX kcal", "pfc": "P: XXg, F: XXg, C: XXg" }
                        """},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ],
            max_tokens=300
        )
        ai_response = response.choices[0].message.content
        if "```json" in ai_response:
            ai_response = ai_response.split("```json\n")[1].split("\n```")[0]
        ai_data = json.loads(ai_response)
        
        db.collection('activities').add({
            'type': 'food',
            'user_name': user_name,
            'memo': memo, # ★追加: メモを保存
            'calories': ai_data.get('calories', '不明'),
            'pfc': ai_data.get('pfc', '不明'),
            'timestamp': datetime.datetime.now(datetime.timezone.utc)
        })
        
        return jsonify(ai_data)

    except Exception as e:
        print(f"エラー: {e}")
        return jsonify({'error': str(e)}), 500

# --- 2. トレーニング記録 ---
@app.route('/log_training', methods=['POST'])
def log_training():
    if not db: return jsonify({'error': 'DBエラー'}), 500
    try:
        data = request.json
        duration = data.get('duration')
        user_name = data.get('user_name', '名無しさん')
        # ★追加: メモを受け取る
        memo = data.get('memo', '')

        if not duration: return jsonify({'error': '時間なし'}), 400

        db.collection('activities').add({
            'type': 'training',
            'user_name': user_name,
            'memo': memo, # ★追加: メモを保存
            'duration': duration,
            'timestamp': datetime.datetime.now(datetime.timezone.utc)
        })
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- 3. 活動履歴の取得 ---
@app.route('/get_activities', methods=['GET'])
def get_activities():
    if not db: return jsonify({'error': 'DBエラー'}), 500
    try:
        activities_ref = db.collection('activities').order_by(
            'timestamp', direction='DESCENDING'
        ).limit(20)

        activities = []
        for doc in activities_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
            if 'timestamp' in data and data['timestamp']:
                jst_timestamp = data['timestamp'].astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                data['timestamp_str'] = jst_timestamp.strftime('%Y年%m月%d日 %H:%M')
            
            # メモがない古いデータのために空文字を入れておく
            if 'memo' not in data:
                data['memo'] = ''

            activities.append(data)
        return jsonify(activities)
    except Exception as e:
        print(f"取得エラー: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)