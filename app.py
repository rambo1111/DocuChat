from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
import os
import fitz
from pymongo import MongoClient
from bson import Binary, ObjectId
import google.generativeai as genai
from sentence_transformers import SentenceTransformer
from io import BytesIO
from PIL import Image
import base64
from pinecone import Pinecone, ServerlessSpec
from langchain.text_splitter import RecursiveCharacterTextSplitter
from flask_socketio import SocketIO, emit
from datetime import datetime, timedelta

app = Flask(__name__)
socketio = SocketIO(app)

# MongoDB setup
MONGO_URI = os.environ.get("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client['pdf_chat_db']
pdf_collection = db['pdf_files']
image_collection = db['pdf_images']
chunk_collection = db['text_chunks']

# Gemini setup
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Pinecone setup
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index("test")
INDEX_NAME = "test"
DIMENSION = 384

# Initialize embedding model
embedding_model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')

# Create TTL index for PDF files
pdf_collection.create_index("upload_date", expireAfterSeconds=7 * 24 * 60 * 60)  # 7 days

def cleanup_all_data():
    """Clean up all data in MongoDB and Pinecone"""
    try:
        # Clean MongoDB collections
        pdf_collection.delete_many({})
        image_collection.delete_many({})
        chunk_collection.delete_many({})
        print("MongoDB collections cleaned")

        # Initialize Pinecone and clean index
        pc = Pinecone(api_key=PINECONE_API_KEY)
        
        try:
            if pc.Index.exists(INDEX_NAME): 
                index = pc.Index(INDEX_NAME)
                # Delete all vectors
                index.delete(delete_all=True)
                print("Pinecone index cleaned")
        except Exception as e:
            # If index doesn't exist, create new one
            pc.create_index(
                name=INDEX_NAME,
                dimension=DIMENSION,
                metric='cosine',
                spec=ServerlessSpec(
                    cloud='aws',
                    region='us-east-1'
                )
            )
            print(f"Created new Pinecone index: {INDEX_NAME}")
        
        return True
    except Exception as e:
        print(f"Error during cleanup: {e}")
        return False
    
@app.context_processor
def utility_processor():
    cleanup_all_data()
    return dict()

def store_pdf_in_mongodb(file):
    """Store PDF file in MongoDB"""
    try:
        pdf_binary = file.read()
        pdf_document = {
            'filename': secure_filename(file.filename),
            'data': Binary(pdf_binary),
            'upload_date': datetime.utcnow(),
            'processed': False,
            'file_size': len(pdf_binary)
        }
        result = pdf_collection.insert_one(pdf_document)
        return str(result.inserted_id)
    except Exception as e:
        print(f"Error storing PDF: {e}")
        raise

def process_pdf_pages(pdf_id):
    """Extract and process pages from PDF"""
    pdf_doc = pdf_collection.find_one({'_id': ObjectId(pdf_id)})
    if not pdf_doc:
        return False

    try:
        pdf_data = BytesIO(pdf_doc['data'])
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap()
            img_data = pix.tobytes()
            
            image_document = {
                'pdf_id': pdf_id,
                'filename': pdf_doc['filename'],
                'page_number': page_num + 1,
                'image': Binary(img_data),
                'processed': False
            }
            image_collection.insert_one(image_document)
        
        doc.close()
        
        # Mark PDF as processed
        pdf_collection.update_one(
            {'_id': ObjectId(pdf_id)},
            {'$set': {'processed': True}}
        )
        return True
    except Exception as e:
        print(f"Error processing PDF: {e}")
        return False

def prepare_image_for_gemini(image_data):
    """Prepare image for Gemini AI processing"""
    try:
        image = Image.open(BytesIO(image_data))
        if image.mode == 'RGBA':
            image = image.convert('RGB')
        img_byte_arr = BytesIO()
        image.save(img_byte_arr, format='JPEG')
        return img_byte_arr.getvalue(), image
    except Exception as e:
        print(f"Error preparing image: {e}")
        return None, None

def process_images_with_ai():
    """Process images with Gemini AI and store embeddings"""
    unprocessed_images = image_collection.find({'processed': False})
    
    for img_doc in unprocessed_images:
        try:
            processed_image_data, _ = prepare_image_for_gemini(img_doc['image'])
            if processed_image_data:
                base64_image = base64.b64encode(processed_image_data).decode('utf-8')
                
                # Extract text using Gemini
                response = model.generate_content([
                    "Extract all text content from this image. Include any text from diagrams or tables. Provide only the extracted text without any additional explanation.",
                    {"mime_type": "image/jpeg", "data": base64_image}
                ])
                extracted_text = response.text

                # Split text into chunks
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=1000,
                    chunk_overlap=200
                )
                chunks = text_splitter.split_text(extracted_text)

                # Process each chunk
                for idx, chunk in enumerate(chunks):
                    embedding = embedding_model.encode(chunk).tolist()
                    
                    metadata = {
                        "pdf_id": str(img_doc['pdf_id']),
                        "filename": img_doc['filename'],
                        "page_number": img_doc['page_number'],
                        "chunk_index": idx,
                        "text": chunk
                    }
                    vector_id = f"{img_doc['_id']}_{idx}"
                    index.upsert(vectors=[(vector_id, embedding, metadata)])

                    chunk_document = {
                        'pdf_id': img_doc['pdf_id'],
                        'page_number': img_doc['page_number'],
                        'chunk_index': idx,
                        'text': chunk,
                        'vector_id': vector_id
                    }
                    chunk_collection.insert_one(chunk_document)

                # Mark image as processed
                image_collection.update_one(
                    {'_id': img_doc['_id']},
                    {'$set': {'processed': True}}
                )

        except Exception as e:
            print(f"Error processing image {img_doc['_id']}: {e}")
            continue

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'files[]' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files[]')
    processed_files = []
    
    try:
        for file in files:
            if file.filename and file.filename.endswith('.pdf'):
                # Store and process PDF
                pdf_id = store_pdf_in_mongodb(file)
                if process_pdf_pages(pdf_id):
                    processed_files.append(pdf_id)
        
        # Process images with AI
        process_images_with_ai()
        
        return jsonify({
            'success': True,
            'message': f'Successfully processed {len(processed_files)} files'
        })
    
    except Exception as e:
        # Cleanup on error
        for pdf_id in processed_files:
            pdf_collection.delete_one({'_id': ObjectId(pdf_id)})
            image_collection.delete_many({'pdf_id': pdf_id})
            chunk_collection.delete_many({'pdf_id': pdf_id})
        return jsonify({'error': str(e)}), 500

