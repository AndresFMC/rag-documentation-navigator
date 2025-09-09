import os
import json
import gzip
import boto3
from typing import List, Dict, Any
import logging

# LangSmith imports
from langsmith import Client
from langchain.callbacks.tracers.langchain import LangChainTracer

# Configurar logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Clientes de AWS (inicializados fuera del handler para reutilización)
s3_client = boto3.client("s3", region_name='eu-central-1')
bedrock_client = boto3.client(service_name='bedrock-runtime', region_name='eu-central-1')
cloudwatch = boto3.client('cloudwatch', region_name='eu-central-1')

# Variables de entorno
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "andres-rdn-index-storage")
INDEX_KEY = "index.json.gz"

# Configuración de LangSmith (opcional)
langsmith_api_key = os.environ.get('LANGSMITH_API_KEY')
if langsmith_api_key:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = "rag-documentation-navigator"
    os.environ["LANGCHAIN_ENDPOINT"] = "https://eu.api.smith.langchain.com"
    langsmith_enabled = True
    try:
        langsmith_client = Client()
        logger.info("✅ LangSmith configured successfully")
    except Exception as e:
        langsmith_enabled = False
        logger.warning(f"⚠️ Error configuring LangSmith: {e}")
else:
    langsmith_enabled = False
    logger.info("ℹ️ LangSmith not configured - functioning normally")

# Cache global para el índice (persiste entre invocaciones warm)
INDEX_CACHE = None

def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Calcula la similitud coseno entre dos vectores.
    Implementación pura en Python para evitar dependencia de NumPy.
    """
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    
    if norm1 == 0 or norm2 == 0:
        return 0.0
    
    return dot_product / (norm1 * norm2)

def load_index() -> Dict[str, Any]:
    """
    Carga el índice desde S3 con sistema de cache.
    Solo descarga en cold starts, reutiliza en warm starts.
    """
    global INDEX_CACHE
    
    if INDEX_CACHE is not None:
        logger.info("✅ Using index from cache (warm start)")
        return INDEX_CACHE
    
    try:
        logger.info(f"📥 Downloading index from s3://{S3_BUCKET_NAME}/{INDEX_KEY}")
        
        # Descargar desde S3
        response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=INDEX_KEY)
        compressed_data = response['Body'].read()
        
        # Descomprimir
        logger.info("📦 Decompressing index...")
        json_data = gzip.decompress(compressed_data).decode('utf-8')
        
        # Parsear JSON
        INDEX_CACHE = json.loads(json_data)
        
        logger.info(f"✅ Index loaded: {INDEX_CACHE['metadata']['total_chunks']} chunks")
        return INDEX_CACHE
        
    except Exception as e:
        logger.error(f"❌ Error loading index: {str(e)}")
        raise

def search_similar_chunks(query: str, index: Dict, top_k: int = 5) -> List[Dict]:
    """
    Busca los chunks más similares a la consulta usando similitud coseno.
    """
    logger.info(f"🔍 Searching chunks for: '{query[:50]}...'")
    
    try:
        # Generar embedding de la consulta
        response = bedrock_client.invoke_model(
            modelId="amazon.titan-embed-text-v1",
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": query})
        )
        
        response_body = json.loads(response['body'].read())
        query_embedding = response_body.get('embedding', [])
        
        if not query_embedding:
            logger.error("Could not generate embedding for query")
            return []
        
        # Calcular similitudes con todos los chunks
        similarities = []
        for chunk in index["chunks"]:
            if "embedding" in chunk and chunk["embedding"]:
                sim = cosine_similarity(query_embedding, chunk["embedding"])
                similarities.append({
                    "chunk": chunk,
                    "similarity": sim
                })
        
        # Ordenar por similitud y tomar los top_k
        similarities.sort(key=lambda x: x["similarity"], reverse=True)
        top_chunks = similarities[:top_k]
        
        logger.info(f"✅ Found {len(top_chunks)} relevant chunks")
        
        # Devolver solo los chunks (sin scores)
        return [item["chunk"] for item in top_chunks]
        
    except Exception as e:
        logger.error(f"Error in search: {str(e)}")
        return []

def generate_answer_with_tracking(question: str, context_chunks: List[Dict], request_id: str) -> tuple:
    """
    Genera una respuesta usando Claude 3 Sonnet con el contexto recuperado.
    Incluye tracking con LangSmith si está configurado.
    """
    import time
    start_time = time.time()
    
    try:
        # Construir el contexto a partir de los chunks
        if not context_chunks:
            return "No relevant information found in the documentation.", [], {}
        
        context_parts = []
        sources = set()
        
        for i, chunk in enumerate(context_chunks, 1):
            context_parts.append(f"[Fragment {i}]:\n{chunk.get('text', '')}\n")
            if 'metadata' in chunk and 'source' in chunk['metadata']:
                sources.add(chunk['metadata']['source'])
        
        context = "\n".join(context_parts)
        
        # Construir el prompt EN INGLÉS
        prompt = f"""You are an expert assistant that answers questions based ONLY on the provided context.

