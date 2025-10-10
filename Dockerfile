FROM public.ecr.aws/lambda/python:3.13

RUN mkdir lambda
RUN mkdir engines

# Install uv for faster Python package management
RUN pip install uv

# Copy Python files
COPY lambda/*.py lambda
COPY engines/*.py engines

# Copy uv configuration files
COPY pyproject.toml .
COPY uv.lock .

# Install only lambda-specific dependencies
RUN uv sync --frozen --group lambda --no-dev
