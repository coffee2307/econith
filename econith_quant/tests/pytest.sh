#!/bin/bash

echo "Running Unit tests"

pytest --random-order --cov=econith --cov-config=.coveragerc tests/
