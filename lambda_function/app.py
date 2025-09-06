import os
import sys
import json
import boto3

# --- LangChain Optimization for Lambda Environment ---
# Add bundled libraries to Python's path
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from langchain_aws import BedrockEmbeddings, BedrockChat
from langchain_community.vectorstores import FAISS
from langchain.prompts import PromptTemplate
from langchain.chains import RetrievalQA

# --- Initialization of Clients and Global Variables ---
# Initialized OUTSIDE the handler for reuse in "warm" Lambda invocations.

S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
INDEX_PATH_IN_S3 = "index.faiss"
PKL_PATH_IN_S3 = "index.pkl"

s3_client = boto3.client("s3")
bedrock_client = boto3.client(service_name='bedrock-runtime', region_name='eu-central-1')

# Temporary path within the Lambda execution environment
TMP_INDEX_PATH = "/tmp/index.faiss"
TMP_PKL_PATH = "/tmp/index.pkl"

# --- Bedrock Models ---
# Embeddings Model
embeddings_model = BedrockEmbeddings(
    client=bedrock_client,
    model_id="amazon.titan-embed-text-v1"
)

# Language Model (LLM) for response generation
llm = BedrockChat(
    client=bedrock_client,
    model_id="anthropic.claude-3-sonnet-20240229-v1:0",
    model_kwargs={"temperature": 0.2, "max_tokens_to_sample": 2048}
)

def load_index_from_s3():
    """
    Downloads the FAISS index files from S3 to the /tmp/ directory if they don't already exist.
    This function only runs during a "cold start".
    """
    if not os.path.exists(TMP_INDEX_PATH):
        print("Downloading index.faiss from S3...")
        s3_client.download_file(S3_BUCKET_NAME, INDEX_PATH_IN_S3, TMP_INDEX_PATH)
        print("Download of index.faiss complete.")
    
    if not os.path.exists(TMP_PKL_PATH):
        print("Downloading index.pkl from S3...")
        s3_client.download_file(S3_BUCKET_NAME, PKL_PATH_IN_S3, TMP_PKL_PATH)
        print("Download of index.pkl complete.")

def lambda_handler(event, context):
    """
    Main entry point for the Lambda function.
    Orchestrates the RAG logic.
    """
    print(f"Received event: {event}")

    # --- 1. Load the vector index ---
    try:
        load_index_from_s3()
        print("Loading FAISS index from /tmp into memory...")
        vectorstore = FAISS.load_local(
            folder_path="/tmp",
            embeddings=embeddings_model,
            allow_dangerous_deserialization=True # Required to load the .pkl file
        )
        print("Index loaded successfully.")
    except Exception as e:
        print(f"Error loading index: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Could not load the index from S3. {str(e)}"})
        }

    # --- 2. Extract the user's question ---
    try:
        # API Gateway body is a string, needs to be parsed
        body = json.loads(event.get("body", "{}"))
        user_question = body.get("question")
        
        if not user_question:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Parameter 'question' not found in the request body."})
            }
    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Request body is not a valid JSON."})
        }

    # --- 3. Build and run the RAG chain ---
    prompt_template = """
    Use the following context to answer the question as accurately and concisely as possible.
    If you don't know the answer or the context doesn't contain the information, say "The documentation does not contain information on this topic.".
    Do not make up answers.

    Context:
    {context}

    Question: {question}

    Answer:
    """

    PROMPT = PromptTemplate(
        template=prompt_template, input_variables=["context", "question"]
    )

    # Create the RetrievalQA chain
    qa_chain = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=vectorstore.as_retriever(),
        return_source_documents=True,
        chain_type_kwargs={"prompt": PROMPT}
    )

    print(f"Running RAG chain with question: {user_question}")
    result = qa_chain({"query": user_question})
    
    answer = result.get("result")
    source_documents = result.get("source_documents", [])
    
    # Extract the source document names
    sources = list(set([doc.metadata.get("source", "N/A") for doc in source_documents]))

    print(f"Generated answer: {answer}")
    print(f"Sources: {sources}")

    # --- 4. Return the response ---
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*" # Allows the API to be called from any website
        },
        "body": json.dumps({
            "answer": answer,
            "sources": sources
        })
    }