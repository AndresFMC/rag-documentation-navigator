#!/bin/bash
set -e

echo "🚀 Iniciando despliegue de CÓDIGO para RAG Documentation Navigator..."

# --- Variables ---
FUNCTION_NAME="RAG-Documentation-Navigator-Function"
REGION="eu-central-1"
ZIP_FILE="deployment_package.zip"
SOURCE_DIR="lambda_function"
S3_BUCKET="andres-rdn-uploads"

# --- Lógica ---
echo "🧹 Limpiando ZIP anterior..."
rm -f $ZIP_FILE

echo "🗜️  Creando archivo ZIP solo con el código fuente..."
cd $SOURCE_DIR

zip -r ../$ZIP_FILE . -q
cd ..
ZIP_SIZE=$(du -h $ZIP_FILE | cut -f1)
echo "✅ ZIP de código creado: $ZIP_FILE ($ZIP_SIZE)"

echo "☁️ Subiendo ZIP a S3..."
aws s3 cp $ZIP_FILE s3://$S3_BUCKET/lambda-packages/ --region $REGION
echo "✅ ZIP subido a S3."

echo "🔄 Actualizando función Lambda..."
aws lambda update-function-code \
    --function-name $FUNCTION_NAME \
    --s3-bucket $S3_BUCKET \
    --s3-key "lambda-packages/$ZIP_FILE" \
    --region $REGION \
    --no-cli-pager
echo "✅ Función Lambda actualizada."

rm $ZIP_FILE
echo ""
echo "🎉 ¡Despliegue de CÓDIGO completado!"