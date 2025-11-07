#! /usr/bin/env bash

echo "Checking for linting errors..."
uvx ruff check .

echo "Checking for type errors..."
uvx mypy .

echo "Running formatter..."
uvx ruff format .

