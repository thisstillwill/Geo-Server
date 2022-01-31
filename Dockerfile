# syntax=docker/dockerfile:1
FROM python:3.10
WORKDIR /src
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
EXPOSE 6379
COPY . .
CMD ["uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "6379"]