Relevant context:
{context}

User question: {question}

Instructions:
1. Answer ONLY with information from the provided context
2. If the context doesn't contain the information, clearly state that it's not available in the documentation
3. Be concise but complete
4. Do not make up information
5. Respond in English

Answer:"""

        # Configurar callbacks para LangSmith si está disponible
        callbacks = []
        if langsmith_enabled:
            try:
                tracer = LangChainTracer(
                    project_name="rag-documentation-navigator",
                    client=langsmith_client
                )
                callbacks = [tracer]
                logger.info("🔍 LangSmith tracking active")
            except Exception as e:
                logger.warning(f"⚠️ Error configuring LangSmith tracer: {e}")
        
        # Usar LangChain para mejor tracking
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage
        
        llm = ChatBedrock(
            model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            model_kwargs={"temperature": 0.1, "max_tokens": 2048},
            callbacks=callbacks
        )
        
        logger.info("🤖 Generating response with Claude 3 Sonnet...")
        
        # Invocar con metadata para LangSmith
        message = HumanMessage(content=prompt)
        response = llm.invoke(
            [message],
            config={
                "metadata": {
                    "request_id": request_id,
                    "question_length": len(question),
                    "context_chunks": len(context_chunks),
                    "use_case": "rag_query"
                },
                "tags": ["production", "rag", "documentation-navigator"]
            }
        )
        
        processing_time = time.time() - start_time
        
        # Calcular métricas de tokens
        input_tokens = estimate_tokens(prompt)
        output_tokens = estimate_tokens(response.content)
        
        metrics = {
            "processing_time": processing_time,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "langsmith_tracking": langsmith_enabled
        }
        
        if langsmith_enabled:
            logger.info(f"📊 LangSmith: {input_tokens} tokens in, {output_tokens} tokens out")
        
        logger.info("✅ Response generated successfully")
        return response.content, list(sources), metrics
        
    except Exception as e:
        logger.error(f"Error generating response: {str(e)}")
        return f"Error generating response: {str(e)}", [], {}

def estimate_tokens(text: str) -> int:
    """Estima tokens de forma más precisa. 1 palabra ≈ 1.3 tokens para Claude."""
    return int(len(text.split()) * 1.3)

def send_metrics_to_cloudwatch(metrics: Dict, request_id: str):
    """Envía métricas a CloudWatch para monitoreo."""
    try:
        cloudwatch.put_metric_data(
            Namespace='RAGDocumentNavigator',
            MetricData=[
                {
                    'MetricName': 'ResponseTime',
                    'Value': metrics.get('processing_time', 0),
                    'Unit': 'Seconds',
                    'Dimensions': [{'Name': 'RequestId', 'Value': request_id}]
                },
                {
                    'MetricName': 'InputTokens',
                    'Value': metrics.get('input_tokens', 0),
                    'Unit': 'Count'
                },
                {
                    'MetricName': 'OutputTokens',
                    'Value': metrics.get('output_tokens', 0),
                    'Unit': 'Count'
                }
            ]
        )
        logger.info(f"📊 Metrics sent to CloudWatch")
    except Exception as e:
        logger.warning(f"Error sending metrics: {e}")

def lambda_handler(event, context):
    """
    Handler principal de Lambda.
    Procesa las consultas y devuelve respuestas basadas en RAG.
    """
    request_id = context.aws_request_id
    logger.info(f"📨 Event received: {json.dumps(event)[:500]}... [Request ID: {request_id}]")

    
    # Headers CORS para permitir llamadas desde el frontend
    cors_headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST, OPTIONS"
    }


    # Validar API key
    headers = event.get('headers', {})
    provided_api_key = headers.get('x-api-key') or headers.get('X-Api-Key')
    valid_api_key = os.environ.get('VALID_API_KEY')
    
    if not provided_api_key or provided_api_key != valid_api_key:
        logger.warning(f"Invalid API key provided: {provided_api_key}")
        return {
            "statusCode": 401,
            "headers": cors_headers,
            "body": json.dumps({
                "error": "Unauthorized",
                "message": "Valid API key required. Contact @andres-fmc for access."
            })
        }


    
    # Manejar preflight CORS
    if event.get('httpMethod') == 'OPTIONS':
        return {
            "statusCode": 200,
            "headers": cors_headers,
            "body": ""
        }
    
    try:
        # 1. Extraer y validar la pregunta
        body = json.loads(event.get("body", "{}"))
        question = body.get("question", "").strip()
        
        if not question:
            return {
                "statusCode": 400,
                "headers": cors_headers,
                "body": json.dumps({
                    "error": "Missing 'question' parameter",
                    "message": "Please provide a question in the request body"
                })
            }
        
        logger.info(f"❓ Question received: {question}")
        
        # 2. Cargar el índice
        index = load_index()
        
        # 3. Buscar chunks similares
        relevant_chunks = search_similar_chunks(question, index, top_k=5)
        
        if not relevant_chunks:
            return {
                "statusCode": 200,
                "headers": cors_headers,
                "body": json.dumps({
                    "answer": "No relevant information found in the documentation for your question.",
                    "sources": [],
                    "chunks_used": 0,
                    "model_used": "Claude 3 Sonnet"
                })
            }
        
        # 4. Generar respuesta con el LLM y tracking
        answer, sources, metrics = generate_answer_with_tracking(question, relevant_chunks, request_id)
        
        # 5. Enviar métricas a CloudWatch
        send_metrics_to_cloudwatch(metrics, request_id)
        
        # 6. Preparar respuesta
        response_data = {
            "answer": answer,
            "sources": sources,
            "chunks_used": len(relevant_chunks),
            "model_used": "Claude 3 Sonnet",
            "metrics": {
                "response_time": round(metrics.get('processing_time', 0), 2),
                "tokens": {
                    "input": metrics.get('input_tokens', 0),
                    "output": metrics.get('output_tokens', 0)
                },
                "langsmith_tracking": metrics.get('langsmith_tracking', False)
            }
        }
        
        logger.info(f"✅ Response prepared with {len(sources)} sources [Request ID: {request_id}]")
        
        return {
            "statusCode": 200,
            "headers": cors_headers,
            "body": json.dumps(response_data)
        }
        
    except json.JSONDecodeError:
        logger.error("Error decoding JSON from body")
        return {
            "statusCode": 400,
            "headers": cors_headers,
            "body": json.dumps({
                "error": "Invalid JSON",
                "message": "The request body must be valid JSON"
            })
        }
        
    except Exception as e:
        logger.error(f"❌ General error: {str(e)}")
        
        # Log del traceback completo para debugging
        import traceback
        logger.error(traceback.format_exc())
        
        return {
            "statusCode": 500,
            "headers": cors_headers,
            "body": json.dumps({
                "error": "Internal server error",
                "message": "An error occurred processing your request",
                "details": str(e)
            })
        }