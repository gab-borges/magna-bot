FROM python:3.11-slim

# Install LaTeX and required packages
RUN apt-get update && apt-get install -y \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-science \
    dvipng \
    cm-super \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Install Python dependencies
RUN pip install -r requirements.txt

CMD ["python", "main.py"]