@socketio.on('chat_message')
def handle_message(message):
    try:
        query_embedding = embedding_model.encode(message['query']).tolist()
        results = index.query(vector=query_embedding, top_k=5, include_metadata=True)
        
        context = "\n\n".join([match.metadata.get('text', '') for match in results.matches])
        
        prompt = f"""Based on the following context, answer the user's question: '{message['query']}'

Context:
{context}

Please provide a clear and concise response using markdown formatting when appropriate. Focus on directly answering the question using only information from the provided context."""
        
        response = model.generate_content(prompt, generation_config=genai.GenerationConfig(
            temperature=0.1,
            max_output_tokens=800
        ))
        
        emit('chat_response', {'response': response.text})
    
    except Exception as e:
        emit('chat_response', {'error': str(e)})

def cleanup_old_data():
    """Cleanup function for old data"""
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=7)
        expired_pdfs = pdf_collection.find({
            'upload_date': {'$lt': cutoff_date}
        })
        
        for pdf in expired_pdfs:
            pdf_id = str(pdf['_id'])
            
            # Clean up related data
            image_collection.delete_many({'pdf_id': pdf_id})
            chunk_collection.delete_many({'pdf_id': pdf_id})
            
            # Remove vectors from Pinecone
            chunks = chunk_collection.find({'pdf_id': pdf_id})
            vector_ids = [chunk['vector_id'] for chunk in chunks]
            if vector_ids:
                index.delete(ids=vector_ids)

    except Exception as e:
        print(f"Error during cleanup: {e}")

if __name__ == '__main__':
    socketio.run(app, debug=True)
