FROM python:3.13
WORKDIR /app

COPY python/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG PYDEBUGGER=0

RUN if [ "$PYDEBUGGER" = "1" ]; then \
        pip install debugpy; \
    fi

COPY python/src .

CMD [ "python", "./main.py" ]