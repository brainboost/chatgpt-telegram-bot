FROM public.ecr.aws/lambda/python:3.13
RUN pip install uv
RUN mkdir -p /var/task/lambda /var/task/engines
COPY lambda/pyproject.toml lambda/uv.lock /var/task/lambda/
COPY engines/pyproject.toml engines/uv.lock /var/task/engines/
WORKDIR /var/task/lambda
RUN uv pip install --system --no-cache -r <(uv pip compile pyproject.toml)
WORKDIR /var/task/engines
RUN uv pip install --system --no-cache -r <(uv pip compile pyproject.toml)
COPY lambda/*.py /var/task/lambda/
COPY engines/*.py /var/task/engines/
WORKDIR /var/task
RUN uv cache clean