FROM python:3.12-slim

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    tmux \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Install CLI tools (claude-code / opencode via npm)
# ---------------------------------------------------------------------------
RUN npm install -g @anthropic-ai/claude-code opencode 2>/dev/null || true

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ---------------------------------------------------------------------------
# Application files
# ---------------------------------------------------------------------------
COPY app.py /app/app.py
COPY ui.html /app/ui.html

WORKDIR /app

# Working directory for cloned repos
RUN mkdir -p /workspace

EXPOSE 8000

CMD ["python", "app.py"]
