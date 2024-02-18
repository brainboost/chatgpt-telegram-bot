FROM public.ecr.aws/lambda/python:3.11
RUN yum update -y
RUN yum install git -y
RUN mkdir lambda
RUN mkdir engines
COPY lambda/requirements.txt lambda
COPY engines/requirements.txt engines
COPY lambda/*.py lambda
COPY engines/*.py engines
RUN pip install pip --upgrade
# RUN pip install uv
# RUN uv venv
# RUN source .venv/bin/activate
RUN pip install -r engines/requirements.txt
RUN pip install -r lambda/requirements.txt