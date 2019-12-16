FROM python:3.8-alpine

WORKDIR /app
CMD ["python", "main.py"]

ADD requirements.txt .
RUN pip3 install -r requirements.txt

ADD . .
