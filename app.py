from flask import Flask, render_template, request, send_file, jsonify, url_for
import requests
from bs4 import BeautifulSoup
import json
import re
import os
import pandas as pd
import threading
from datetime import datetime, timedelta

app = Flask(__name__)

# Đường dẫn lưu trữ các file đã trích xuất
EXTRACTED_FILES_DIR = os.path.join(os.getcwd(), 'extracted_files')

# Tạo thư mục lưu trữ nếu chưa có
if not os.path.exists(EXTRACTED_FILES_DIR):
    os.makedirs(EXTRACTED_FILES_DIR)

# Giới hạn số lượng file
MAX_FILES = 10

def manage_extracted_files():
    """Quản lý các file đã trích xuất, giới hạn số lượng và xóa file cũ"""
    try:
        # Lấy danh sách file Excel
        files = [f for f in os.listdir(EXTRACTED_FILES_DIR) if f.endswith('.xlsx')]
        
        # Sắp xếp file theo thời gian tạo (mới nhất đầu tiên)
        files_with_time = [
            (f, os.path.getctime(os.path.join(EXTRACTED_FILES_DIR, f))) 
            for f in files
        ]
        files_with_time.sort(key=lambda x: x[1], reverse=True)
        
        # Xóa các file cũ nếu vượt quá giới hạn
        while len(files_with_time) > MAX_FILES:
            old_file = files_with_time.pop()[0]
            os.remove(os.path.join(EXTRACTED_FILES_DIR, old_file))
    except Exception as e:
        print(f"Lỗi quản lý file: {e}")

def get_file_details(files):
    """Lấy thông tin chi tiết của các file"""
    details = {}
    for file in files:
        file_path = os.path.join(EXTRACTED_FILES_DIR, file)
        created_time = os.path.getctime(file_path)
        created_at = datetime.fromtimestamp(created_time).strftime('%d/%m/%Y %H:%M')
        details[file] = {
            'created_at': created_at,
            'size': f"{os.path.getsize(file_path) / 1024:.1f} KB"
        }
    return details

import uuid
from urllib.parse import urlparse

def generate_unique_filename(url, extension='.xlsx'):
    """Tạo tên file duy nhất từ URL"""
    try:
        # Phân tích URL
        parsed_url = urlparse(url)
        
        # Lấy path cuối cùng
        path = parsed_url.path.strip('/').split('/')[-1]
        
        # Loại bỏ số và các ký tự không mong muốn
        clean_path = re.sub(r'^[\d-]+', '', path)
        clean_path = re.sub(r'[^a-zA-Z0-9-]', '', clean_path)
        
        # Giới hạn độ dài
        clean_path = clean_path[:50]
        
        # Kết hợp path và UUID để tạo tên file duy nhất
        filename = f"{clean_path}_{uuid.uuid4().hex[:8]}{extension}"
        
        return filename
    
    except Exception as e:
        print(f"Lỗi tạo tên file: {e}")
        # Fallback về phương thức tạo tên file gốc nếu có lỗi
        return f"{uuid.uuid4()}{extension}"
    
