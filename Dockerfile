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
# Install claude-code via official installer (npm install is deprecated)
# See: https://docs.anthropic.com/en/docs/claude-code/setup
# ---------------------------------------------------------------------------
RUN curl -fsSL https://claude.ai/install.sh | bash

# Ensure the installer's default bin directory is on PATH for subsequent
# RUN commands and for the final container runtime.
ENV PATH="/root/.local/bin:${PATH}"

# ---------------------------------------------------------------------------
# Install opencode via official npm package (package name: opencode-ai)
# See: https://www.npmjs.com/package/opencode-ai
# ---------------------------------------------------------------------------
RUN npm install -g opencode-ai

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
