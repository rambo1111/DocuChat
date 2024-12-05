import os
import google.generativeai as genai
from PIL import Image
import fitz
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
import shutil

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Gemini API Configuration
genai.configure(api_key="AIzaSyBwPwFyxDFtUvSgGjUK4tV3bXZ9-3yc-9c")  # Replace with your actual API key
model = genai.GenerativeModel("gemini-1.5-flash")

# Global chat session
chat = model.start_chat(history=[])
current_images = []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/cleanup', methods=['POST'])
def cleanup():
    try:
        # Clear the current_images list
        global current_images
        current_images = []

        # Delete the upload folder contents
        upload_folder = app.config['UPLOAD_FOLDER']
        if os.path.exists(upload_folder):
            for root, dirs, files in os.walk(upload_folder, topdown=False):
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        os.remove(file_path)
                    except PermissionError:
                        print(f"Could not delete {file_path}. File in use.")
                for dir in dirs:
                    os.rmdir(os.path.join(root, dir))
        return jsonify({"message": "Cleanup successful"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/upload', methods=['POST'])
def upload_pdf():
    global current_images
    
    # Reset previous images
    current_images = []
    
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Convert PDF to images
        output_folder = os.path.join(app.config['UPLOAD_FOLDER'], 'images')
        os.makedirs(output_folder, exist_ok=True)
        
        doc = fitz.open(filepath)
        images = []
        
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=180)
            image_path = os.path.join(output_folder, f"page_{i + 1}.png")
            pix.save(image_path)
            
            # Open the image for Gemini analysis
            with Image.open(image_path) as img:
                images.append(img.copy())
        
        doc.close()
        
        # Send images to Gemini for initial analysis
        try:
            response = chat.send_message(["Please summarize the content in the/these file/files.", *images])
            summary = response.text
        except Exception as e:
            summary = f"Error analyzing PDF: {str(e)}"
        
        current_images = images
        
        return jsonify({
            "message": "PDF uploaded successfully", 
            "summary": summary
        })

@app.route('/chat', methods=['POST'])
def chat_endpoint():
    global current_images
    user_message = request.json['message']
    
    # try:
    #     # If images are available, include them in the context
    #     if current_images:
    #         response = chat.send_message(["Context: Previous images are part of our conversation", user_message, *current_images])
    #     else:
    #         response = chat.send_message(user_message)
        
    #     return jsonify({"response": response.text})
    try:
        response = chat.send_message(user_message)
    
        return jsonify({"response": response.text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