def get_flashcards_from_page(url):
    """Lấy flashcards từ một trang web"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return None
        
        soup = BeautifulSoup(response.content, 'html.parser')
        flashcards = []
        
        # Các chiến lược trích xuất flashcards
        strategies = [
            # Chiến lược 1: Tìm trong script JSON-LD
            lambda: extract_jsonld_flashcards(soup),
            
            # Chiến lược 2: Tìm các phần tử HTML chứa câu hỏi và câu trả lời
            lambda: extract_html_flashcards(soup)
        ]
        
        # Thử các chiến lược trích xuất
        for strategy in strategies:
            flashcards = strategy()
            if flashcards:
                return flashcards
        
        return None
    
    except requests.RequestException as e:
        print(f"Lỗi khi tải trang: {e}")
        return None

def extract_jsonld_flashcards(soup):
    """Trích xuất flashcards từ script JSON-LD"""
    flashcards = []
    scripts = soup.find_all('script', type="application/ld+json")
    
    for script in scripts:
        try:
            data = json.loads(script.string)
            
            # Xử lý các định dạng JSON-LD khác nhau
            if isinstance(data, list):
                data = data[0]
            
            # Tìm câu hỏi và câu trả lời
            questions = data.get('hasPart', []) or data.get('question', [])
            
            for question in questions:
                flashcard = {
                    'question': question.get('text', ''),
                    'answer': question.get('acceptedAnswer', {}).get('text', '')
                }
                
                if flashcard['question'] and flashcard['answer']:
                    flashcards.append(flashcard)
        
        except (json.JSONDecodeError, TypeError):
            continue
    
    return flashcards

def extract_html_flashcards(soup):
    """Trích xuất flashcards từ cấu trúc HTML"""
    flashcards = []
    
    # Tìm các phần tử chứa câu hỏi và câu trả lời
    question_elements = soup.find_all(['div', 'p'], class_=re.compile(r'(question|answer)'))
    
    for i in range(0, len(question_elements), 2):
        if i + 1 < len(question_elements):
            question = question_elements[i].get_text(strip=True)
            answer = question_elements[i+1].get_text(strip=True)
            
            if question and answer:
                flashcards.append({
                    'question': question,
                    'answer': answer
                })
    
    return flashcards

def save_flashcards(flashcards, start_number, filename):
    """Lưu flashcards vào file"""
    with open(filename, 'a', encoding='utf-8') as file:
        for index, flashcard in enumerate(flashcards, start=start_number):
            file.write(f"Câu hỏi {index}: {flashcard['question']}\n")
            file.write(f"Câu trả lời {index}: {flashcard['answer']}\n\n")
    return start_number + len(flashcards)

def get_all_flashcards(url):
    """Trích xuất tất cả flashcards từ URL"""
    page_number = 1
    total_flashcards = 0
    question_number = 1
    
        # Sử dụng generate_unique_filename để tạo tên file txt
    filename = generate_unique_filename(url, extension='.txt')
    filepath = os.path.join(EXTRACTED_FILES_DIR, filename)
    
    with open(filepath, 'w', encoding='utf-8') as file:
        file.write("===== BẮT ĐẦU TRÍCH XUẤT FLASHCARDS =====\n\n")
    
    while True:
        full_url = f"{url}?page={page_number}"
        flashcards = get_flashcards_from_page(full_url)
        
        if not flashcards:
            break
        
        question_number = save_flashcards(flashcards, question_number, filepath)
        total_flashcards += len(flashcards)
        print(f"Đã lấy {len(flashcards)} flashcards từ trang {page_number}.")
        
        page_number += 1
    
    print(f"Đã trích xuất tổng cộng {total_flashcards} flashcards và lưu vào file {filepath}.")
    return filepath

def parse_flashcards(file_path):
    """Chuyển đổi file txt sang DataFrame"""
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()
    
    flashcard_sections = re.findall(r'Câu hỏi (\d+): (.*?)\nCâu trả lời \1: (.*?)(?=Câu hỏi \d+|$)', content, re.DOTALL)
    
    questions = []
    options = []
    answers = []
    
    for section in flashcard_sections:
        question_num, question, answer = section
        
        # Tìm các lựa chọn nếu có
        option_match = re.findall(r'([A-E])\) (.*?)(?=[A-E]\)|\n|$)', question)
        if option_match:
            options_list = option_match
            question = re.sub(r'\n[A-E]\).*', '', question).strip()
            options_str = '\n'.join([f"{opt[0]}) {opt[1]}" for opt in options_list])
        else:
            options_str = ''
        
        questions.append(question)
        options.append(options_str)
        answers.append(answer.strip())
    
    df = pd.DataFrame({
        'Question': questions,
        'Options': options,
        'Answer': answers
    })
    
    return df

def convert_to_excel(input_file, output_file='flashcards.xlsx'):
    """Chuyển đổi file txt sang Excel"""
    df = parse_flashcards(input_file)
    df.to_excel(output_file, index=False, engine='openpyxl')
    print(f"Flashcards converted and saved to {output_file}")
    return output_file

def delete_file_after_delay(filename, delay=86400):  # Mặc định là 24 giờ
    """Xóa file sau một khoảng thời gian nhất định"""
    threading.Timer(delay, lambda: os.remove(filename) if os.path.exists(filename) else None).start()

@app.route("/")
def index():
    """Trang chủ - hiển thị danh sách file đã trích xuất"""
    manage_extracted_files()
    
    extracted_files = [f for f in os.listdir(EXTRACTED_FILES_DIR) if f.endswith('.xlsx')]
    file_details = get_file_details(extracted_files)
    
    return render_template("index.html", 
                           extracted_files=extracted_files, 
                           file_details=file_details)

@app.route("/extract", methods=["POST"])
def extract():
    """Xử lý trích xuất flashcards từ URL"""
    url = request.form['url']
    
    try:
        # Trích xuất flashcards và lưu vào file txt
        txt_filepath = get_all_flashcards(url)
        
        # Chuyển đổi file txt sang Excel
        output_excel_filename = os.path.basename(txt_filepath).replace('.txt', '.xlsx')
        output_excel_filepath = os.path.join(EXTRACTED_FILES_DIR, output_excel_filename)
        convert_to_excel(txt_filepath, output_excel_filepath)
        
        # Gọi hàm để xóa file txt gốc
        if os.path.exists(txt_filepath):
            os.remove(txt_filepath)
        
        # Gọi hàm để xóa file Excel sau 24 giờ
        delete_file_after_delay(output_excel_filepath, 86400)  # Xóa sau 24 giờ
        
        # Trả về JSON response để phù hợp với AJAX request
        return jsonify({
            'success': True, 
            'filename': output_excel_filename,
            'message': 'Trích xuất thành công!'
        }), 200
    
    except Exception as e:
        # Trả về JSON error response
        return jsonify({
            'success': False, 
            'message': str(e)
        }), 400

@app.route("/download/<filename>")
def download(filename):
    """Tải xuống file Excel"""
    try:
        path = os.path.join(EXTRACTED_FILES_DIR, filename)
        if os.path.exists(path):
            return send_file(path, as_attachment=True)
        else:
            return 'File not found', 404
    except Exception as e:
        return f'Error downloading file: {str(e)}', 500

@app.route("/delete/<filename>", methods=['DELETE'])
def delete(filename):
    """Xóa file Excel"""
    try:
        file_path = os.path.join(EXTRACTED_FILES_DIR, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({'success': True, 'message': 'File deleted successfully'}), 200
        else:
            return jsonify({'success': False, 'message': 'File not found'}), 404
    except Exception as e:
        return jsonify({
            'success': False, 
            'message': f'Error deleting file: {str(e)}'
        }), 500

# Thêm một số xử lý lỗi toàn cục
@app.errorhandler(404)
def page_not_found(e):
    return jsonify({'success': False, 'message': 'Trang không tồn tại'}), 404

@app.errorhandler(500)
def internal_server_error(e):
    return jsonify({'success': False, 'message': 'Lỗi máy chủ nội bộ'}), 500

if __name__ == "__main__":
    # Tạo thư mục lưu trữ file nếu chưa tồn tại
    if not os.path.exists(EXTRACTED_FILES_DIR):
        os.makedirs(EXTRACTED_FILES_DIR)
    
    # Cấu hình ứng dụng
    app.config.update(
        SECRET_KEY='your_secret_key_here',  # Khóa bảo mật
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # Giới hạn kích thước upload
        TEMPLATES_AUTO_RELOAD=True  # Tự động reload template khi dev
    )
    
    # Chạy ứng dụng
    app.run(
        host='0.0.0.0',      # Cho phép truy cập từ các thiết bị khác
        port=5000,           # Cổng mặc định
        debug=True,          # Chế độ debug
        threaded=True        # Hỗ trợ đa luồng
    )