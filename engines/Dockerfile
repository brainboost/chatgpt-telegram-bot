FROM public.ecr.aws/lambda/python:3.9
COPY requirements.txt ./
RUN python3.9 -m pip install --upgrade pip
RUN python3.9 -m pip install -r requirements.txt
COPY *.py ./
CMD ["bing_gpt.sqs_handler"]