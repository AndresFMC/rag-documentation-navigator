import os
import json
import gzip
import boto3
from dotenv import load_dotenv
from tqdm import tqdm

from langchain_aws import BedrockEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader

# --- Configuración ---
load_dotenv()
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
bedrock_client = boto3.client(service_name='bedrock-runtime', region_name='eu-central-1')

print("🚀 RAG Documentation Navigator - Optimized Index Builder")
print(f"📍 Región: eu-central-1 | 🪣 Bucket: {S3_BUCKET_NAME}")
print("="*60)

def create_optimized_index():
    # Paso 1: Cargar documentos
    print("\n📚 [1/6] Cargando documentos PDF...")
    loader = DirectoryLoader('./data/', glob="**/*.pdf", loader_cls=PyPDFLoader, show_progress=True)
    documents = loader.load()
    
    if not documents:
        print("❌ ERROR: No se encontraron documentos en la carpeta 'data/'")
        print("   Asegúrate de tener los PDFs en ./data/")
        return
    
    print(f"✅ {len(documents)} documentos cargados exitosamente")
    
    # Mostrar qué documentos se cargaron
    unique_sources = set()
    for doc in documents:
        if 'source' in doc.metadata:
            unique_sources.add(doc.metadata['source'])
    
    print("\n📄 Documentos procesados:")
    for source in unique_sources:
        filename = os.path.basename(source)
        print(f"   • {filename}")
    
    # Paso 2: Dividir en chunks
    print("\n✂️ [2/6] Dividiendo documentos en chunks...")
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        separators=["\n\n", "\n", ".", " ", ""],
        length_function=len
    )
    docs = text_splitter.split_documents(documents)
    print(f"✅ Documentos divididos en {len(docs)} chunks")
    
    # Paso 3: Inicializar modelo de embeddings
    print("\n🤖 [3/6] Inicializando Amazon Titan Embeddings...")
    embeddings_model = BedrockEmbeddings(
        client=bedrock_client,
        model_id="amazon.titan-embed-text-v1"
    )
    print("✅ Modelo de embeddings listo")
    
    # Paso 4: Generar embeddings
    print("\n🧮 [4/6] Generando embeddings vectoriales...")
    print("   (Esto puede tomar varios minutos)")
    
    index_data = {
        "chunks": [],
        "metadata": {
            "total_chunks": len(docs),
            "embedding_model": "amazon.titan-embed-text-v1",
            "embedding_dimension": 1536,
            "chunk_size": 1000,
            "chunk_overlap": 100,
            "created_date": str(os.popen('date').read().strip())
        }
    }
    
    # Procesar en batches para mostrar progreso
    batch_size = 10
    for i in tqdm(range(0, len(docs), batch_size), desc="Procesando"):
        batch = docs[i:i+batch_size]
        
        for j, doc in enumerate(batch):
            doc_index = i + j
            
            # Generar embedding
            try:
                embedding = embeddings_model.embed_query(doc.page_content)
                
                # Limpiar metadata para hacerla más compacta
                clean_metadata = {
                    "source": os.path.basename(doc.metadata.get('source', 'unknown')),
                    "page": doc.metadata.get('page', 0)
                }
                
                # Agregar al índice
                index_data["chunks"].append({
                    "id": doc_index,
                    "text": doc.page_content[:1000],  # Limitar texto para reducir tamaño
                    "embedding": embedding,
                    "metadata": clean_metadata
                })
                
            except Exception as e:
                print(f"\n⚠️ Error en chunk {doc_index}: {str(e)}")
                continue
    
    successful_chunks = len(index_data["chunks"])
    print(f"\n✅ Embeddings generados: {successful_chunks}/{len(docs)} chunks")
    
    # Paso 5: Comprimir y guardar localmente
    print("\n💾 [5/6] Comprimiendo y guardando índice...")
    
    # Crear directorio si no existe
    os.makedirs("local_index", exist_ok=True)
    
    # Convertir a JSON y comprimir
    json_data = json.dumps(index_data)
    json_size_mb = len(json_data.encode('utf-8')) / (1024 * 1024)
    print(f"   Tamaño JSON sin comprimir: {json_size_mb:.2f} MB")
    
    compressed_data = gzip.compress(json_data.encode('utf-8'), compresslevel=9)
    compressed_size_mb = len(compressed_data) / (1024 * 1024)
    print(f"   Tamaño comprimido: {compressed_size_mb:.2f} MB")
    print(f"   Ratio de compresión: {(1 - compressed_size_mb/json_size_mb)*100:.1f}%")
    
    # Guardar localmente
    local_path = "local_index/index.json.gz"
    with open(local_path, "wb") as f:
        f.write(compressed_data)
    print(f"✅ Índice guardado localmente en: {local_path}")
    
    # Paso 6: Subir a S3
    print(f"\n☁️ [6/6] Subiendo índice a S3...")
    s3_client = boto3.client('s3', region_name='eu-central-1')
    
    try:
        # Subir con metadata útil
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key="index.json.gz",
            Body=compressed_data,
            ContentType="application/gzip",
            Metadata={
                'chunks': str(successful_chunks),
                'original-size-mb': f"{json_size_mb:.2f}",
                'compressed-size-mb': f"{compressed_size_mb:.2f}"
            }
        )
        print(f"✅ Índice subido exitosamente a: s3://{S3_BUCKET_NAME}/index.json.gz")
        
        # Verificar que se subió correctamente
        response = s3_client.head_object(Bucket=S3_BUCKET_NAME, Key="index.json.gz")
        s3_size_mb = response['ContentLength'] / (1024 * 1024)
        print(f"✅ Verificación: Archivo en S3 tiene {s3_size_mb:.2f} MB")
        
    except Exception as e:
        print(f"❌ Error subiendo a S3: {e}")
        print("   Verifica que el bucket existe y tienes permisos")
        return
    
    # Resumen final
    print("\n" + "="*60)
    print("🎉 ¡ÍNDICE OPTIMIZADO CREADO CON ÉXITO!")
    print("="*60)
    print(f"""
📊 Resumen:
   • Documentos procesados: {len(unique_sources)}
   • Total de chunks: {successful_chunks}
   • Tamaño final: {compressed_size_mb:.2f} MB (vs ~99 MB con FAISS)
   • Reducción: {(1 - compressed_size_mb/99)*100:.1f}% más pequeño
   • Ubicación S3: s3://{S3_BUCKET_NAME}/index.json.gz
   
🚀 Siguiente paso: Actualizar la función Lambda con el código optimizado
""")

if __name__ == "__main__":
    create_optimized_index()