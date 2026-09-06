FROM python:3.14.6-slim-trixie AS base

# Setup env
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONFAULTHANDLER=1
ENV PATH=/home/econithuser/.local/bin:$PATH
ENV ECONITH_APP_ENV="docker"

# Prepare environment
RUN mkdir /econith \
  && apt-get update \
  && apt-get -y install --no-install-recommends sudo libatlas3-base curl sqlite3 libgomp1 \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* \
  && useradd -u 1000 -G sudo -U -m -s /bin/bash econithuser \
  && chown econithuser:econithuser /econith \
  # Allow sudoers
  && echo "econithuser ALL=(ALL) NOPASSWD: /bin/chown" >> /etc/sudoers

WORKDIR /econith

# Install dependencies
FROM base AS python-deps
RUN  apt-get update \
  && apt-get -y install --no-install-recommends build-essential libssl-dev git libffi-dev libgfortran5 pkg-config cmake gcc \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* \
  && pip install --upgrade pip wheel

# Install dependencies
COPY --chown=econithuser:econithuser requirements.txt requirements-hyperopt.txt /econith/
USER econithuser
RUN  pip install --user --no-cache-dir "numpy<3.0" \
  && pip install --user --no-cache-dir -r requirements-hyperopt.txt

# Copy dependencies to runtime-image
FROM base AS runtime-image

COPY --from=python-deps --chown=econithuser:econithuser /home/econithuser/.local /home/econithuser/.local

USER econithuser
# Install and execute
COPY --chown=econithuser:econithuser . /econith/

RUN pip install -e . --user --no-cache-dir \
  && mkdir /econith/user_data/ \
  && econith install-ui

ENTRYPOINT ["econith"]
# Default to trade mode
CMD [ "trade" ]
