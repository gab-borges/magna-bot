FROM python:3.11-slim

# Install system dependencies including LaTeX
RUN apt-get update && apt-get install -y \
    ffmpeg \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-latex-recommended \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-science \
    texlive-pictures \
    dvipng \
    cm-super \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

COPY cookies.txt .

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the rest of the application
COPY . .

CMD ["python", "main.py"]