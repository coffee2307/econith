ARG sourceimage=econith/econith-quant
ARG sourcetag=develop
FROM ${sourceimage}:${sourcetag}

# Install dependencies
COPY requirements-plot.txt /econith/

RUN pip install -r requirements-plot.txt --user --no-cache-dir
