FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 ca-certificates tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV HOME=/tmp HF_HOME=/tmp/huggingface XDG_CACHE_HOME=/tmp/.cache

COPY installed-requirements.txt ./
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r installed-requirements.txt
ARG INSTALL_CUDA=0
RUN if [ "$INSTALL_CUDA" = 1 ]; then python -m pip install --no-cache-dir 'nvidia-cublas-cu12>=12,<13' 'nvidia-cudnn-cu12>=9,<10'; fi
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib

COPY *.py ./
COPY templates ./templates
COPY static ./static
COPY vendor0184 ./vendor0184
COPY installer-entrypoint.py /usr/local/bin/installer-entrypoint.py

USER 10001:10001

EXPOSE 8092
ENTRYPOINT ["python", "/usr/local/bin/installer-entrypoint.py"]
CMD ["gunicorn", "--bind", "0.0.0.0:8092", "--workers", "2", "app:create_app()"]
