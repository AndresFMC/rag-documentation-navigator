#!/bin/bash
set -e

echo "🔧 Creando layer completo con TODAS las dependencias..."

# Limpiar directorio anterior
rm -rf complete_layer_package
mkdir -p complete_layer_package/python

# Crear requirements completo con todas las dependencias necesarias
cat > complete_layer_requirements.txt << EOF
langchain==0.3.14
langchain-aws==0.2.10
langsmith==0.2.4
langchain-core==0.3.63
pydantic>=2.0.0,<3.0.0
requests>=2.31.0
requests-toolbelt>=1.0.0
urllib3>=1.26.0,<3.0.0
certifi>=2023.0.0
charset-normalizer>=3.0.0
idna>=3.0.0
boto3>=1.28.0
botocore>=1.31.0
jmespath>=1.0.0
python-dateutil>=2.8.0
s3transfer>=0.6.0
six>=1.16.0
PyYAML>=6.0.0
jsonpatch>=1.33
jsonpointer>=2.4
packaging>=23.0
tenacity>=8.2.0
typing-extensions>=4.5.0
annotated-types>=0.4.0
pydantic-core>=2.18.0
orjson>=3.9.0
# LangSmith específicas
httpx>=0.24.0,<1
h11>=0.14.0
httpcore>=0.17.0
anyio>=3.6.0
sniffio>=1.3.0
certifi>=2023.0.0
EOF

echo "📦 Instalando dependencias completas en el layer..."
pip install -r complete_layer_requirements.txt -t complete_layer_package/python/ --force-reinstall

# Verificar que las dependencias críticas estén presentes
echo "🔍 Verificando dependencias críticas..."
if [ -d "complete_layer_package/python/requests_toolbelt" ]; then
    echo "✅ requests_toolbelt encontrado"
else
    echo "❌ requests_toolbelt NO encontrado"
fi

if [ -d "complete_layer_package/python/langchain" ]; then
    echo "✅ langchain encontrado"
else
    echo "❌ langchain NO encontrado"
fi

if [ -d "complete_layer_package/python/pydantic" ]; then
    echo "✅ pydantic encontrado"
else
    echo "❌ pydantic NO encontrado"
fi

echo "🗜️ Comprimiendo layer completo..."
cd complete_layer_package
zip -r ../complete_layer.zip . -q
cd ..

echo "📊 Tamaño del layer completo:"
du -h complete_layer.zip

echo "☁️ Publicando nueva versión del layer..."
LAYER_RESPONSE=$(aws lambda publish-layer-version \
    --layer-name rdn-dependencies-layer \
    --zip-file fileb://complete_layer.zip \
    --compatible-runtimes python3.12 \
    --region eu-central-1 \
    --description "Complete dependencies for RAG Documentation Navigator - All deps included" \
    --no-cli-pager)

echo "✅ Layer completo creado."

# Extraer el ARN del nuevo layer
NEW_LAYER_ARN=$(echo $LAYER_RESPONSE | python3 -c "import sys, json; print(json.load(sys.stdin)['LayerVersionArn'])")
echo "🔗 Nuevo Layer ARN: $NEW_LAYER_ARN"

echo "🔄 Actualizando función Lambda con el nuevo layer..."
aws lambda update-function-configuration \
    --function-name RAG-Documentation-Navigator-Function \
    --layers $NEW_LAYER_ARN \
    --region eu-central-1 \
    --no-cli-pager > /dev/null

echo "✅ Función actualizada con el layer completo."

# Limpiar archivos temporales
rm -rf complete_layer_package complete_layer.zip complete_layer_requirements.txt

echo ""
echo "🎉 ¡Layer completo creado y aplicado exitosamente!"
echo "🧪 Esperando 10 segundos antes de probar..."
sleep 10

echo "🔍 Probando la función..."
curl -X POST https://q7i1i1g84c.execute-api.eu-central-1.amazonaws.com/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is cost optimization in AWS?"}' \
  | python -m json.tool

echo ""
echo "✅ ¡Proceso completado!"