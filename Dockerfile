FROM python:3.12-slim

# --- Install system dependencies ---
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    ca-certificates \
    git \
    jq \
    # Docker CLI dependencies
    gnupg \
    lsb-release

# --- Install Docker CLI ---
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-24.0.7.tgz | tar xz -C /usr/local/bin --strip-components=1 docker/docker

# --- Create app directories ---
RUN mkdir -p /app /app/data /app/log /app/config

# --- Set working directory ---
WORKDIR /app

# --- Copy your Pipfile and lock ---
COPY Pipfile Pipfile.lock ./

# --- Install pipenv and dependencies ---
RUN pip install --upgrade pip && pip install pipenv && pipenv install --deploy --system

# --- Copy your application code ---
COPY autotune ./autotune
COPY drivers ./drivers
COPY scripts/* .

RUN rm autotune/params.toml
