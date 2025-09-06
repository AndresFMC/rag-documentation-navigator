import os
import boto3
from dotenv import load_dotenv

from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import BedrockEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader

# --- 1. Carga de Configuración y Clientes ---
load_dotenv()
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

# Inicializa el cliente de Bedrock en tu región elegida (Frankfurt).
bedrock_client = boto3.client(service_name='bedrock-runtime', region_name='eu-central-1')

print("Iniciando Fase 1 para: rag-documentation-navigator")
print(f"Región de AWS: eu-central-1 | Bucket S3: {S3_BUCKET_NAME}")
print("="*50)

def create_and_store_index():
    # ... (el resto del código es idéntico al anterior)
    print("Paso 2: Cargando documents de la carpeta 'data/'...")
    loader = DirectoryLoader('./data/', glob="**/*.pdf", loader_cls=PyPDFLoader, show_progress=True)
    documents = loader.load()
    if not documents:
        print("\nERROR: No se encontraron documentos. Asegúrate de colocar tus PDFs en la carpeta 'data/'.")
        return
    print(f"-> {len(documents)} documentos cargados.")

    print("\nPaso 3: Dividiendo documentos en chunks...")
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    docs = text_splitter.split_documents(documents)
    print(f"-> Documentos divididos en {len(docs)} chunks.")

    print("\nPaso 4: Creando embeddings con Amazon Titan G1 - Text...")
    embeddings_model = BedrockEmbeddings(client=bedrock_client, model_id="amazon.titan-embed-text-v1")

    print("\nPaso 5: Creando índice FAISS en memoria y guardando en 'local_index/'...")
    vectorstore = FAISS.from_documents(docs, embeddings_model)
    vectorstore.save_local("local_index")
    print("-> Índice guardado localmente.")

    print(f"\nPaso 6: Subiendo archivos de índice a S3 Bucket: {S3_BUCKET_NAME}...")
    s3_client = boto3.client('s3')
    try:
        s3_client.upload_file("./local_index/index.faiss", S3_BUCKET_NAME, "index.faiss")
        s3_client.upload_file("./local_index/index.pkl", S3_BUCKET_NAME, "index.pkl")
        print("-> Archivos 'index.faiss' y 'index.pkl' subidos a S3 con éxito.")
    except Exception as e:
        print(f"\nERROR subiendo archivos a S3: {e}")

if __name__ == "__main__":
    create_and_store_index()
    print("\n" + "="*50)
    print("¡Proceso de Fase 1 completado!")