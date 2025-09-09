#!/bin/bash
aws s3 cp frontend/index.html s3://andres-rag-document-navigator-demo/ --region eu-central-1
echo "Frontend actualizado"