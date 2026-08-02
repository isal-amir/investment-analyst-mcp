#!/bin/bash

# GCP Multi-Agent MCP Portfolio Deployment Script
# This script automates building the container, pushing to Artifact Registry,
# and deploying to GCP Cloud Run with Vertex AI integration.

set -e

# ==========================================
# CONFIGURATION - CHANGE THESE TO MATCH YOURS
# ==========================================
PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1" # Recommended for Vertex AI availability
SERVICE_NAME="portfolio-risk-analyzer"
REPOSITORY_NAME="agentic-mcp-repo"
SERVICE_ACCOUNT_NAME="agentic-portfolio-sa"
# ==========================================

if [ -z "$PROJECT_ID" ]; then
    echo "Error: No active GCP Project ID found. Please run 'gcloud config set project [YOUR-PROJECT-ID]' first."
    exit 1
fi

echo "=========================================================="
echo "Starting deployment for service: $SERVICE_NAME"
echo "Target GCP Project: $PROJECT_ID"
echo "Target Region: $REGION"
echo "=========================================================="

# 1. Enable Required GCP APIs
echo "Step 1: Enabling necessary Google Cloud APIs..."
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com \
    --project="$PROJECT_ID"

# 2. Create GCP Service Account if not exists
SA_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
echo "Step 2: Checking Service Account ($SA_EMAIL)..."
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Creating new Service Account..."
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
        --description="Service account for agentic portfolio application to access Vertex AI" \
        --display-name="Agentic Portfolio Service Account" \
        --project="$PROJECT_ID"
else
    echo "Service Account already exists."
fi

# 3. Grant IAM Permissions for Vertex AI Access
echo "Step 3: Binding Vertex AI User role to Service Account..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/aiplatform.user"

# 4. Create Artifact Registry Repository if not exists
REPO_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY_NAME}"
echo "Step 4: Checking Artifact Registry Repository..."
if ! gcloud artifacts repositories describe "$REPOSITORY_NAME" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Creating Docker repository in Artifact Registry..."
    gcloud artifacts repositories create "$REPOSITORY_NAME" \
        --repository-format=docker \
        --location="$REGION" \
        --description="Docker repository for multi-agent portfolio project" \
        --project="$PROJECT_ID"
else
    echo "Repository already exists."
fi

# 5. Build and Push Container using Google Cloud Build
IMAGE_TAG="${REPO_URI}/${SERVICE_NAME}:latest"
echo "Step 5: Building container image via Cloud Build ($IMAGE_TAG)..."
gcloud builds submit --tag "$IMAGE_TAG" --project="$PROJECT_ID"

# 6. Deploy to GCP Cloud Run
echo "Step 6: Deploying to GCP Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
    --image="$IMAGE_TAG" \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --set-env-vars="USE_VERTEX_AI=true,PORT=8000" \
    --allow-unauthenticated \
    --project="$PROJECT_ID" \
    --port=8000

# Retrieve Service URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format='value(status.url)' --project="$PROJECT_ID")

echo "=========================================================="
echo "DEPLOYMENT COMPLETED SUCCESSFULLY!"
echo "Your live portfolio application is available at:"
echo "👉 $SERVICE_URL"
echo "=========================================================="
