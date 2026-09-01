# Stdlib-only app: the base image is the entire dependency story.
FROM python:3.12-slim

WORKDIR /app
COPY agt/ agt/
COPY web/ web/

EXPOSE 8000
# 0.0.0.0 is correct inside the container — the compose port mapping decides
# who can actually reach it (see agt/serve.py for why the default is loopback).
CMD ["python3", "-m", "agt.serve", "--host", "0.0.0.0", "--port", "8000"]
