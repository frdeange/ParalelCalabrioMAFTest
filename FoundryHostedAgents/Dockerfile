# Foundry Hosted Agent container for the WFM Data Assistant (main_v9.py).
#
# The hosting platform listens on port 8088 by default. Foundry injects
# FOUNDRY_PROJECT_ENDPOINT, AZURE_AI_MODEL_DEPLOYMENT_NAME and
# APPLICATIONINSIGHTS_CONNECTION_STRING at runtime. All other env vars
# (BU_ID, MCP_SERVER_URL, FOUNDRY_MODEL) come from agent.yaml.

FROM python:3.13-slim

WORKDIR /app

COPY . user_agent/
WORKDIR /app/user_agent

RUN if [ -f requirements.txt ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    else \
        echo "No requirements.txt found"; \
    fi

EXPOSE 8088

CMD ["python", "main_v9.py"]
